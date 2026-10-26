import asyncio
import io
import time

import pytest
from pyubx2 import GET, UBXMessage
from rich.console import Console
from textual.widgets import Static

from ubxctl import app as appmod
from ubxctl.app import LINK_SATURATED, UbloxApp, antenna_guess, link_load

RXINFO = {"sw": "EXT SPGL1L5 6.00 (041e8a)", "hw": "000A0000",
          "ext": ["FWVER=SPGL1L5 6.00", "PROTVER=40.00", "MOD=NEO-F10N"],
          "fw": "SPGL1L5 6.00", "prot": "40.00", "model": "NEO-F10N"}


class StubDevice:
    """The Device methods UbloxApp calls, with no receiver behind them.

    RX bytes grow at `rx_rate` bytes/s from creation, which is what the link load is computed from.
    """

    port, baud = "/dev/stub", 38400

    def __init__(self, rx_rate: float = 0.0) -> None:
        self.ser = object()
        self.rx_rate, self.t0 = rx_rate, time.monotonic()
        self.tx_bytes = 0
        self.written: list[bytes] = []

    @property
    def rx_bytes(self) -> int:
        return int((time.monotonic() - self.t0) * self.rx_rate)

    def add_listener(self, fn):
        pass

    def write(self, data):
        self.written.append(data)
        self.tx_bytes += len(data)

    def poll(self, cls, mid, timeout=1.5):
        return None

    def send_acked(self, data, timeout=1.5):
        return True

    def valget(self, layer, keys=None):
        return {}

    def valset(self, layers, items):
        return True

    def valdel(self, layers, keys):
        return True

    def mon_ver(self, timeout=1.5):
        return RXINFO


def mon_rf(agc: int, ant_status: int = 1, jamming: int = 1) -> bytes:
    blocks = {}
    for i, block in ((1, 0), (2, 1)):
        blocks.update({f"blockId_{i:02d}": block, f"jammingState_{i:02d}": jamming,
                       f"antStatus_{i:02d}": ant_status, f"antPower_{i:02d}": 2,
                       f"noisePerMS_{i:02d}": 90, f"agcCnt_{i:02d}": agc, f"jamInd_{i:02d}": 10})
    return UBXMessage("MON", "MON-RF", GET, version=0, nBlocks=2, **blocks).serialize()


def text(app, selector: str) -> str:
    console = Console(width=200, file=io.StringIO(), record=True)
    console.print(app.query_one(selector, Static).content)
    return console.export_text()


def run(coro):
    return asyncio.run(coro)


# --- pure helpers -----------------------------------------------------------------


@pytest.mark.parametrize("args, expected", [
    ((6.8, 0, "OK", False), ("likely active (from AGC)", "green")),
    ((39.9, None, "unknown", True), ("likely active (from AGC)", "green")),
    ((72.7, 0, "OK", True), ("likely passive (from AGC)", "green")),
    ((72.7, 0, "OK", False), ("passive or none (from AGC)", "yellow")),
    ((6.8, 2, "OK", False), ("unknown (AGC guess needs normal LNA mode)", "")),
    ((6.8, 0, "warning", False), ("unknown (AGC unreliable while jammed)", "")),
    ((6.8, 0, "CRITICAL", False), ("unknown (AGC unreliable while jammed)", "")),
])
def test_antenna_guess(args, expected):
    assert antenna_guess(*args) == expected


def test_link_load():
    assert link_load([], 38400) is None
    assert link_load([(0.0, 0)], 38400) is None
    assert link_load([(0.0, 0), (2.0, 7680)], 38400) is None  # window too short
    assert link_load([(0.0, 0), (4.0, 4 * 3840)], 38400) == pytest.approx(1.0)
    assert link_load([(0.0, 0), (4.0, 4 * 960)], 38400) == pytest.approx(0.25)
    assert link_load([(0.0, 0), (4.0, 4 * 960)], 9600) == pytest.approx(1.0)


# --- headless app -----------------------------------------------------------------


def test_status_shows_the_antenna_guess():
    async def go():
        app = UbloxApp(StubDevice(), RXINFO)
        async with app.run_test(size=(130, 40)) as pilot:
            app.inbox.append(("UBX", mon_rf(agc=558)))
            await pilot.pause(0.5)
            assert text(app, "#rf").count("likely active (from AGC)") == 2
            app.inbox.append(("UBX", mon_rf(agc=5952)))
            await pilot.pause(0.5)
            assert text(app, "#rf").count("passive or none (from AGC)") == 2
            app.inbox.append(("UBX", mon_rf(agc=5952, ant_status=4)))  # a real supervisor: OPEN
            await pilot.pause(0.5)
            assert "OPEN, power unknown" in text(app, "#rf")
    run(go())


def test_polls_status_messages():
    async def go():
        dev = StubDevice()
        app = UbloxApp(dev, RXINFO)
        async with app.run_test() as pilot:
            await pilot.pause(1.3)
        polled = {bytes(w[2:4]) for w in dev.written}
        assert {bytes(p) for p in appmod.POLLED} <= polled
    run(go())


@pytest.mark.parametrize("rate, saturated", [(3840, True), (1000, False)])
def test_link_load_warning(monkeypatch, rate, saturated):
    # Shorter minimum window than the real 3 s (tested above) so this doesn't hinge on timer jitter.
    monkeypatch.setattr(appmod, "link_load", lambda samples, baud: link_load(samples, baud, min_span=0.5))

    async def go():
        app = UbloxApp(StubDevice(rx_rate=rate), RXINFO)
        notes: list[str] = []
        async with app.run_test(size=(130, 40)) as pilot:
            app.notify = lambda message, **kw: notes.append(message)
            await pilot.pause(2.6)  # ticks at ~1 s and ~2 s
            rf = text(app, "#rf")
            assert "of 38400 baud" in rf
            assert app.link_saturated is saturated
            assert ("Saturated" in rf) is saturated
            assert sum("saturated" in n for n in notes) == (1 if saturated else 0)
    run(go())


def test_link_samples_reset_on_baud_change():
    async def go():
        dev = StubDevice(rx_rate=3840)
        app = UbloxApp(dev, RXINFO)
        async with app.run_test() as pilot:
            await pilot.pause(1.2)
            assert app.link
            dev.baud = 921600
            await pilot.pause(1.1)
            assert len(app.link) == 1 and app.link_baud == 921600
    run(go())


def test_saturation_threshold_is_below_full_load():
    assert 0.5 < LINK_SATURATED < 1.0
