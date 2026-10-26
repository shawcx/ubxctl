"""Textual front end: status, satellites, configuration, messages, console, tools."""

from __future__ import annotations

import collections
import time
from pathlib import Path

from pyubx2.ubxtypes_core import UBX_MSGIDS
from rich.table import Table
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (Button, Checkbox, DataTable, Footer, Header, Input, Label,
                             OptionList, RichLog, Select, Static, TabbedContent, TabPane)
from textual.widgets.option_list import Option

from . import portcheck, ubx
from .device import BAUD_CANDIDATES, Device, NoUbxReply, detect_baud, parse_ubx
from .keyinfo import info

PRESET_DIR = Path(__file__).parent / "presets"
LAYER_NAMES = {ubx.LAYER_RAM: "RAM", ubx.LAYER_BBR: "BBR", ubx.LAYER_FLASH: "Flash",
               ubx.LAYER_DEFAULT: "Default"}
GNSS = {0: "GPS", 1: "SBAS", 2: "Galileo", 3: "BeiDou", 4: "IMES", 5: "QZSS", 6: "GLONASS", 7: "NavIC"}
SIGNALS = {
    (0, 0): "L1C/A", (0, 3): "L2CL", (0, 4): "L2CM", (0, 6): "L5I", (0, 7): "L5Q",
    (1, 0): "L1C/A",
    (2, 0): "E1C", (2, 1): "E1B", (2, 3): "E5aI", (2, 4): "E5aQ", (2, 5): "E5bI", (2, 6): "E5bQ",
    (3, 0): "B1I", (3, 1): "B1I", (3, 2): "B2I", (3, 3): "B2I", (3, 5): "B1C", (3, 7): "B2a",
    (5, 0): "L1C/A", (5, 1): "L1S", (5, 4): "L2CM", (5, 5): "L2CL", (5, 8): "L5I", (5, 9): "L5Q",
    (6, 0): "L1OF", (6, 2): "L2OF",
    (7, 0): "L5A",
}
FIX_TYPES = {0: "No fix", 1: "Dead reckoning", 2: "2D", 3: "3D", 4: "GNSS + DR", 5: "Time only"}
CARR_SOLN = {0: "", 1: " RTK float", 2: " RTK fixed"}
ANT_STATUS = {0: "init", 1: "unknown", 2: "OK", 3: "SHORT", 4: "OPEN"}
ANT_POWER = {0: "off", 1: "on", 2: "unknown"}
AGC_ACTIVE_MAX = 40  # % of max gain; below this an active antenna is likely (see antenna_guess)
LINK_WINDOW = 5.0  # seconds of RX byte counts behind the link load figure
LINK_SATURATED = 0.9  # load at which the receiver starts dropping replies
JAM_STATE = {0: "unknown", 1: "OK", 2: "warning", 3: "CRITICAL"}
PORT_ORDER = ["I2C", "UART1", "UART2", "USB", "SPI"]
POLLED = [(ubx.CLS_NAV, 0x07), (ubx.CLS_NAV, 0x04), (ubx.CLS_NAV, 0x35), (ubx.CLS_NAV, 0x43),
          (ubx.CLS_NAV, 0x09), (ubx.CLS_MON, 0x38)]


def fmt_value(kd: ubx.KeyDef, raw: bytes | None, enum_labels: bool = True) -> str:
    if raw is None:
        return ""
    v = ubx.decode(kd, raw)
    if kd.type == "L":
        return "true" if v else "false"
    if kd.type[0] == "X" or not kd.documented:
        return f"0x{v:0{2 * len(raw)}X}" if kd.type[0] == "X" or len(raw) > 2 else str(v)
    if kd.type[0] == "R":
        return f"{v:g}"
    labels = info(kd.name).enum
    if enum_labels and v in labels:
        return f"{v} ({labels[v]})"
    return str(v)


def edit_text(kd: ubx.KeyDef, raw: bytes | None) -> str:
    if raw is None:
        return ""
    v = ubx.decode(kd, raw)
    if kd.type == "L":
        return "true" if v else "false"
    if kd.type[0] == "X":
        return f"0x{v:0{2 * len(raw)}X}"
    return repr(v) if kd.type[0] == "R" else str(v)


def fmt_dist(m: int) -> str:
    return f"{m} m" if m < 1000 else f"{m / 1000:.2f} km"


def link_load(samples, baud: int, min_span: float = 3.0) -> float | None:
    """Fraction of the UART's capacity (8N1: baud / 10 bytes/s) used by received data.

    `samples` are (monotonic time, total RX bytes), oldest first; None until they span `min_span`.
    """
    if len(samples) < 2 or samples[-1][0] - samples[0][0] < min_span:
        return None
    (t0, b0), (t1, b1) = samples[0], samples[-1]
    return (b1 - b0) / (t1 - t0) / (baud / 10)


def antenna_guess(agc_pct: float, lna_mode: int | None, jam: str, tracking: bool) -> tuple[str, str]:
    """(text, style) guessing the antenna type from AGC, for receivers without an antenna supervisor.

    An active antenna's gain makes the receiver turn its own down. Measured on a NEO-F10N in normal
    LNA mode: AGC 7% with an active antenna, 73% unplugged.
    """
    if lna_mode not in (None, 0):
        return "unknown (AGC guess needs normal LNA mode)", ""
    if jam in ("warning", "CRITICAL"):
        return "unknown (AGC unreliable while jammed)", ""
    if agc_pct < AGC_ACTIVE_MAX:
        return "likely active (from AGC)", "green"
    if tracking:
        return "likely passive (from AGC)", "green"
    return "passive or none (from AGC)", "yellow"


def preset_header(path: Path) -> tuple[str, str]:
    """A preset's leading comment block: first line is the title, the rest the description."""
    lines = []
    for line in path.read_text().splitlines():
        if not line.startswith("#"):
            break
        lines.append(line.lstrip("#").strip())
    return (lines[0] if lines else path.stem), " ".join(lines[1:])


