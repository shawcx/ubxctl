"""Make sure nothing else (mainly gpsd) is reading the serial port.

Linux doesn't give a process exclusive use of a tty: if gpsd also has the port
open, each byte goes to whichever process reads first, so both see a broken
stream, and gpsd may also send its own configuration commands.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import socket
import subprocess
import time
from dataclasses import dataclass

GPSD_UNITS = ("gpsd.socket", "gpsd.service")
GPSD_DEFAULTS = "/etc/default/gpsd"


@dataclass
class Proc:
    pid: int
    name: str
    uid: int
    cmdline: list[str]

    def __str__(self) -> str:
        return f"{self.name} (pid {self.pid}): {' '.join(self.cmdline)}"


def _procs() -> list[Proc]:
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open(f"/proc/{d}/comm") as f:
                name = f.read().strip()
            with open(f"/proc/{d}/cmdline", "rb") as f:
                cmd = [a.decode(errors="replace") for a in f.read().split(b"\0") if a]
            uid = os.stat(f"/proc/{d}").st_uid
        except OSError:
            continue
        out.append(Proc(int(d), name, uid, cmd))
    return out


def _alive(pid: int) -> bool:
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"  # zombie = already exited
    except (OSError, IndexError):
        return False


def holders(real: str, procs: list[Proc]) -> list[Proc]:
    """Processes with the device open. Other users' fd tables aren't readable, so
    this only finds our own processes (and everything when run as root)."""
    out = []
    for p in procs:
        if p.pid == os.getpid():
            continue
        try:
            fds = os.listdir(f"/proc/{p.pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(f"/proc/{p.pid}/fd/{fd}") == real:
                    out.append(p)
                    break
            except OSError:
                pass
    return out


def _resolves_to(paths, real: str) -> bool:
    return any(os.path.realpath(p) == real for p in paths if p.startswith("/"))


def _gpsd_port(p: Proc) -> int:
    for i, a in enumerate(p.cmdline):
        if a == "-S" and i + 1 < len(p.cmdline):
            return int(p.cmdline[i + 1])
        if a.startswith("-S") and a[2:].isdigit():
            return int(a[2:])
    return 2947


def gpsd_devices(p: Proc) -> list[str] | None:
    """Ask a running gpsd which devices it has open; None if it doesn't answer."""
    try:
        with socket.create_connection(("127.0.0.1", _gpsd_port(p)), timeout=1.0) as s:
            s.sendall(b"?DEVICES;\n")
            buf = b""
            deadline = time.monotonic() + 1.5
            while time.monotonic() < deadline:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
                for line in buf.split(b"\n"):
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("class") == "DEVICES":
                        return [d.get("path", "") for d in msg.get("devices", [])]
    except (OSError, ValueError):
        pass
    return None


def _configured_devices() -> list[str]:
    """DEVICES from /etc/default/gpsd, which gpsd opens when it (re)starts."""
    try:
        with open(GPSD_DEFAULTS) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DEVICES="):
                    return shlex.split(line)[0][len("DEVICES="):].split()
    except (OSError, ValueError):
        pass
    return []


def _in_systemd_unit(p: Proc) -> bool:
    try:
        with open(f"/proc/{p.pid}/cgroup") as f:
            return "gpsd.service" in f.read()
    except OSError:
        return False


def _unit_active(unit: str) -> bool:
    try:
        return subprocess.run(["systemctl", "is-active", "--quiet", unit]).returncode == 0
    except FileNotFoundError:
        return False


def _systemctl(action: str, units: list[str]) -> bool:
    cmd = ["systemctl", action, *units]
    if os.geteuid() != 0:
        cmd = ["sudo", *cmd]
    print("+ " + " ".join(cmd))
    try:
        return subprocess.run(cmd).returncode == 0
    except FileNotFoundError:
        return False


def free_port(port: str) -> list[str]:
    """Stop gpsd if it is (or could start) using `port`.

    Returns the systemd units that were stopped, for restart_gpsd(). Exits if
    another program holds the port or gpsd can't be stopped.
    """
    real = os.path.realpath(port)
    if not os.path.exists(real):
        return []
    procs = _procs()

    others = [p for p in holders(real, procs) if p.name != "gpsd"]
    if others:
        raise SystemExit(f"{port} is already open by:\n  " + "\n  ".join(map(str, others))
                         + "\nClose it first; two programs reading one serial port corrupt each other's data.")

    users = []
    for p in (p for p in procs if p.name == "gpsd"):
        devices = gpsd_devices(p)
        if (devices is not None and _resolves_to(devices, real)) or _resolves_to(p.cmdline, real) \
                or p in holders(real, [p]):
            users.append(p)
    # A socket-activated gpsd that isn't running yet would open its configured
    # devices as soon as any client connects.
    socket_risk = not users and _unit_active("gpsd.socket") and _resolves_to(_configured_devices(), real)
    if not users and not socket_risk:
        return []

    stopped: list[str] = []
    # Only touch systemd's gpsd if it's the one in the way, not e.g. a hand-started copy.
    units = [u for u in GPSD_UNITS if _unit_active(u)] \
        if socket_risk or any(map(_in_systemd_unit, users)) else []
    if units:
        why = "is using" if users else "is set to open"
        print(f"gpsd {why} {port}; stopping it until you quit.")
        if _systemctl("stop", units):
            stopped = units
        else:
            print("Couldn't stop gpsd via systemctl.")

    for p in users:
        if _alive(p.pid) and p.uid == os.getuid():
            print(f"Stopping gpsd (pid {p.pid}), which is using {port}.")
            os.kill(p.pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and any(_alive(p.pid) for p in users):
        time.sleep(0.1)

    left = [p for p in users if _alive(p.pid)]
    if left:
        restart_gpsd(stopped)
        raise SystemExit(f"gpsd is still using {port}:\n  " + "\n  ".join(map(str, left))
                         + f"\nStop it with: sudo kill {' '.join(str(p.pid) for p in left)}")
    return stopped


def restart_gpsd(units: list[str]) -> None:
    if units:
        print("Restarting gpsd…")
        if not _systemctl("start", units):
            print("Couldn't restart gpsd; run: sudo systemctl start " + " ".join(units))
