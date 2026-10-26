"""UBX framing, configuration-key helpers and value encoding.

Generation 9+ receivers (M10, F10, F9, X20) store configuration in a key/value
database accessed with UBX-CFG-VALGET / VALSET / VALDEL. A key ID is 32 bits:

    bits 28..30  storage size (1 = bit, 2 = 1 byte, 3 = 2, 4 = 4, 5 = 8)
    bits 16..23  group ID
    bits  0..11  item ID
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from pyubx2 import UBX_CONFIG_DATABASE

SYNC = b"\xb5\x62"

CLS_NAV, CLS_ACK, CLS_CFG, CLS_MON = 0x01, 0x05, 0x06, 0x0A
ID_VALSET, ID_VALGET, ID_VALDEL = 0x8A, 0x8B, 0x8C
ID_RST = 0x04

# Layer numbers used by VALGET, and bitmasks used by VALSET / VALDEL.
LAYER_RAM, LAYER_BBR, LAYER_FLASH, LAYER_DEFAULT = 0, 1, 2, 7
MASK_RAM, MASK_BBR, MASK_FLASH = 0x01, 0x02, 0x04

WILDCARD_ALL = 0x0FFFFFFF
MAX_KEYS_PER_MSG = 64

_SIZE_BYTES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8}


def checksum(body: bytes) -> bytes:
    a = b = 0
    for x in body:
        a = (a + x) & 0xFF
        b = (b + a) & 0xFF
    return bytes((a, b))


def frame(cls: int, mid: int, payload: bytes = b"") -> bytes:
    body = bytes((cls, mid)) + struct.pack("<H", len(payload)) + payload
    return SYNC + body + checksum(body)


def key_size(key: int) -> int:
    return _SIZE_BYTES[(key >> 28) & 0x7]


def key_group(key: int) -> int:
    return (key >> 16) & 0xFF


# --- key database ----------------------------------------------------------


@dataclass(frozen=True)
class KeyDef:
    key: int
    name: str          # u-blox style: CFG-GROUP-ITEM
    group: str         # e.g. RATE
    type: str          # L, U1, I2, E1, X4, R8 ...
    documented: bool


def _pretty(pyubx_name: str) -> tuple[str, str]:
    # CFG_NAVSPG_INFIL_MINELEV -> ("CFG-NAVSPG-INFIL_MINELEV", "NAVSPG")
    _, group, item = pyubx_name.split("_", 2)
    return f"CFG-{group}-{item}", group


def _short_type(t: str) -> str:
    # pyubx2 uses e.g. "U002", "L001", "R008" (letter + byte count)
    letter, n = t[0], int(t[1:])
    return "L" if letter == "L" else f"{letter}{n}"


_BY_KEY: dict[int, KeyDef] = {}
_BY_NAME: dict[str, KeyDef] = {}
_GROUP_NAMES: dict[int, str] = {}

for _n, (_k, _t) in UBX_CONFIG_DATABASE.items():
    _name, _grp = _pretty(_n)
    _def = KeyDef(_k, _name, _grp, _short_type(_t), True)
    # A handful of keys have aliases; keep the first name seen for display.
    _BY_KEY.setdefault(_k, _def)
    _BY_NAME[_name] = _def
    _GROUP_NAMES.setdefault(key_group(_k), _grp)


def keydef(key: int) -> KeyDef:
    d = _BY_KEY.get(key)
    if d:
        return d
    grp = _GROUP_NAMES.get(key_group(key), f"0x{key_group(key):02X}")
    size = key_size(key)
    t = "L" if size == 1 and (key >> 28) & 7 == 1 else f"U{size}"
    return KeyDef(key, f"CFG-{grp}-0x{key:08X}", grp, t, False)


def lookup(name_or_hex: str) -> KeyDef | None:
    s = name_or_hex.strip()
    if s.lower().startswith("0x"):
        return keydef(int(s, 16))
    s = s.upper().replace("_", "-", 2) if s.upper().startswith("CFG_") else s.upper()
    return _BY_NAME.get(s)


# --- value encoding ---------------------------------------------------------

_FMT = {
    "U1": "<B", "U2": "<H", "U4": "<I", "U8": "<Q",
    "E1": "<B", "E2": "<H", "E4": "<I",
    "X1": "<B", "X2": "<H", "X4": "<I", "X8": "<Q",
    "I1": "<b", "I2": "<h", "I4": "<i", "I8": "<q",
    "R4": "<f", "R8": "<d",
}


def decode(kd: KeyDef, raw: bytes):
    if kd.type == "L":
        return bool(raw[0] & 1)
    return struct.unpack(_FMT[kd.type], raw)[0]


def encode(kd: KeyDef, value) -> bytes:
    if kd.type == "L":
        return bytes((1 if value else 0,))
    if kd.type[0] in "R":
        return struct.pack(_FMT[kd.type], float(value))
    return struct.pack(_FMT[kd.type], int(value))


def parse_value(kd: KeyDef, text: str):
    """Parse user-entered text into a Python value for this key's type."""
    s = text.strip()
    if kd.type == "L":
        if s.lower() in ("1", "true", "t", "yes", "y", "on"):
            return True
        if s.lower() in ("0", "false", "f", "no", "n", "off"):
            return False
        raise ValueError("expected true/false")
    if kd.type[0] == "R":
        return float(s)
    v = int(s, 0)
    size = int(kd.type[1:])
    if kd.type[0] == "I":
        lo, hi = -(1 << (8 * size - 1)), (1 << (8 * size - 1)) - 1
    else:
        lo, hi = 0, (1 << (8 * size)) - 1
    if not lo <= v <= hi:
        raise ValueError(f"out of range {lo}..{hi}")
    return v