def ubx_name(f: bytes) -> str:
    return UBX_MSGIDS.get(bytes(f[2:4]), f"UBX-{f[2]:02X}-{f[3]:02X}")


# --- modal screens -------------------------------------------------------------


class ConfirmScreen(ModalScreen[bool]):
    BINDINGS = [Binding("escape", "dismiss(False)", "Cancel")]

    def __init__(self, title: str, body: str, ok: str = "OK") -> None:
        super().__init__()
        self._title, self._body, self._ok = title, body, ok

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Static(self._body)
            with Horizontal(classes="buttons"):
                yield Button(self._ok, variant="error", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "ok")


class EditScreen(ModalScreen[tuple | None]):
    """Edit one configuration key and choose which layers to write it to."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, kd: ubx.KeyDef, values: dict[int, bytes | None], flash_ok: bool,
                 layers: int) -> None:
        super().__init__()
        self.kd, self.values, self.flash_ok, self.init_layers = kd, values, flash_ok, layers

    def compose(self) -> ComposeResult:
        kd = self.kd
        i = info(kd.name)
        ram = self.values.get(ubx.LAYER_RAM)
        with Vertical(classes="dialog"):
            yield Label(kd.name, classes="dialog-title")
            yield Static(f"Key 0x{kd.key:08X}   type {kd.type}" + (f"   unit {i.unit}" if i.unit else ""))
            if i.desc:
                yield Static(i.desc, classes="desc")
            cur = "   ".join(f"{LAYER_NAMES[l]}: {fmt_value(kd, self.values.get(l)) or '-'}"
                           for l in (ubx.LAYER_RAM, ubx.LAYER_BBR, ubx.LAYER_FLASH, ubx.LAYER_DEFAULT))
            yield Static(cur, classes="current")
            if kd.type == "L":
                yield Select([("true", True), ("false", False)], allow_blank=False,
                             value=bool(ubx.decode(kd, ram)) if ram else False, id="value")
            elif i.enum:
                opts = [(f"{v}  {lbl}", v) for v, lbl in i.enum.items()]
                cur_v = ubx.decode(kd, ram) if ram else next(iter(i.enum))
                if cur_v not in i.enum:
                    opts.append((f"{cur_v}  (current)", cur_v))
                yield Select(opts, allow_blank=False, value=cur_v, id="value")
            else:
                yield Input(edit_text(kd, ram), id="value", placeholder=kd.type)
            if i.danger:
                yield Static(f"⚠ {i.danger}", classes="danger")
            yield Static("Write to:", classes="desc")
            with Horizontal(classes="layers"):
                yield Checkbox("RAM", bool(self.init_layers & ubx.MASK_RAM), id="l_ram")
                yield Checkbox("BBR", bool(self.init_layers & ubx.MASK_BBR), id="l_bbr")
                yield Checkbox("Flash", bool(self.init_layers & ubx.MASK_FLASH) and self.flash_ok,
                               id="l_flash", disabled=not self.flash_ok)
            yield Static("", id="error", classes="danger")
            with Horizontal(classes="buttons"):
                yield Button("Apply", variant="primary", id="apply")
                yield Button("Revert to default", id="revert")
                yield Button("Cancel", id="cancel")

    def _mask(self) -> int:
        m = 0
        if self.query_one("#l_ram", Checkbox).value:
            m |= ubx.MASK_RAM
        if self.query_one("#l_bbr", Checkbox).value:
            m |= ubx.MASK_BBR
        if self.query_one("#l_flash", Checkbox).value:
            m |= ubx.MASK_FLASH
        return m

    @on(Input.Submitted)
    def _submitted(self) -> None:
        self._apply()

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "apply":
            self._apply()
        elif event.button.id == "revert":
            self.dismiss(("revert", None, 0))
        else:
            self.dismiss(None)

    def _apply(self) -> None:
        w = self.query_one("#value")
        mask = self._mask()
        if not mask:
            self.query_one("#error", Static).update("Choose at least one layer.")
            return
        try:
            value = w.value if isinstance(w, Select) else ubx.parse_value(self.kd, w.value)
        except ValueError as e:
            self.query_one("#error", Static).update(f"Invalid value: {e}")
            return
        self.dismiss(("set", value, mask))


class FileScreen(ModalScreen[tuple | None]):
    """Ask for a file path plus export/import options."""

    BINDINGS = [Binding("escape", "dismiss(None)", "Cancel")]

    def __init__(self, mode: str, flash_ok: bool) -> None:
        super().__init__()
        self.mode, self.flash_ok = mode, flash_ok

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            if self.mode == "export":
                yield Label("Export configuration", classes="dialog-title")
                yield Input("ublox-config.txt", id="path")
                yield Checkbox("Only keys that differ from default", True, id="changed")
            else:
                yield Label("Import configuration", classes="dialog-title")
                presets = sorted(PRESET_DIR.glob("*.txt"))
                if presets:
                    yield Select([(preset_header(p)[0], str(p)) for p in presets],
                                 prompt="Presets…", id="preset")
                    yield Static("", id="preset-desc", classes="desc")
                yield Input("ublox-config.txt", id="path")
                yield Static("Write to:", classes="desc")
                with Horizontal(classes="layers"):
                    yield Checkbox("RAM", True, id="l_ram")
                    yield Checkbox("BBR", False, id="l_bbr")
                    yield Checkbox("Flash", False, id="l_flash", disabled=not self.flash_ok)
            with Horizontal(classes="buttons"):
                yield Button("OK", variant="primary", id="ok")
                yield Button("Cancel", id="cancel")

    @on(Select.Changed, "#preset")
    def _preset(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.query_one("#path", Input).value = event.value
            self.query_one("#preset-desc", Static).update(preset_header(Path(event.value))[1])

    @on(Input.Submitted)
    @on(Button.Pressed)
    def _done(self, event) -> None:
        if isinstance(event, Button.Pressed) and event.button.id != "ok":
            self.dismiss(None)
            return
        path = self.query_one("#path", Input).value.strip()
        if self.mode == "export":
            self.dismiss((path, self.query_one("#changed", Checkbox).value))
        else:
            m = 0
            for wid, bit in (("#l_ram", ubx.MASK_RAM), ("#l_bbr", ubx.MASK_BBR), ("#l_flash", ubx.MASK_FLASH)):
                if self.query_one(wid, Checkbox).value:
                    m |= bit
            self.dismiss((path, m))


# --- main app ------------------------------------------------------------------


class UbloxApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "ubxctl"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "reload", "Reload config"),
        Binding("1", "tab('status')", "Status", show=False),
        Binding("2", "tab('sats')", "Satellites", show=False),
        Binding("3", "tab('config')", "Config", show=False),
        Binding("4", "tab('msgs')", "Messages", show=False),
        Binding("5", "tab('console')", "Console", show=False),
        Binding("6", "tab('tools')", "Tools", show=False),
        Binding("p", "toggle_poll", "Pause polling"),
        Binding("o", "reset_trip", "Reset trip"),
    ]

    def __init__(self, device: Device, rxinfo: dict) -> None:
        super().__init__()
        self.dev = device
        self.rxinfo = rxinfo
        self.cfg: dict[int, dict[int, bytes]] = {l: {} for l in LAYER_NAMES}
        # VALGET can't tell "no flash" from "empty flash"; assume it exists until a write is NAKed.
        self.flash_ok = True
        self.edit_layers = ubx.MASK_RAM
        self.group = "*"
        self.inbox: collections.deque = collections.deque(maxlen=5000)
        self.polling = True
        self.pvt = self.dop = self.rf = self.odo = None
        self.sat_rows: list = []
        self.sig: dict[tuple[int, int], list[tuple[str, int]]] = {}
        self.last_pvt = 0.0
        self.link: collections.deque = collections.deque()  # (time, rx_bytes) for link_load
        self.link_baud = device.baud
        self.link_saturated = False
        self.msg_keys: dict[tuple[str, str], int] = {}
        device.add_listener(lambda kind, item: self.inbox.append((kind, item)))

    # --- layout ---------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(initial="status"):
            with TabPane("1 Status", id="status"):
                with Horizontal(id="status-panels"):
                    yield Static(id="pvt", classes="panel")
                    yield Static(id="rf", classes="panel")
                with Horizontal(classes="buttons"):
                    yield Button("Reset trip", id="odo_reset")
            with TabPane("2 Satellites", id="sats"):
                yield DataTable(id="sattable", cursor_type="row", zebra_stripes=True)
            with TabPane("3 Configuration", id="config"):
                with Horizontal():
                    with Vertical(id="cfg-left"):
                        yield OptionList(id="groups")
                        yield Checkbox("Undocumented keys", False, id="undoc")
                        yield Checkbox("Changed only", False, id="changed")
                    with Vertical(id="cfg-right"):
                        yield Input(placeholder="Filter keys (name or description)…", id="filter")
                        yield DataTable(id="cfgtable", cursor_type="row", zebra_stripes=True)
                        yield Static(id="keyhelp")
            with TabPane("4 Messages", id="msgs"):
                yield Static("Output rate per navigation solution (0 = off). Enter on a cell to edit. "
                             "Yellow = differs from default.", classes="hint")
                yield DataTable(id="msgtable", cursor_type="cell", zebra_stripes=True)
            with TabPane("5 Console", id="console"):
                with Horizontal(id="console-opts"):
                    yield Checkbox("NMEA", True, id="c_nmea")
                    yield Checkbox("UBX", True, id="c_ubx")
                    yield Checkbox("TX", False, id="c_tx")
                    yield Checkbox("Polled status msgs", False, id="c_polled")
                    yield Checkbox("Pause", False, id="c_pause")
                yield RichLog(id="log", max_lines=3000, markup=False)
            with TabPane("6 Tools", id="tools"):
                with VerticalScroll():
                    yield Static(id="rxinfo", classes="panel")
                    yield Label("Configuration", classes="section")
                    with Horizontal(classes="buttons"):
                        yield Button("Reload from receiver", id="t_reload")
                        yield Button("Save changes to Flash", id="t_save_flash", variant="primary")
                        yield Button("Save changes to BBR", id="t_save_bbr")
                        yield Button("Clear saved config", id="t_clear", variant="error")
                    with Horizontal(classes="buttons"):
                        yield Button("Export to file", id="t_export")
                        yield Button("Import / presets", id="t_import")
                    yield Label("Receiver", classes="section")
                    with Horizontal(classes="buttons"):
                        yield Button("Hot start", id="t_hot")
                        yield Button("Warm start", id="t_warm")
                        yield Button("Cold start", id="t_cold")
                        yield Button("Reboot", id="t_reboot", variant="warning")
        yield Footer()

    def on_mount(self) -> None:
        self._update_subtitle()
        t = self.query_one("#sattable", DataTable)
        t.add_columns("GNSS", "SV", "C/N0", "", "Elev", "Azim", "Used", "Quality", "Health", "Signals")
        c = self.query_one("#cfgtable", DataTable)
        c.add_column("Key", key="name")
        c.add_column("Type", key="type")
        for l in LAYER_NAMES:
            c.add_column(LAYER_NAMES[l], key=str(l))
        self._render_rxinfo()
        self.set_interval(0.2, self._drain)
        self.set_interval(1.0, self._poll)
        self.set_interval(1.0, self._tick)
        self.load_config()

    def _update_subtitle(self) -> None:
        r = self.rxinfo
        self.sub_title = f"{r.get('model', r['hw'])}  {r.get('fw', r['sw'])}  @ {self.dev.port} {self.dev.baud}"

    def _render_rxinfo(self) -> None:
        r = self.rxinfo
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold")
        t.add_column()
        t.add_row("Model", r.get("model", "?"))
        t.add_row("Firmware", r.get("fw", "?"))
        t.add_row("Protocol", r.get("prot", "?"))
        t.add_row("SW version", r["sw"])
        t.add_row("HW version", r["hw"])
        t.add_row("Extensions", ", ".join(e for e in r["ext"]
                                          if not e.startswith(("MOD=", "FWVER=", "PROTVER="))))
        t.add_row("Port", f"{self.dev.port} @ {self.dev.baud}")
        t.add_row("Flash layer", f"{len(self.cfg[ubx.LAYER_FLASH])} keys stored" if self.flash_ok
                  else "not available")
        t.add_row("BBR layer", f"{len(self.cfg[ubx.LAYER_BBR])} keys stored")
        self.query_one("#rxinfo", Static).update(t)

    def action_tab(self, tab: str) -> None:
        # A focused widget in the old pane would pull the tab straight back.
        self.set_focus(None)
        self.query_one(TabbedContent).active = tab

    def action_toggle_poll(self) -> None:
        self.polling = not self.polling
        self.notify("Status polling " + ("resumed" if self.polling else "paused"))

    @on(Button.Pressed, "#odo_reset")
    def action_reset_trip(self) -> None:
        self.reset_trip()

    @work(thread=True, group="odo")
    def reset_trip(self) -> None:
        # Zeroes the trip distance; the total keeps counting.
        acked = self.dev.send_acked(ubx.frame(ubx.CLS_NAV, 0x10))
        f = self.dev.poll(ubx.CLS_NAV, 0x09)
        o = parse_ubx(f) if f else None
        if o is not None and o.distance == 0:
            self.call_from_thread(self.notify, "Trip reset")
        elif acked is False:
            self.call_from_thread(self.notify, "Receiver rejected the trip reset", severity="error")
        else:
            self.call_from_thread(self.notify, "Trip reset not confirmed", severity="warning")

    # --- live data -------------------------------------------------------------

    def _poll(self) -> None:
        if not self.polling or not self.dev.ser:
            return
        for cls, mid in POLLED:
            try:
                self.dev.write(ubx.poll(cls, mid))
            except Exception:
                return

    def _tick(self) -> None:
        now = time.monotonic()
        if self.dev.baud != self.link_baud:  # samples from the old rate don't apply
            self.link.clear()
            self.link_baud = self.dev.baud
        self.link.append((now, self.dev.rx_bytes))
        while now - self.link[0][0] > LINK_WINDOW:
            self.link.popleft()
        load = link_load(self.link, self.dev.baud)
        saturated = load is not None and load >= LINK_SATURATED
        if saturated and not self.link_saturated:
            self.notify(f"Serial link saturated ({load:.0%} of {self.dev.baud} baud): the receiver is "
                        "dropping replies. Raise CFG-UART1-BAUDRATE or reduce output.",
                        severity="warning", timeout=15)
        self.link_saturated = saturated
        # Also redraws when replies stop arriving, so the link and staleness rows stay current.
        self._render_status()

    def _drain(self) -> None:
        log = self.query_one("#log", RichLog)
        opts = {cid: self.query_one(f"#{cid}", Checkbox).value
                for cid in ("c_nmea", "c_ubx", "c_tx", "c_polled", "c_pause")}
        polled = {bytes(p) for p in POLLED}
        changed = False
        lines = []
        while self.inbox:
            kind, item = self.inbox.popleft()
            if kind == "ERROR":
                self.notify(f"Serial error: {item}", severity="error", timeout=10)
                continue
            if kind == "NMEA":
                if opts["c_nmea"]:
                    lines.append(Text(item))
                continue
            if kind == "TX":
                if opts["c_tx"] and (opts["c_polled"] or bytes(item[2:4]) not in polled):
                    lines.append(Text(f"TX  {ubx_name(item)}  {item[6:-2].hex(' ')}", style="magenta"))
                continue
            ident = bytes(item[2:4])
            if ident in polled:
                changed |= self._handle_status(item)
                if not opts["c_polled"]:
                    continue
            if opts["c_ubx"]:
                style = "red" if ident == b"\x05\x00" else "cyan"
                lines.append(Text(f"RX  {ubx_name(item)}  len={len(item) - 8}", style=style))
        if lines and not opts["c_pause"]:
            for line in lines[-500:]:
                log.write(line)
        if changed:
            self._render_status()

    def _handle_status(self, f: bytes) -> bool:
        p = parse_ubx(f)
        if p is None:
            return False
        ident = p.identity
        if ident == "NAV-PVT":
            self.pvt, self.last_pvt = p, time.monotonic()
        elif ident == "NAV-DOP":
            self.dop = p
        elif ident == "NAV-ODO":
            self.odo = p
        elif ident == "MON-RF":
            self.rf = p
        elif ident == "NAV-SIG":
            sig: dict = collections.defaultdict(list)
            for i in range(1, p.numSigs + 1):
                g, sv = getattr(p, f"gnssId_{i:02d}"), getattr(p, f"svId_{i:02d}")
                cno = getattr(p, f"cno_{i:02d}")
                name = SIGNALS.get((g, getattr(p, f"sigId_{i:02d}")), "?")
                sig[(g, sv)].append((name, cno))
            self.sig = sig
        elif ident == "NAV-SAT":
            rows = []
            for i in range(1, p.numSvs + 1):
                g = lambda a: getattr(p, f"{a}_{i:02d}")  # noqa: E731
                rows.append((g("gnssId"), g("svId"), g("cno"), g("elev"), g("azim"), g("svUsed"),
                             g("qualityInd"), g("health")))
            self.sat_rows = sorted(rows)
            self._render_sats()
            return False
        return True

    def _render_status(self) -> None:
        p, d = self.pvt, self.dop
        t = Table.grid(padding=(0, 2))
        t.add_column(style="bold", min_width=12)
        t.add_column(min_width=28)
        if p:
            fix = FIX_TYPES.get(p.fixType, str(p.fixType)) + CARR_SOLN.get(p.carrSoln, "")
            ok = p.gnssFixOk
            t.add_row("Fix", Text(fix + ("" if ok else "  (not valid)"),
                                  style="bold green" if ok and p.fixType >= 2 else "bold red"))
            t.add_row("Satellites", str(p.numSV))
            t.add_row("UTC", f"{p.year:04d}-{p.month:02d}-{p.day:02d} {p.hour:02d}:{p.min:02d}:{p.second:02d}"
                      + ("" if p.validTime else " (invalid)"))
            t.add_row("Latitude", f"{p.lat:.8f}°")
            t.add_row("Longitude", f"{p.lon:.8f}°")
            t.add_row("Altitude MSL", f"{p.hMSL / 1000:.2f} m")
            t.add_row("Ellipsoid ht", f"{p.height / 1000:.2f} m")
            t.add_row("Horiz. acc.", f"{p.hAcc / 1000:.2f} m")
            t.add_row("Vert. acc.", f"{p.vAcc / 1000:.2f} m")
            t.add_row("Speed", f"{p.gSpeed / 1000:.2f} m/s  ({p.gSpeed * 0.0036:.1f} km/h)")
            t.add_row("Heading", f"{p.headMot:.1f}°")
            t.add_row("Time acc.", f"{p.tAcc} ns")
        else:
            t.add_row("Fix", "waiting for NAV-PVT…")
        if d:
            t.add_row("", "")
            t.add_row("DOP", f"G {d.gDOP:.2f}  P {d.pDOP:.2f}  H {d.hDOP:.2f}  V {d.vDOP:.2f}  T {d.tDOP:.2f}")
        t.add_row("", "")
        use_odo = ubx.lookup("CFG-ODO-USE_ODO")
        odo_on = self.cfg[ubx.LAYER_RAM].get(use_odo.key)
        if odo_on is not None and not ubx.decode(use_odo, odo_on):
            t.add_row("Trip", Text("odometer off (CFG-ODO-USE_ODO)", style="dim"))
        elif self.odo:
            o = self.odo
            t.add_row("Trip", f"{fmt_dist(o.distance)}  ± {fmt_dist(o.distanceStd)}")
            t.add_row("Odometer", fmt_dist(o.totalDistance))
        else:
            t.add_row("Trip", "waiting for NAV-ODO…")
        self.query_one("#pvt", Static).update(t)

        r = Table.grid(padding=(0, 2))
        r.add_column(style="bold", min_width=12)
        r.add_column()
        if self.rf:
            rf = self.rf
            lna = ubx.lookup("CFG-HW-RF_LNA_MODE")
            lna_raw = self.cfg[ubx.LAYER_RAM].get(lna.key)
            lna_mode = ubx.decode(lna, lna_raw) if lna_raw is not None else None
            tracking = any(row[2] for row in self.sat_rows)
            for i in range(1, rf.nBlocks + 1):
                g = lambda a: getattr(rf, f"{a}_{i:02d}")  # noqa: E731
                jam = JAM_STATE.get(g("jammingState"), "?")
                ant = ANT_STATUS.get(g("antStatus"), "?")
                if ant == "unknown":  # no antenna supervisor
                    ant_text = Text(*antenna_guess(g("agcCnt") / 81.91, lna_mode, jam, tracking))
                else:
                    ant_text = Text(f"{ant}, power {ANT_POWER.get(g('antPower'), '?')}",
                                    style="green" if ant == "OK" else "red")
                r.add_row(f"RF block {g('blockId')}", "")
                r.add_row("  Antenna", ant_text)
                r.add_row("  Jamming", Text(f"{jam}  (indicator {g('jamInd')}/255)",
                                            style="red" if jam in ("warning", "CRITICAL") else ""))
                r.add_row("  Noise", f"{g('noisePerMS')} /ms")
                r.add_row("  AGC", f"{g('agcCnt')} ({g('agcCnt') / 81.91:.0f}%)")
        else:
            r.add_row("RF", "waiting for MON-RF…")
        r.add_row("", "")
        r.add_row("Link", f"RX {self.dev.rx_bytes:,} B   TX {self.dev.tx_bytes:,} B")
        load = link_load(self.link, self.dev.baud)
        if load is not None:
            hot = load >= LINK_SATURATED
            r.add_row("  Load", Text(f"{load:.0%} of {self.dev.baud} baud", style="bold red" if hot else ""))
            if hot:
                r.add_row("", Text("Saturated: replies get dropped. Raise CFG-UART1-BAUDRATE or reduce "
                                   "output (e.g. output-nmea-minimal).", style="red"))
        if self.last_pvt and time.monotonic() - self.last_pvt > 3:
            r.add_row("", Text("No NAV-PVT for > 3 s", style="bold red"))
        self.query_one("#rf", Static).update(r)

    def _render_sats(self) -> None:
        t = self.query_one("#sattable", DataTable)
        row = t.cursor_row
        t.clear()
        for g, sv, cno, elev, azim, used, q, health in self.sat_rows:
            bar = Text("█" * (cno // 3), style="green" if used else "yellow" if cno else "dim")
            sigs = "  ".join(f"{n} {c}" for n, c in self.sig.get((g, sv), []) if c)
            t.add_row(GNSS.get(g, str(g)), str(sv), str(cno or ""), bar,
                      str(elev) if elev > -91 else "", str(azim), "✔" if used else "",
                      str(q), {0: "?", 1: "ok", 2: "bad"}.get(health, str(health)), sigs)
        if self.sat_rows:
            t.move_cursor(row=min(row, len(self.sat_rows) - 1))

    # --- configuration -----------------------------------------------------------

    def action_reload(self) -> None:
        self.load_config()

    @work(thread=True, exclusive=True, group="cfgload")
    def load_config(self) -> None:
        self.call_from_thread(self.notify, "Reading configuration…")
        cfg = {}
        for layer in LAYER_NAMES:
            r = self.dev.valget(layer)
            if r is None:
                self.call_from_thread(self.notify, f"No answer reading {LAYER_NAMES[layer]} layer",
                                      severity="error")
                return
            cfg[layer] = r
        self.cfg = cfg
        self.call_from_thread(self._config_loaded)

    def _config_loaded(self) -> None:
        n = len(self.cfg[ubx.LAYER_RAM])
        self.notify(f"Loaded {n} keys "
                    f"({len(self.cfg[ubx.LAYER_BBR])} in BBR, {len(self.cfg[ubx.LAYER_FLASH])} in flash)")
        self._render_rxinfo()
        self._render_groups()
        self._render_cfg()
        self._render_msgs()

    def _visible_keys(self) -> list[ubx.KeyDef]:
        show_undoc = self.query_one("#undoc", Checkbox).value
        only_changed = self.query_one("#changed", Checkbox).value
        flt = self.query_one("#filter", Input).value.strip().upper()
        out = []
        for k in self.cfg[ubx.LAYER_RAM]:
            kd = ubx.keydef(k)
            if not kd.documented and not show_undoc:
                continue
            if self.group != "*" and kd.group != self.group:
                continue
            if only_changed and not self._is_changed(k):
                continue
            if flt and flt not in kd.name and flt not in info(kd.name).desc.upper() \
                    and flt not in f"0X{k:08X}":
                continue
            out.append(kd)
        return sorted(out, key=lambda d: (d.group, not d.documented, d.name))

    def _is_changed(self, key: int) -> bool:
        return (self.cfg[ubx.LAYER_RAM].get(key) != self.cfg[ubx.LAYER_DEFAULT].get(key)
                or key in self.cfg[ubx.LAYER_BBR] or key in self.cfg[ubx.LAYER_FLASH])

    def _render_groups(self) -> None:
        show_undoc = self.query_one("#undoc", Checkbox).value
        counts = collections.Counter(
            kd.group for kd in map(ubx.keydef, self.cfg[ubx.LAYER_RAM]) if kd.documented or show_undoc)
        ol = self.query_one("#groups", OptionList)
        ol.clear_options()
        ol.add_option(Option(f"All ({sum(counts.values())})", id="*"))
        for g in sorted(counts):
            ol.add_option(Option(f"{g} ({counts[g]})", id=g))
        ids = ["*"] + sorted(counts)
        if self.group not in ids:
            self.group = "*"
        ol.highlighted = ids.index(self.group)

    def _cfg_cells(self, kd: ubx.KeyDef) -> list:
        changed = self._is_changed(kd.key)
        name = Text(kd.name, style="bold yellow" if changed else "dim" if not kd.documented else "")
        return [name, kd.type] + [fmt_value(kd, self.cfg[l].get(kd.key)) for l in LAYER_NAMES]

    def _render_cfg(self) -> None:
        t = self.query_one("#cfgtable", DataTable)
        row = t.cursor_row
        t.clear()
        for kd in self._visible_keys():
            t.add_row(*self._cfg_cells(kd), key=str(kd.key))
        if t.row_count:
            t.move_cursor(row=min(row, t.row_count - 1))
        else:
            self.query_one("#keyhelp", Static).update("")

    def _refresh_key_row(self, key: int) -> None:
        t = self.query_one("#cfgtable", DataTable)
        kd = ubx.keydef(key)
        if str(key) in t.rows:
            for col, cell in zip(["name", "type"] + [str(l) for l in LAYER_NAMES], self._cfg_cells(kd)):
                t.update_cell(str(key), col, cell)

    @on(OptionList.OptionHighlighted, "#groups")
    def _group_selected(self, event: OptionList.OptionHighlighted) -> None:
        if event.option.id != self.group:
            self.group = event.option.id
            self._render_cfg()

    @on(Checkbox.Changed, "#undoc")
    def _undoc_changed(self) -> None:
        self._render_groups()
        self._render_cfg()

    @on(Checkbox.Changed, "#changed")
    @on(Input.Changed, "#filter")
    def _filter_changed(self) -> None:
        self._render_cfg()

    @on(DataTable.RowHighlighted, "#cfgtable")
    def _cfg_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        kd = ubx.keydef(int(event.row_key.value))
        i = info(kd.name)
        txt = Text()
        txt.append(kd.name, style="bold")
        txt.append(f"   0x{kd.key:08X}   {kd.type}" + (f"   [{i.unit}]" if i.unit else ""), style="dim")
        if not kd.documented:
            txt.append("\nUndocumented key (not in the u-blox interface description).", style="dim")
        if i.desc:
            txt.append("\n" + i.desc)
        if i.enum:
            txt.append("\n" + ", ".join(f"{v}={lbl}" for v, lbl in i.enum.items()), style="cyan")
        if i.danger:
            txt.append(f"\n⚠ {i.danger}", style="bold red")
        self.query_one("#keyhelp", Static).update(txt)

    @on(DataTable.RowSelected, "#cfgtable")
    def _cfg_selected(self, event: DataTable.RowSelected) -> None:
        self.open_editor(int(event.row_key.value))

    def open_editor(self, key: int) -> None:
        kd = ubx.keydef(key)
        values = {l: self.cfg[l].get(key) for l in LAYER_NAMES}

        def done(result) -> None:
            if not result:
                return
            action, value, mask = result
            if action == "set":
                self.edit_layers = mask
                self.apply_value(key, value, mask)
            else:
                self.revert_key(key)

        self.push_screen(EditScreen(kd, values, self.flash_ok, self.edit_layers), done)

    @work(thread=True, group="cfgwrite")
    def apply_value(self, key: int, value, mask: int) -> None:
        kd = ubx.keydef(key)
        if kd.name == "CFG-UART1-BAUDRATE" and mask & ubx.MASK_RAM:
            if value not in BAUD_CANDIDATES:
                self.call_from_thread(self.notify, f"Unsupported baud rate {value}", severity="error")
                return
            ok = self.dev.set_receiver_baud(int(value), mask)
            if ok:
                self.call_from_thread(self._update_subtitle)
                self.call_from_thread(self._render_rxinfo)
        else:
            ok = self.dev.valset(mask, [(key, ubx.encode(kd, value))])
        if not ok and mask & ubx.MASK_FLASH:
            self._check_flash()
        if ok:
            self.call_from_thread(self.notify, f"{kd.name} = {value} → "
                                  + "+".join(n for b, n in ((1, "RAM"), (2, "BBR"), (4, "Flash")) if mask & b))
        else:
            self.call_from_thread(self.notify, f"Receiver rejected {kd.name} = {value}", severity="error")
        self._reread([key])

    @work(thread=True, group="cfgwrite")
    def revert_key(self, key: int) -> None:
        kd = ubx.keydef(key)
        default = self.cfg[ubx.LAYER_DEFAULT].get(key)
        layers = ubx.MASK_BBR | (ubx.MASK_FLASH if self.flash_ok else 0)
        ok = self.dev.valdel(layers, [key])
        if default is not None:
            if kd.name == "CFG-UART1-BAUDRATE":
                ok = self.dev.set_receiver_baud(ubx.decode(kd, default), ubx.MASK_RAM) and ok
                self.call_from_thread(self._update_subtitle)
            else:
                ok = self.dev.valset(ubx.MASK_RAM, [(key, default)]) and ok
        self.call_from_thread(self.notify, f"{kd.name} reverted to default" if ok
                              else f"Revert of {kd.name} failed", severity="information" if ok else "error")
        self._reread([key])

    def _check_flash(self) -> None:
        """After a NAKed write that included flash: see whether flash is the problem (worker thread)."""
        kd = ubx.lookup("CFG-RATE-MEAS")
        cur = self.cfg[ubx.LAYER_FLASH].get(kd.key)
        if cur is None:
            return  # nothing safe to rewrite without changing what's stored
        if self.dev.send_acked(ubx.valset(ubx.MASK_FLASH, [(kd.key, cur)])) is False:
            self.flash_ok = False
            self.call_from_thread(self.notify, "Receiver rejects flash writes; flash disabled",
                                  severity="warning")
            self.call_from_thread(self._render_rxinfo)

    def _reread(self, keys: list[int]) -> None:
        """Re-read keys from RAM/BBR/Flash (worker thread) and refresh the views."""
        for layer in (ubx.LAYER_RAM, ubx.LAYER_BBR, ubx.LAYER_FLASH):
            r = self.dev.valget(layer, keys)
            if r is None:
                continue
            for k in keys:
                if k in r:
                    self.cfg[layer][k] = r[k]
                else:
                    self.cfg[layer].pop(k, None)

        def refresh() -> None:
            for k in keys:
                self._refresh_key_row(k)
            self._render_msgs()
        self.call_from_thread(refresh)

    # --- messages matrix -------------------------------------------------------

    def _render_msgs(self) -> None:
        t = self.query_one("#msgtable", DataTable)
        keys: dict[tuple[str, str], int] = {}
        for k in self.cfg[ubx.LAYER_RAM]:
            kd = ubx.keydef(k)
            if kd.group == "MSGOUT" and kd.documented:
                msg, _, port = kd.name.split("-", 2)[2].rpartition("_")
                keys[(msg, port)] = k
        self.msg_keys = keys
        ports = [p for p in PORT_ORDER if any(port == p for _, port in keys)]
        msgs = sorted({m for m, _ in keys}, key=lambda m: (not m.startswith("NMEA"), m))
        cur = t.cursor_coordinate
        t.clear(columns=True)
        t.add_column("Message", key="msg")
        for p in ports:
            t.add_column(p, key=p)
        for m in msgs:
            label = m.replace("NMEA_ID_", "NMEA ").replace("NMEA_NAV2_ID_", "NMEA NAV2 ") \
                     .replace("PUBX_ID_", "PUBX ").replace("UBX_", "UBX ").replace("_", "-")
            cells: list = [label]
            for p in ports:
                k = keys.get((m, p))
                if k is None:
                    cells.append("")
                    continue
                v = self.cfg[ubx.LAYER_RAM][k][0]
                changed = self._is_changed(k)
                cells.append(Text(str(v), style="bold yellow" if changed else "bold green" if v else "dim"))
            t.add_row(*cells, key=m)
        if t.row_count:
            t.move_cursor(row=min(cur.row, t.row_count - 1), column=max(1, min(cur.column, len(ports))))

    @on(DataTable.CellSelected, "#msgtable")
    def _msg_selected(self, event: DataTable.CellSelected) -> None:
        k = self.msg_keys.get((event.cell_key.row_key.value, event.cell_key.column_key.value))
        if k is not None:
            self.open_editor(k)

    # --- tools -------------------------------------------------------------------

    @on(Button.Pressed, "#t_reload")
    def _t_reload(self) -> None:
        self.load_config()

    @on(Button.Pressed, "#t_save_flash, #t_save_bbr")
    def _t_save(self, event: Button.Pressed) -> None:
        to_flash = event.button.id == "t_save_flash"
        if to_flash and not self.flash_ok:
            self.notify("This receiver has no flash layer", severity="error")
            return
        items = [(k, v) for k, v in self.cfg[ubx.LAYER_RAM].items()
                 if ubx.keydef(k).documented and v != self.cfg[ubx.LAYER_DEFAULT].get(k)]
        if not items:
            self.notify("RAM matches defaults; nothing to save")
            return
        where = "flash" if to_flash else "battery-backed RAM"
        names = "\n".join(f"  {ubx.keydef(k).name} = {fmt_value(ubx.keydef(k), v)}" for k, v in items[:20])
        more = f"\n  … and {len(items) - 20} more" if len(items) > 20 else ""
        mask = ubx.MASK_FLASH if to_flash else ubx.MASK_BBR

        def go(ok: bool) -> None:
            if ok:
                self._write_items(items, mask, f"Saved {len(items)} keys to {where}")
        self.push_screen(ConfirmScreen(f"Save to {where}",
                                       f"Write the {len(items)} keys that differ from default:\n{names}{more}",
                                       "Save"), go)

    @work(thread=True, group="cfgwrite")
    def _write_items(self, items: list[tuple[int, bytes]], mask: int, msg: str) -> None:
        ok = self.dev.valset(mask, items)
        self.call_from_thread(self.notify, msg if ok else "Receiver rejected the write",
                              severity="information" if ok else "error")
        self._reread([k for k, _ in items])

    @on(Button.Pressed, "#t_clear")
    def _t_clear(self) -> None:
        def go(ok: bool) -> None:
            if ok:
                self._clear_saved()
        self.push_screen(ConfirmScreen(
            "Clear saved configuration",
            "Delete every key stored in BBR" + (" and flash" if self.flash_ok else "") + ".\n"
            "RAM keeps its current values until the receiver is rebooted; after a reboot it "
            "starts with factory defaults (UART1 at 38400 baud — the TUI will look for it).",
            "Clear"), go)

    @work(thread=True, group="cfgwrite")
    def _clear_saved(self) -> None:
        layers = ubx.MASK_BBR | (ubx.MASK_FLASH if self.flash_ok else 0)
        ok = self.dev.valdel(layers, [ubx.WILDCARD_ALL])
        self.call_from_thread(self.notify, "Saved configuration cleared" if ok else "Clear failed",
                              severity="information" if ok else "error")
        self.load_config()

    @on(Button.Pressed, "#t_hot, #t_warm, #t_cold")
    def _t_start(self, event: Button.Pressed) -> None:
        mask, name = {"t_hot": (ubx.HOT_START, "Hot"), "t_warm": (ubx.WARM_START, "Warm"),
                      "t_cold": (ubx.COLD_START, "Cold")}[event.button.id]
        self.dev.reset(mask, ubx.RST_GNSS)
        self.pvt = None
        self.notify(f"{name} start sent (GNSS restart, configuration kept)")

    @on(Button.Pressed, "#t_reboot")
    def _t_reboot(self) -> None:
        def go(ok: bool) -> None:
            if ok:
                self._reboot()
        self.push_screen(ConfirmScreen("Reboot receiver",
                                       "Software reset. RAM configuration is reloaded from flash/BBR/defaults; "
                                       "unsaved RAM changes are lost.", "Reboot"), go)

    @work(thread=True, exclusive=True, group="cfgload")
    def _reboot(self) -> None:
        self.dev.reset(ubx.HOT_START, ubx.RST_SW)
        time.sleep(1.5)
        if not self.dev.mon_ver(timeout=1.0):
            # Saved config may use a different baud rate; go looking for it.
            for baud in BAUD_CANDIDATES:
                self.dev.set_local_baud(baud)
                if self.dev.mon_ver(timeout=0.6):
                    break
            else:
                self.call_from_thread(self.notify, "Receiver not answering after reboot", severity="error")
                return
        self.call_from_thread(self._update_subtitle)
        self.call_from_thread(self.notify, f"Receiver back at {self.dev.baud} baud")
        self.load_config()

    @on(Button.Pressed, "#t_export")
    def _t_export(self) -> None:
        def go(res) -> None:
            if not res:
                return
            path, changed_only = res
            lines = [f"# u-blox configuration exported {time.strftime('%Y-%m-%d %H:%M:%S')}",
                     f"# {self.rxinfo.get('model', '')} {self.rxinfo.get('fw', '')}", ""]
            n = 0
            for k, v in sorted(self.cfg[ubx.LAYER_RAM].items()):
                kd = ubx.keydef(k)
                if not kd.documented or (changed_only and v == self.cfg[ubx.LAYER_DEFAULT].get(k)):
                    continue
                lines.append(f"{kd.name} {edit_text(kd, v)}")
                n += 1
            try:
                Path(path).write_text("\n".join(lines) + "\n")
            except OSError as e:
                self.notify(f"Export failed: {e}", severity="error")
                return
            self.notify(f"Exported {n} keys to {Path(path).resolve()}")
        self.push_screen(FileScreen("export", self.flash_ok), go)

    @on(Button.Pressed, "#t_import")
    def _t_import(self) -> None:
        def go(res) -> None:
            if not res:
                return
            path, mask = res
            if not mask:
                self.notify("No layer selected", severity="error")
                return
            try:
                text = Path(path).read_text()
            except OSError as e:
                self.notify(f"Import failed: {e}", severity="error")
                return
            items, errors = [], []
            for n, line in enumerate(text.splitlines(), 1):
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                try:
                    name, val = line.split(None, 1)
                    kd = ubx.lookup(name)
                    if kd is None:
                        raise ValueError(f"unknown key {name}")
                    value = ubx.parse_value(kd, val)
                    if kd.name == "CFG-UART1-BAUDRATE" and mask & ubx.MASK_RAM:
                        if value == self.dev.baud:
                            continue
                        raise ValueError("change the baud rate from the Configuration tab")
                    items.append((kd.key, ubx.encode(kd, value)))
                except ValueError as e:
                    errors.append(f"line {n}: {e}")
            if errors:
                self.notify("\n".join(errors[:5]), severity="warning", timeout=10)
            if items:
                self._write_items(items, mask, f"Imported {len(items)} keys from {path}")
        self.push_screen(FileScreen("import", self.flash_ok), go)


def run(port: str, baud: int | None) -> None:
    stopped = portcheck.free_port(port)
    try:
        _run(port, baud)
    finally:
        portcheck.restart_gpsd(stopped)


def _run(port: str, baud: int | None) -> None:
    if baud is None:
        print(f"Looking for receiver on {port}…")
        try:
            baud = detect_baud(port, (921600,) + BAUD_CANDIDATES)
        except NoUbxReply as e:
            raise SystemExit(f"{port}: {e}")
        if baud is None:
            raise SystemExit(f"No UBX response on {port} at any of {BAUD_CANDIDATES}")
    dev = Device(port, baud)
    dev.open()
    rxinfo = None
    for _ in range(3):  # a UART saturated with output can drop the reply
        rxinfo = dev.mon_ver()
        if rxinfo:
            break
    if rxinfo is None:
        dev.close()
        raise SystemExit(f"No UBX response on {port} at {baud} baud")
    try:
        UbloxApp(dev, rxinfo).run()
    finally:
        dev.close()
