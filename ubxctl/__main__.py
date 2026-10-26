import argparse

from .app import run
from .device import find_port


def main() -> None:
    ap = argparse.ArgumentParser(prog="ubxctl", description="Configure a u-blox GNSS receiver (unofficial)")
    ap.add_argument("-p", "--port", help="serial port (default: first u-blox or USB-serial adapter found)")
    ap.add_argument("-b", "--baud", type=int, help="baud rate (default: auto-detect)")
    args = ap.parse_args()
    port = args.port or find_port()
    if port is None:
        raise SystemExit("No USB serial port found; pass one with -p")
    run(port, args.baud)


if __name__ == "__main__":
    main()
