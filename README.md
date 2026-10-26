# ubxctl

An unofficial terminal configuration tool for u-blox generation 9+ GNSS receivers
(M10, F10, F9, X20), in the spirit of u-center 2. Tested against a NEO-F10N (SPG 6.00).

> ubxctl is an independent project. It is not affiliated with, endorsed by, or supported by
> u-blox AG. u-blox and u-center are trademarks of u-blox AG.

```
.venv/bin/ubxctl                         # /dev/ttyUSB0 (macOS: first /dev/cu.usbserial-*), auto-detect baud
.venv/bin/ubxctl -p /dev/ttyACM0 -b 921600
```

Setup: `python3 -m venv .venv && .venv/bin/pip install -e .`

## Tabs (keys 1–6)

1. **Status**: fix, position, accuracy, DOP, antenna/jamming state per RF block, and the
   receiver's odometer (trip ± uncertainty, total). **Reset trip** (or `o`) zeroes the trip.
   Without an antenna supervisor (e.g. NEO-F10N), the antenna type is guessed from AGC: an
   active antenna's gain pulls AGC low (7% vs. 73% unplugged on a NEO-F10N).
2. **Satellites**: per-SV C/N0, elevation/azimuth, used flag, per-signal C/N0 (e.g. L1C/A and L5Q).
3. **Configuration**: every key the receiver reports, by group, with RAM / BBR / Flash / Default
   values. Yellow = differs from default or stored in BBR/Flash. Enter edits a key and lets you
   choose which layers to write; "Revert to default" deletes it from BBR/Flash and restores RAM.
   Tick "Undocumented keys" to see keys the receiver has but the interface description doesn't name.
4. **Messages**: output-rate matrix (message × port) for NMEA, PUBX and UBX messages.
5. **Console**: live NMEA/UBX traffic.
6. **Tools**: save RAM changes to Flash/BBR, clear saved config, export/import, hot/warm/cold start, reboot.

Status data is fetched by polling (NAV-PVT, NAV-DOP, NAV-SAT, NAV-SIG, MON-RF once a second), so it
doesn't change the receiver's output configuration. `p` pauses polling.

## Presets

Tools → Import / presets lists the files in `ubxctl/presets/`. Each one changes only the
keys it names, so they combine: pick one `output-*` and one `profile-*`, then add any others.
Rates in `CFG-MSGOUT-*` are per navigation solution (10 = every 10th fix).

| Preset | What it does |
|---|---|
| `output-nmea-default` | Factory NMEA set on UART1 (GGA GLL GSA GSV RMC VTG); undoes the other output presets |
| `output-nmea-minimal` | GGA + RMC only, ~85% less serial traffic |
| `output-ubx-binary` | NMEA off; UBX NAV-PVT + NAV-DOP every fix, NAV-SAT every 10th |
| `profile-portable` | Portable model, 1 Hz (factory default); undoes the other profiles |
| `profile-car` | Automotive model, 10 Hz, odometer on its car profile |
| `profile-drone` | Airborne <4g model, 10 Hz |
| `profile-timing` | Stationary model, 1 Hz, NMEA ZDA on (for NTP via gpsd/chrony) |
| `gps-l5-override` | Use GPS L5 despite its pre-operational "unhealthy" flag |
| `jamming-monitor` | Enable interference detection (Status tab shows OK/warning/critical) |

To make your own, export from Tools and edit the file. The leading `#` lines become the title
and description in the picker.

## Notes

- **gpsd:** serial ports aren't exclusive on Linux, so if gpsd also had the port open, both
  programs would get a corrupted stream. At startup the TUI asks gpsd which devices it has
  open (and checks `/etc/default/gpsd` in case socket activation could start it mid-session).
  If gpsd is using this port it stops `gpsd.socket`/`gpsd.service` (via `sudo systemctl`,
  so expect a password prompt) and starts them again when you quit. gpsd serving other
  devices is left alone. Any other program holding the port makes the TUI refuse to start.

- Changes go to RAM only unless you tick BBR or Flash; RAM changes are lost on power cycle.
- Changing `CFG-UART1-BAUDRATE` in RAM switches the TUI to the new rate automatically. This
  assumes the TUI is connected to UART1 (true for USB-serial adapters on the UART pins).
- Export format is one `CFG-NAME value` per line; `#` starts a comment.
- Key names and types come from pyubx2's config database; descriptions for common keys are in
  `ubxctl/keyinfo.py`.
