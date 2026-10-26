"""Serial connection to a u-blox receiver with a background reader thread."""

from __future__ import annotations

import struct
import threading
import time
from collections.abc import Callable

import serial
from serial.tools import list_ports
from pyubx2 import UBXReader

from . import ubx

BAUD_CANDIDATES = (38400, 9600, 115200, 230400, 460800, 921600)
SATURATED_RETRIES = 4  # MON-VER polls at a rate with valid traffic but no reply yet
# USB vendor IDs in find_port() preference order: u-blox native USB, then USB-serial bridges
# (FTDI, Silicon Labs CP210x, WCH CH340, Prolific PL2303).
USB_VIDS = {vid: i for i, vid in enumerate((0x1546, 0x0403, 0x10C4, 0x1A86, 0x067B))}

Listener = Callable[[str, object], None]


def parse_ubx(raw: bytes):
    """Decode a UBX frame with pyubx2, or None if it can't be decoded."""
    try:
        return UBXReader.parse(raw)
    except Exception:
        return None


class _Waiter:
    def __init__(self, match: Callable[[bytes], bool]) -> None:
        self.match = match
        self.event = threading.Event()
        self.frame: bytes | None = None


class Device:
    def __init__(self, port: str, baud: int) -> None:
        self.port = port
        self.baud = baud
        self.ser: serial.Serial | None = None
        self._framer = ubx.Framer()
        self._listeners: list[Listener] = []
        self._waiters: list[_Waiter] = []
        self._lock = threading.Lock()
        self._wlock = threading.Lock()
        self._cfg_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._running = False
        self.rx_bytes = 0
        self.tx_bytes = 0

    # --- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        self.ser = serial.Serial(self.port, self.baud, timeout=0.05)
        self._running = True
        self._thread = threading.Thread(target=self._reader, name="ubx-reader", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if self.ser:
            self.ser.close()
        self.ser = None

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def set_local_baud(self, baud: int) -> None:
        self.baud = baud
        if self.ser:
            self.ser.baudrate = baud
            self.ser.reset_input_buffer()
            self._framer = ubx.Framer()

    def _reader(self) -> None:
        while self._running:
            try:
                data = self.ser.read(4096)
            except (serial.SerialException, OSError, TypeError) as e:
                self._emit("ERROR", str(e))
                self._running = False
                break
            if not data:
                continue
            self.rx_bytes += len(data)
            for kind, item in self._framer.feed(data):
                if kind == "UBX":
                    with self._lock:
                        for w in self._waiters:
                            if w.frame is None and w.match(item):
                                w.frame = item
                                w.event.set()
                self._emit(kind, item)

    def _emit(self, kind: str, item) -> None:
        for fn in self._listeners:
            try:
                fn(kind, item)
            except Exception:
                pass

    # --- raw I/O -------------------------------------------------------------

    def write(self, data: bytes) -> None:
        with self._wlock:
            self.ser.write(data)
            self.tx_bytes += len(data)
        self._emit("TX", data)

    def request(self, data: bytes, match: Callable[[bytes], bool], timeout: float = 1.5,
                retries: int = 1) -> bytes | None:
        """Send a frame and wait for the first incoming UBX frame accepted by `match`."""
        for _ in range(retries + 1):
            w = _Waiter(match)
            with self._lock:
                self._waiters.append(w)
            try:
                self.write(data)
                if w.event.wait(timeout):
                    return w.frame
            finally:
                with self._lock:
                    self._waiters.remove(w)
        return None

    def poll(self, cls: int, mid: int, timeout: float = 1.5) -> bytes | None:
        return self.request(ubx.poll(cls, mid), lambda f: f[2] == cls and f[3] == mid, timeout)

    def send_acked(self, data: bytes, timeout: float = 1.5) -> bool | None:
        """Send a CFG frame; True on ACK, False on NAK, None on timeout."""
        cls, mid = data[2], data[3]

        def match(f: bytes) -> bool:
            return f[2] == ubx.CLS_ACK and len(f) >= 10 and f[6] == cls and f[7] == mid

        f = self.request(data, match, timeout, retries=0)
        if f is None:
            return None
        return f[3] == 0x01

    # --- receiver info -------------------------------------------------------

    def mon_ver(self, timeout: float = 1.5) -> dict | None:
        f = self.poll(ubx.CLS_MON, 0x04, timeout)
        if not f:
            return None
        p = f[6:-2]
        info = {
            "sw": p[:30].rstrip(b"\0").decode(errors="replace"),
            "hw": p[30:40].rstrip(b"\0").decode(errors="replace"),
            "ext": [p[i:i + 30].rstrip(b"\0").decode(errors="replace") for i in range(40, len(p), 30)],
        }
        for e in info["ext"]:
            if e.startswith("MOD="):
                info["model"] = e[4:]
            elif e.startswith("FWVER="):
                info["fw"] = e[6:]
            elif e.startswith("PROTVER="):
                info["prot"] = e[8:]
        return info

    # --- configuration database -------------------------------------------

    def valget(self, layer: int, keys: list[int] | None = None) -> dict[int, bytes] | None:
        """Read keys (default: everything) from a layer, following pagination.

        Returns {} if the layer holds none of the keys (receiver NAKs), or None
        if the receiver didn't answer.
        """
        keys = keys or [ubx.WILDCARD_ALL]
        out: dict[int, bytes] = {}
        with self._cfg_lock:
            for i in range(0, len(keys), ubx.MAX_KEYS_PER_MSG):
                chunk = keys[i:i + ubx.MAX_KEYS_PER_MSG]
                wildcard = len(chunk) == 1 and (chunk[0] & 0xFFFF) == 0xFFFF
                pos = 0
                while True:
                    def match(f: bytes, pos=pos) -> bool:
                        if f[2] == ubx.CLS_CFG and f[3] == ubx.ID_VALGET:
                            return f[7] == layer and struct.unpack_from("<H", f, 8)[0] == pos
                        return f[2] == ubx.CLS_ACK and f[3] == 0x00 and f[6] == ubx.CLS_CFG \
                            and f[7] == ubx.ID_VALGET
                    f = self.request(ubx.valget(layer, chunk, pos), match)
                    if f is None:
                        return None
                    if f[2] == ubx.CLS_ACK:  # NAK: nothing stored for these keys
                        break
                    items = ubx.parse_cfgdata(f[10:-2])
                    out.update(items)
                    if not wildcard or len(items) < ubx.MAX_KEYS_PER_MSG:
                        break
                    pos += len(items)
        return out

    def valset(self, layers: int, items: list[tuple[int, bytes]]) -> bool:
        """Write values; uses a transaction when more than 64 keys are written."""
        with self._cfg_lock:
            if len(items) <= ubx.MAX_KEYS_PER_MSG:
                return self.send_acked(ubx.valset(layers, items)) is True
            chunks = [items[i:i + ubx.MAX_KEYS_PER_MSG] for i in range(0, len(items), ubx.MAX_KEYS_PER_MSG)]
            for n, chunk in enumerate(chunks):
                # Start, then ongoing; the last chunk commits the transaction.
                txn = 1 if n == 0 else 3 if n == len(chunks) - 1 else 2
                if self.send_acked(ubx.valset(layers, chunk, txn)) is not True:
                    return False
            return True

    def valdel(self, layers: int, keys: list[int]) -> bool:
        with self._cfg_lock:
            for i in range(0, len(keys), ubx.MAX_KEYS_PER_MSG):
                if self.send_acked(ubx.valdel(layers, keys[i:i + ubx.MAX_KEYS_PER_MSG])) is not True:
                    return False
            return True

    def set_receiver_baud(self, baud: int, layers: int) -> bool:
        """Change CFG-UART1-BAUDRATE and follow the receiver to the new rate.

        Assumes this connection is the receiver's UART1. Returns True if the
        receiver answers at the new rate.
        """
        kd = ubx.lookup("CFG-UART1-BAUDRATE")
        old = self.baud
        with self._cfg_lock:
            # The receiver may switch before the ACK leaves, so don't rely on it.
            self.write(ubx.valset(layers, [(kd.key, ubx.encode(kd, baud))]))
            time.sleep(0.25)
            self.set_local_baud(baud)
        if self.mon_ver(timeout=1.0):
            return True
        self.set_local_baud(old)
        return False

    def reset(self, nav_bbr_mask: int, reset_mode: int) -> None:
        # The receiver doesn't acknowledge CFG-RST.
        self.write(ubx.cfg_rst(nav_bbr_mask, reset_mode))


class NoUbxReply(Exception):
    """The device sends valid NMEA/UBX at some baud rate but never answers a UBX poll."""


def detect_baud(port: str, candidates=BAUD_CANDIDATES) -> int | None:
    """Find the baud rate at which the receiver answers a UBX poll.

    Valid NMEA or UBX traffic means the rate is right even without a reply: a UART saturated
    with output drops some replies, so that rate gets longer retries instead of being skipped.
    Raises NoUbxReply if the device talks but never answers (not a u-blox receiver, or UBX
    input is off).
    """
    for baud in candidates:
        try:
            d = Device(port, baud)
            d.open()
        except serial.SerialException:
            return None
        valid: list[str] = []
        d.add_listener(lambda kind, item: valid.append(kind)
                       if kind == "UBX" or (kind == "NMEA" and ubx.nmea_ok(item)) else None)
        try:
            if d.mon_ver(timeout=0.6):
                return baud
            if len(valid) >= 2:
                for _ in range(SATURATED_RETRIES):
                    if d.mon_ver(timeout=1.0):
                        return baud
                raise NoUbxReply(f"valid NMEA at {baud} baud but no reply to UBX polls; "
                                 "not a u-blox receiver, or its UBX input is off")
        finally:
            d.close()
    return None


def find_port() -> str | None:
    """The most likely receiver port: u-blox native USB first, then common USB-serial adapters."""
    ports = [p for p in list_ports.comports() if p.vid is not None]
    ports.sort(key=lambda p: (USB_VIDS.get(p.vid, len(USB_VIDS)), p.device))
    return ports[0].device if ports else None
