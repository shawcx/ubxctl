import random
import struct
import threading
import time
from types import SimpleNamespace

import pytest
import serial

from ubxctl import device, ubx

RIGHT_BAUD = 38400


def nmea(body: str) -> bytes:
    cs = 0
    for c in body.encode():
        cs ^= c
    return f"${body}*{cs:02X}\r\n".encode()


NMEA = nmea("GNRMC,000000.00,V,,,,,,,261026,,,N,V")


def test_fake_nmea_is_valid():
    assert ubx.nmea_ok(NMEA.decode().strip())


def mon_ver_reply() -> bytes:
    payload = b"EXT SPGL1L5 6.00 (041e8a)".ljust(30, b"\0") + b"000A0000".ljust(10, b"\0")
    for ext in (b"FWVER=SPGL1L5 6.00", b"PROTVER=40.00", b"MOD=NEO-F10N"):
        payload += ext.ljust(30, b"\0")
    return ubx.frame(ubx.CLS_MON, 0x04, payload)


class FakeReceiver:
    """Stands in for serial.Serial: a receiver at RIGHT_BAUD that streams NMEA and answers UBX.

    At any other rate it produces noise and ignores input, like a real mismatched UART.
    """

    def __init__(self, port, baudrate, timeout=None, *, talks=True, answers=True, drop=0,
                 config=None):
        self.port, self.baudrate = port, baudrate
        self.talks, self.answers, self.drop = talks, answers, drop
        self.config = config or {}  # layer -> {key: raw}
        self.writes: list[bytes] = []
        self._out = bytearray()
        self._lock = threading.Lock()
        self._framer = ubx.Framer()
        self._rng = random.Random(baudrate)
        self._next_nmea = 0.0

    def read(self, n):
        time.sleep(0.005)
        with self._lock:
            if self.talks and time.monotonic() >= self._next_nmea:
                self._next_nmea = time.monotonic() + 0.05
                self._out += NMEA if self.baudrate == RIGHT_BAUD else self._rng.randbytes(len(NMEA))
            data = bytes(self._out[:n])
            del self._out[:n]
        return data

    def write(self, data):
        self.writes.append(bytes(data))
        if self.baudrate != RIGHT_BAUD or not self.answers:
            return len(data)
        for kind, f in self._framer.feed(data):
            if kind == "UBX":
                self._reply(f)
        return len(data)

    def _reply(self, f: bytes) -> None:
        cls, mid, payload = f[2], f[3], f[6:-2]
        if (cls, mid) == (ubx.CLS_MON, 0x04):
            if self.drop:
                self.drop -= 1  # a saturated UART loses the reply
                return
            out = mon_ver_reply()
        elif (cls, mid) == (ubx.CLS_CFG, ubx.ID_VALGET):
            out = self._valget(payload)
        else:
            out = ubx.frame(ubx.CLS_ACK, 0x01, bytes((cls, mid)))
        with self._lock:
            self._out += out

    def _valget(self, payload: bytes) -> bytes:
        _, layer, pos = struct.unpack_from("<BBH", payload)
        stored = self.config.get(layer, {})
        if not stored:
            return ubx.frame(ubx.CLS_ACK, 0x00, bytes((ubx.CLS_CFG, ubx.ID_VALGET)))
        page = list(stored.items())[pos:pos + ubx.MAX_KEYS_PER_MSG]
        body = b"".join(struct.pack("<I", k) + v for k, v in page)
        return ubx.frame(ubx.CLS_CFG, ubx.ID_VALGET, struct.pack("<BBH", 1, layer, pos) + body)

    def reset_input_buffer(self):
        with self._lock:
            self._out.clear()

    def close(self):
        pass


@pytest.fixture
def fake_serial(monkeypatch):
    """Patch serial.Serial in the device module; returns a setter for the fake's behaviour."""
    opts: dict = {}
    made: list[FakeReceiver] = []

    def factory(port, baudrate, timeout=None):
        if opts.get("missing"):
            raise serial.SerialException(f"could not open port {port}")
        r = FakeReceiver(port, baudrate, timeout, **{k: v for k, v in opts.items() if k != "missing"})
        made.append(r)
        return r

    monkeypatch.setattr(device.serial, "Serial", factory)
    monkeypatch.setattr(device, "SATURATED_RETRIES", 2)
    return SimpleNamespace(set=opts.update, made=made)


# --- detect_baud ----------------------------------------------------------------


