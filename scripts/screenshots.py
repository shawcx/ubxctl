"""Regenerate the README screenshots in docs/ from a connected receiver.

Usage: .venv/bin/python scripts/screenshots.py [port [baud]]

Reads only; nothing is written to the receiver. The position and time shown are replaced with a
fixed made-up fix (Greenwich), so the screenshots don't give away where they were taken.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

from ubxctl import app as ubxapp
from ubxctl.app import UbloxApp
from ubxctl.device import BAUD_CANDIDATES, Device, detect_baud, find_port

DOCS = Path(__file__).resolve().parent.parent / "docs"
SIZE = (120, 34)
TABS = [("1", "status"), ("2", "satellites"), ("3", "configuration")]
FAKE_FIX = dict(lat=51.4778700, lon=-0.0014000, height=93_100, hMSL=46_200,
                year=2026, month=10, day=1, hour=12, min=0, second=0)


class ScreenshotApp(UbloxApp):
    CSS_PATH = Path(ubxapp.__file__).with_name("app.tcss")  # Textual resolves it next to the subclass

    def _handle_status(self, f: bytes) -> bool:
        changed = super()._handle_status(f)
        if self.pvt is not None and not isinstance(self.pvt, SimpleNamespace):
            fields = {k: v for k, v in vars(self.pvt).items() if not k.startswith("_")}
            self.pvt = SimpleNamespace(**(fields | FAKE_FIX))
        return changed


async def shoot(app: UbloxApp) -> None:
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(8)  # config load and a few polls
        for key, name in TABS:
            await pilot.press(key)
            await pilot.pause(2)
            app.save_screenshot(str(DOCS / f"{name}.svg"))
            print(f"wrote docs/{name}.svg")
        await pilot.press("q")


def main() -> None:
    port = sys.argv[1] if len(sys.argv) > 1 else find_port()
    if port is None:
        raise SystemExit("No USB serial port found; pass one as an argument")
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else detect_baud(port, (921600,) + BAUD_CANDIDATES)
    if baud is None:
        raise SystemExit(f"No UBX response on {port}; pass the baud rate as a second argument")
    dev = Device(port, baud)
    dev.open()
    try:
        rxinfo = None
        for _ in range(5):  # a UART saturated by NMEA can drop replies
            rxinfo = rxinfo or dev.mon_ver(timeout=2)
        if rxinfo is None:
            raise SystemExit(f"No UBX response on {port} at {baud} baud")
        DOCS.mkdir(exist_ok=True)
        asyncio.run(shoot(ScreenshotApp(dev, rxinfo)))
    finally:
        dev.close()


if __name__ == "__main__":
    main()
