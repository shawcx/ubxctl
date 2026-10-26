# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

ubxctl is an unofficial Textual TUI for configuring u-blox generation 9+ GNSS receivers (u-center 2
style) over a serial port. It must not look like an official u-blox product: keep "u-blox"/"u-center"
out of names and titles, and keep the README disclaimer.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e .   # editable install; restart ubxctl to pick up edits
.venv/bin/ubxctl                                      # auto-detects port (find_port) and baud
.venv/bin/ubxctl -p /dev/ttyACM0 -b 921600
.venv/bin/python -c "import ubxctl.app"               # quick import/syntax check
.venv/bin/python scripts/screenshots.py [port [baud]] # regenerate docs/*.svg from a receiver (read-only)
.venv/bin/pip install --group test && .venv/bin/python -m pytest   # tests (~25 s, no hardware)
uv build && uvx twine check --strict dist/*           # check packaging
```

Releases: bump `version` in `pyproject.toml`, push, then `gh release create vX.Y.Z --generate-notes`.
`.github/workflows/release.yml` publishes to PyPI with trusted publishing (no token) and fails if the
tag doesn't match the version. The README uses absolute `raw.githubusercontent.com` image URLs so
they also render on PyPI.

Tests (pytest, in `tests/`) need no hardware:
- `test_ubx.py`: framing, `Framer`, key IDs, encode/decode, `nmea_ok`.
- `test_device.py`: `Device`, `detect_baud` and `find_port` against `FakeReceiver`, a stand-in for
  `serial.Serial` that streams NMEA at the right baud, noise at others, answers MON-VER/VALGET/VALSET,
  and can drop replies like a saturated UART.
- `test_app.py`: the app headless (`App.run_test()`) against `StubDevice`, which has the `Device`
  methods the app calls (`write`, `poll`, `send_acked`, `valget`, `valset`, `valdel`, `add_listener`,
  `mon_ver`) and the `port`, `baud`, `ser`, `rx_bytes`, `tx_bytes` attributes. Feed it frames (pyubx2's
  `UBXMessage(...).serialize()` or `ubx.frame()`) by appending `("UBX", frame)` to `app.inbox`. The Tools
  and baud paths also call `reset`, `set_receiver_baud` and `set_local_baud`.

CI (`.github/workflows/ci.yml`) runs them on Python 3.10–3.15 on Linux and 3.15 on macOS, and the
release workflow requires it. No linter yet. `app.save_screenshot()` checks UI changes by eye.

## Working with the real receiver

- Dev hardware: NEO-F10N, firmware `EXT SPGL1L5 6.00`, protocol 40.00.
  - Linux: `/dev/ttyUSB0` at 921600 baud. Its flash layer holds the user's own saved settings (10 Hz,
    921600, car profile, odometer and others).
  - macOS: `/dev/cu.usbserial-110` (CH340 adapter) at 38400 baud, factory settings with nothing in
    BBR/Flash, dual-band active antenna (AGC 7–11% connected, 73% unplugged).
  - Also on the Mac at times, two NMEA-only pucks (not u-blox; `detect_baud` raises `NoUbxReply`):
    - MediaTek MT3333 (AXN 5.1.6, 9600 baud) on `/dev/cu.usbserial-210` (CP2102N). `find_port` prefers
      CP210x over CH340, so pass `-p /dev/cu.usbserial-110` while it's plugged in.
    - Airoha AG3335M (L1/L5, firmware V3.2.1 2024-06-20, 115200 baud, answers `$PAIR021`) on
      `/dev/cu.PL2303G-USBtoUART210` (PL2303GC; needs Prolific's "PL2303 Serial" App Store driver).
- **Check for a running ubxctl before opening the port**: `pgrep -af ubxctl` on Linux, `pgrep -fl ubxctl`
  on macOS (BSD `-a` means something else). Serial ports aren't exclusive on either. If the user's
  ubxctl is running, both processes get a corrupted stream and request/response matching breaks.
- The default NMEA set is ~870 bytes per fix, so 10 Hz needs 115200 baud or more. At 38400 the UART
  saturates and the receiver drops UBX replies (large ones like NAV-SAT first). `detect_baud` and
  startup retry MON-VER at a rate with valid traffic, and the Status tab shows the link load and
  warns at 90% (`LINK_SATURATED`). For test runs, set `CFG-UART1OUTPROT-NMEA` false in RAM, then
  restore it.
- Test writes go to the RAM layer only. Snapshot the values with `valget` first and restore them
  afterwards. Never write BBR/Flash or reboot the receiver without asking: those change the user's
  saved setup.
- VALSET is atomic: one rejected key NAKs the whole message. Bisect key by key to find it. The receiver
  also lists keys it refuses to set, e.g. `CFG-MSGOUT-UBX_RXM_RAWX_*` on SPG 6.00 (no raw measurements).

## Architecture

Data flows through four modules:

- **`ubx.py`**: protocol layer with no I/O.
  - Builds and checksums UBX frames, encodes/decodes config values, and has the stream `Framer` that
    splits UBX frames and NMEA lines.
  - Key IDs carry their storage size in bits 28–30, so any key can be decoded even without a name.
  - `keydef()` maps a key ID to a `KeyDef`. Names and types come from pyubx2's `UBX_CONFIG_DATABASE`,
    converted from `CFG_GROUP_ITEM` to u-blox style `CFG-GROUP-ITEM`. Unknown keys get a synthesized
    `CFG-<GROUP>-0x…` name with `documented=False`.
- **`device.py`**: `Device` owns the serial port and a reader thread.
  - Listeners are called **on the reader thread**.
  - `request()` registers a waiter before writing, then matches the first UBX frame that satisfies a
    predicate. All config operations are serialized by `_cfg_lock`.
  - `valget` uses the wildcard key `0x0FFFFFFF` with position-based pagination (64 keys per message).
    A NAK means "layer holds none of these keys" and returns `{}`. `None` means no answer.
  - `valset` switches to a VALSET transaction above 64 keys; the last chunk commits.
  - `set_receiver_baud` doesn't wait for an ACK: the receiver switches rate before replying, so it
    writes, changes the local baud, and probes with MON-VER.
  - `find_port()` picks the default port from pyserial's `list_ports` by USB vendor ID: u-blox native
    USB (0x1546) first, then FTDI, CP210x, CH340 and PL2303 bridges.
- **`app.py`**: Textual UI.
  - The device listener only appends to the `app.inbox` deque. A 0.2 s timer (`_drain`) consumes it on
    the UI thread, so never touch widgets from the reader thread.
  - Live status comes from **polling** (`POLLED`, every 1 s), not from enabling periodic outputs, so
    viewing doesn't change the receiver's configuration.
  - `self.cfg` maps layer → `{key_id: raw bytes}` (RAM, BBR, Flash, Default) and is the only config
    cache. `load_config()` fills it with wildcard VALGETs; after any write, `_reread(keys)` refreshes
    just those keys and the affected rows.
  - Blocking device calls run in `@work(thread=True)` workers and update the UI via `call_from_thread`.
  - The Messages tab is derived from `CFG-MSGOUT-<MSG>_<PORT>` key names.
  - When MON-RF reports antenna status "unknown" (no antenna supervisor, as on the NEO-F10N),
    `antenna_guess()` infers active vs. passive from AGC. `AGC_ACTIVE_MAX` (40%) comes from two NEO-F10N
    readings only; it needs the normal LNA mode and no jamming.
  - Flash is assumed present (VALGET can't distinguish "no flash" from "empty flash") until a flash
    write is NAKed.
- **`portcheck.py`**: runs in `run()` before the app opens the port. It's Linux-only and returns early
  where there's no `/proc` (macOS), so nothing there checks for other programs holding the port.
  - Asks a running gpsd for its devices with `?DEVICES;` on its TCP port, and checks
    `/etc/default/gpsd` for socket-activation risk.
  - Stops `gpsd.socket` and `gpsd.service` via `sudo systemctl` only if gpsd is using this port, and
    restarts them in a `finally`. Any other process holding the port aborts startup.

Supporting data:

- **`keyinfo.py`** has hand-written descriptions, units, enum labels and `danger` warnings keyed by
  u-blox-style name. MSGOUT/INFMSG/*PROT keys get generated descriptions. Enum values are raw integers
  from the interface description; verify new ones against it.
- **`presets/*.txt`** use the same format as Tools → Export: `CFG-NAME value` per line, `#` comments.
  The leading comment block becomes the picker's title (first line) and description. Import skips
  `CFG-UART1-BAUDRATE` in RAM when it equals the current baud and refuses other values; baud changes
  must go through `apply_value` so the app follows the receiver. Validate new presets on hardware
  (RAM-only, then restore).

## Gotchas

- Undocumented keys: pyubx2 already contains every key in the official F10 SPG 6.00, F10/M10 SPG 7.0x
  and F10 TIM 3.01 interface descriptions. The ~344 remaining keys on the receiver are unpublished
  internals. Don't invent names for them, and don't write them.
- Avoid attribute names that shadow Textual internals: `Screen.layers` is a read-only property.
- Switching `TabbedContent.active` while a widget in the old pane has focus snaps back. Call
  `set_focus(None)` first, as `action_tab` does.
- GPS L5 is flagged unhealthy (pre-operational) and isn't used unless `CFG-SIGNAL-HEALTH_L5` is set.