def parse_cfgdata(data: bytes) -> list[tuple[int, bytes]]:
    """Parse the key/value list of a VALGET response (after the 4-byte header)."""
    out, i = [], 0
    while i + 4 <= len(data):
        key = struct.unpack_from("<I", data, i)[0]
        n = key_size(key)
        out.append((key, data[i + 4:i + 4 + n]))
        i += 4 + n
    return out


# --- message builders ---------------------------------------------------------


def valget(layer: int, keys: list[int], position: int = 0) -> bytes:
    payload = struct.pack("<BBH", 0, layer, position) + b"".join(struct.pack("<I", k) for k in keys)
    return frame(CLS_CFG, ID_VALGET, payload)


def valset(layers: int, items: list[tuple[int, bytes]], transaction: int | None = None) -> bytes:
    # transaction: None = plain set, 1 = start, 2 = ongoing, 3 = commit
    if transaction is None:
        head = struct.pack("<BBH", 0, layers, 0)
    else:
        head = struct.pack("<BBBB", 1, layers, transaction, 0)
    return frame(CLS_CFG, ID_VALSET, head + b"".join(struct.pack("<I", k) + v for k, v in items))


def valdel(layers: int, keys: list[int]) -> bytes:
    payload = struct.pack("<BBH", 0, layers, 0) + b"".join(struct.pack("<I", k) for k in keys)
    return frame(CLS_CFG, ID_VALDEL, payload)


# CFG-RST navBbrMask values
HOT_START, WARM_START, COLD_START = 0x0000, 0x0001, 0xFFFF
# CFG-RST resetMode values
RST_HW_WATCHDOG, RST_SW, RST_GNSS, RST_HW_AFTER_SHUTDOWN = 0x00, 0x01, 0x02, 0x04


def cfg_rst(nav_bbr_mask: int, reset_mode: int) -> bytes:
    return frame(CLS_CFG, ID_RST, struct.pack("<HBB", nav_bbr_mask, reset_mode, 0))


def poll(cls: int, mid: int) -> bytes:
    return frame(cls, mid)


# --- stream framer ----------------------------------------------------------


class Framer:
    """Splits a byte stream into UBX frames and NMEA sentences."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def feed(self, data: bytes):
        self.buf += data
        out = []
        buf = self.buf
        while True:
            u = buf.find(SYNC)
            d = buf.find(b"$")
            starts = [x for x in (u, d) if x >= 0]
            if not starts:
                # Keep a trailing 0xB5 that might be the first half of a sync.
                del buf[:-1 if buf[-1:] == b"\xb5" else len(buf)]
                break
            s = min(starts)
            del buf[:s]
            if buf[:1] == b"$":
                end = buf.find(b"\n")
                if end < 0:
                    if len(buf) > 120:
                        del buf[:1]
                        continue
                    break
                line = bytes(buf[:end + 1])
                if SYNC in line or end > 120:
                    # A stray '$' inside binary data, not a real sentence.
                    del buf[:1]
                    continue
                del buf[:end + 1]
                out.append(("NMEA", line.decode("ascii", "replace").strip()))
                continue
            if len(buf) < 6:
                break
            n = struct.unpack_from("<H", buf, 4)[0]
            if n > 8192:
                del buf[:2]
                continue
            if len(buf) < n + 8:
                break
            f = bytes(buf[:n + 8])
            if checksum(f[2:-2]) != f[-2:]:
                del buf[:2]
                continue
            del buf[:n + 8]
            out.append(("UBX", f))
        return out
