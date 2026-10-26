import struct

import pytest

from ubxctl import ubx

MON_VER_POLL = bytes.fromhex("b5620a0400000e34")  # the well-known UBX-MON-VER poll


def test_frame_and_checksum():
    assert ubx.frame(ubx.CLS_MON, 0x04) == MON_VER_POLL
    assert ubx.poll(ubx.CLS_MON, 0x04) == MON_VER_POLL
    f = ubx.frame(0x06, 0x8A, b"\x00\x01\x00\x00")
    assert f[:2] == ubx.SYNC and struct.unpack_from("<H", f, 4)[0] == 4
    assert ubx.checksum(f[2:-2]) == f[-2:]


def test_key_ids_match_the_interface_description():
    # Key IDs as published by u-blox (the LNA one appears in the NEO-F10N integration manual).
    assert ubx.lookup("CFG-HW-RF_LNA_MODE").key == 0x20A30057
    assert ubx.lookup("CFG-UART1-BAUDRATE").key == 0x40520001
    assert ubx.lookup("CFG-RATE-MEAS").key == 0x30210001


@pytest.mark.parametrize("key, size", [(0x10110013, 1), (0x20A30057, 1), (0x30210001, 2),
                                       (0x40520001, 4), (0x5005002A, 8)])
def test_key_size(key, size):
    assert ubx.key_size(key) == size


def test_lookup_accepts_pyubx2_names_and_hex():
    kd = ubx.lookup("CFG-RATE-MEAS")
    assert (kd.name, kd.group, kd.type, kd.documented) == ("CFG-RATE-MEAS", "RATE", "U2", True)
    assert ubx.lookup("cfg_rate_meas") == kd
    assert ubx.lookup("0x30210001") == kd
    assert ubx.lookup("CFG-NO-SUCH_KEY") is None


def test_keydef_synthesizes_unknown_keys():
    kd = ubx.keydef(0x30FE0123)
    assert kd.documented is False
    assert kd.name == "CFG-0xFE-0x30FE0123"
    assert kd.type == "U2"
    assert ubx.keydef(0x10FE0001).type == "L"
    # A known group keeps its name.
    assert ubx.keydef(0x30210FFF).name == "CFG-RATE-0x30210FFF"


@pytest.mark.parametrize("name, value", [
    ("CFG-ODO-USE_ODO", True),
    ("CFG-ODO-USE_ODO", False),
    ("CFG-RATE-MEAS", 100),
    ("CFG-UART1-BAUDRATE", 921600),
    ("CFG-NAVSPG-DYNMODEL", 4),
    ("CFG-NAVSPG-INFIL_MINELEV", -5),
    ("CFG-NAVSPG-CONSTR_ALT", -123456),
])
def test_encode_decode_roundtrip(name, value):
    kd = ubx.lookup(name)
    raw = ubx.encode(kd, value)
    assert len(raw) == ubx.key_size(kd.key)
    assert ubx.decode(kd, raw) == value


def test_encode_float():
    kd = ubx.KeyDef(0x50FE0001, "CFG-TEST-R8", "TEST", "R8", False)
    assert ubx.decode(kd, ubx.encode(kd, 1.5)) == 1.5


@pytest.mark.parametrize("text, value", [("true", True), ("ON", True), ("1", True),
                                         ("false", False), ("off", False), ("0", False)])
def test_parse_value_bool(text, value):
    assert ubx.parse_value(ubx.lookup("CFG-ODO-USE_ODO"), text) is value


def test_parse_value_ranges():
    u1 = ubx.lookup("CFG-NAVSPG-DYNMODEL")
    assert ubx.parse_value(u1, "0x08") == 8
    with pytest.raises(ValueError):
        ubx.parse_value(u1, "256")
    i1 = ubx.lookup("CFG-NAVSPG-INFIL_MINELEV")
    assert ubx.parse_value(i1, "-128") == -128
    with pytest.raises(ValueError):
        ubx.parse_value(i1, "-129")
    with pytest.raises(ValueError):
        ubx.parse_value(ubx.lookup("CFG-ODO-USE_ODO"), "maybe")


def test_parse_cfgdata_roundtrip():
    items = [(0x30210001, b"\x64\x00"), (0x10110013, b"\x01"), (0x40520001, struct.pack("<I", 38400))]
    data = b"".join(struct.pack("<I", k) + v for k, v in items)
    assert ubx.parse_cfgdata(data) == items


def test_valset_uses_a_transaction_header_only_when_asked():
    item = [(0x30210001, b"\x64\x00")]
    plain = ubx.valset(ubx.MASK_RAM, item)
    assert plain[6:10] == bytes((0, ubx.MASK_RAM, 0, 0))
    txn = ubx.valset(ubx.MASK_RAM | ubx.MASK_BBR, item, transaction=3)
    assert txn[6:10] == bytes((1, 0x03, 3, 0))


def test_nmea_ok():
    assert ubx.nmea_ok("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
    assert not ubx.nmea_ok("$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*48")
    assert not ubx.nmea_ok("$GPGGA,123519,4807.038,N")
    assert not ubx.nmea_ok("$GPGGA,123519*ZZ")
    assert not ubx.nmea_ok("GPGGA,123519*00")


class TestFramer:
    NMEA = b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"

    def test_splits_ubx_and_nmea_with_noise(self):
        f = ubx.Framer()
        out = f.feed(b"\x00\xff" + self.NMEA + b"junk" + MON_VER_POLL + b"\x13")
        assert out == [("NMEA", self.NMEA.decode().strip()), ("UBX", MON_VER_POLL)]

    def test_reassembles_across_feeds(self):
        f = ubx.Framer()
        data = self.NMEA + MON_VER_POLL
        out = []
        for i in range(len(data)):
            out += f.feed(data[i:i + 1])
        assert [k for k, _ in out] == ["NMEA", "UBX"]
        assert out[1][1] == MON_VER_POLL

    def test_keeps_a_split_sync_byte(self):
        f = ubx.Framer()
        assert f.feed(b"noise\xb5") == []
        assert f.feed(MON_VER_POLL[1:]) == [("UBX", MON_VER_POLL)]

    def test_drops_a_bad_checksum(self):
        bad = MON_VER_POLL[:-1] + b"\x00"
        assert ubx.Framer().feed(bad + MON_VER_POLL) == [("UBX", MON_VER_POLL)]

    def test_ignores_a_dollar_inside_binary(self):
        payload = b"$" + bytes(range(40))
        frame = ubx.frame(0x01, 0x07, payload)
        assert ubx.Framer().feed(frame) == [("UBX", frame)]