def test_detect_baud_finds_the_answering_rate(fake_serial):
    assert device.detect_baud("/dev/fake", (115200, RIGHT_BAUD)) == RIGHT_BAUD


def test_detect_baud_retries_a_saturated_receiver(fake_serial):
    # The first 0.6 s probe (two polls) and one retry are lost, as on a UART full of NMEA.
    fake_serial.set(drop=3)
    assert device.detect_baud("/dev/fake", (RIGHT_BAUD, 9600)) == RIGHT_BAUD
    assert [r.baudrate for r in fake_serial.made] == [RIGHT_BAUD]  # never moved on to 9600


def test_detect_baud_rejects_an_nmea_only_device(fake_serial):
    fake_serial.set(answers=False)
    with pytest.raises(device.NoUbxReply, match=f"valid NMEA at {RIGHT_BAUD} baud"):
        device.detect_baud("/dev/fake", (115200, RIGHT_BAUD))


def test_detect_baud_gives_up_on_a_silent_port(fake_serial):
    fake_serial.set(talks=False, answers=False)
    assert device.detect_baud("/dev/fake", (9600, RIGHT_BAUD)) is None


def test_detect_baud_missing_port(fake_serial):
    fake_serial.set(missing=True)
    assert device.detect_baud("/dev/fake", (RIGHT_BAUD,)) is None


# --- Device ---------------------------------------------------------------------


def make_items(n: int) -> dict[int, bytes]:
    return {0x30FE0000 + i: struct.pack("<H", i) for i in range(n)}


@pytest.fixture
def dev(fake_serial):
    d = device.Device("/dev/fake", RIGHT_BAUD)
    yield d
    d.close()


def test_mon_ver(dev):
    dev.open()
    info = dev.mon_ver()
    assert info["model"] == "NEO-F10N" and info["fw"] == "SPGL1L5 6.00" and info["prot"] == "40.00"


def test_valget_follows_pagination(fake_serial, dev):
    stored = make_items(150)  # three pages: 64 + 64 + 22
    fake_serial.set(config={ubx.LAYER_RAM: stored})
    dev.open()
    assert dev.valget(ubx.LAYER_RAM) == stored


def test_valget_nak_means_empty_layer(fake_serial, dev):
    fake_serial.set(config={ubx.LAYER_RAM: make_items(3)})
    dev.open()
    assert dev.valget(ubx.LAYER_FLASH) == {}


def test_valget_without_answer_is_none(fake_serial, dev):
    fake_serial.set(answers=False)
    dev.open()
    assert dev.valget(ubx.LAYER_RAM) is None


def test_valset_uses_a_transaction_above_64_keys(fake_serial, dev):
    dev.open()
    items = list(make_items(100).items())
    assert dev.valset(ubx.MASK_RAM, items) is True
    sent = [w for w in fake_serial.made[0].writes if w[2:4] == bytes((ubx.CLS_CFG, ubx.ID_VALSET))]
    # version 1, then the transaction byte: start, then commit with the last chunk
    assert [(w[6], w[8]) for w in sent] == [(1, 1), (1, 3)]


def test_rx_and_tx_counters(dev):
    dev.open()
    dev.mon_ver()
    assert dev.tx_bytes >= 8 and dev.rx_bytes > 0


# --- find_port ------------------------------------------------------------------


def port(name, vid):
    return SimpleNamespace(device=name, vid=vid)


@pytest.mark.parametrize("ports, expected", [
    ([port("/dev/cu.Bluetooth", None), port("/dev/ttyUSB1", 0x1A86), port("/dev/ttyUSB0", 0x0403),
      port("/dev/ttyACM0", 0x1546)], "/dev/ttyACM0"),                    # u-blox native USB first
    ([port("/dev/ttyUSB1", 0x1A86), port("/dev/ttyUSB0", 0x0403)], "/dev/ttyUSB0"),  # FTDI before CH340
    ([port("/dev/cu.usbserial-110", 0x1A86), port("/dev/cu.usbserial-210", 0x10C4)],
     "/dev/cu.usbserial-210"),                                         # CP210x before CH340
    ([port("COM9", 0x2341)], "COM9"),                                  # any USB serial device
    ([port("/dev/cu.Bluetooth", None)], None),                         # nothing on USB
])
def test_find_port(monkeypatch, ports, expected):
    monkeypatch.setattr(device.list_ports, "comports", lambda: ports)
    assert device.find_port() == expected
