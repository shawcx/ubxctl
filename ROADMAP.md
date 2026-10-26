# Roadmap

Status as of 2026-10-26: v0.1.0, working against a NEO-F10N on SPG 6.00 on Linux and macOS.
Implemented: Status (with odometer and an AGC-based antenna guess), Satellites, Configuration (all
layers, edit/revert), Messages matrix, Console, Tools (save/clear/export/import/resets), 9 validated
presets, gpsd conflict handling, port auto-detection, link-load warning. Tests run in CI on
Python 3.10–3.15; published to PyPI.

## Verify on hardware

These have only been tested against a simulated device or mocks. The receiver on the Mac has factory
settings with nothing in BBR/Flash, so it's a safe place to try the flash and reset items.

- [ ] Trip reset: does the receiver ACK `UBX-NAV-RESETODO`, and does `totalDistance` keep counting?
- [ ] gpsd under systemd: the `sudo systemctl stop`/`start` path and its password prompt (logic tested
      with mocks only).
- [ ] Save changes to Flash/BBR, Clear saved config, Reboot (with baud re-detection), hot/warm/cold
      start. Deliberately skipped so the user's saved setup wasn't touched.

## Next

- [x] Warn when the output can't fit the baud rate (Status tab link load), and keep baud detection
      working when a saturated UART drops replies.
- [ ] Port auto-detection with several USB serial devices: `find_port` goes by vendor ID only, so an
      NMEA-only puck on a CP2102N wins over a u-blox on a CH340. Probe the candidates in order and use
      the first that answers UBX (`detect_baud` already raises `NoUbxReply` for NMEA-only devices).
- [ ] macOS: nothing checks for other programs holding the port (`portcheck` needs `/proc`). Open it
      with pyserial's `exclusive=True` and name any holders with `lsof`.
- [ ] Make undocumented keys read-only in the editor.
- [ ] Tag keys whose pyubx2 name comes from another firmware, not the SPG 6.00 interface description
      (at least 26 on the NEO-F10N).
- [ ] Tools tab: "newer firmware known" line from a built-in table of latest version per firmware
      family (no network access). F10 SPG 7.01 was published 2026-09-02.
- [x] Test suite (`tests/`) and CI on Python 3.10–3.15; releases require it.
- [ ] Linting (ruff) in CI.

## Before sharing

- [x] git repository, license, `.gitignore` (github.com/shawcx/ubxctl, MIT).
- [x] README screenshots (`scripts/screenshots.py`).
- [x] Published to PyPI as `ubxctl` (0.1.0 on 2026-10-26, via the release workflow).
- [ ] Rename internal `UbloxApp` class to match the project name.

## Later

- [ ] Test against SPG 7.x firmware (protocol 52.00): new and changed keys, preset defaults (the L1/L5
      variant enables L5 by default).
- [ ] Check the AGC antenna threshold (40%) on other receivers (M10, F9) and with a passive antenna.
- [ ] Connections other than UART1. Baud following and the protocol-lockout warnings assume the TUI
      is on UART1.
- [ ] Non-interactive mode for scripting, e.g. `ubxctl --preset profile-car --layer flash`.
- [ ] Sky plot of satellite positions.

## Not planned

- **Firmware flashing**: u-blox's update protocol isn't publicly documented, its `ubxfwupdate` tool is
  Windows-only with source under NDA, and a failed flash can leave the module unusable.
- **Raw-measurement logging (RXM-RAWX) on the NEO-F10N**: SPG 6.00 lists the key but rejects it.
- **Names for undocumented keys**: they aren't in any public u-blox document, so names would be guesses.
