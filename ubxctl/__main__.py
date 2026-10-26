import argparse
import glob
import sys

from .app import run


def default_port() -> str:
    if sys.platform == "darwin":
        ports = sorted(glob.glob("/dev/cu.usbserial-*"))
        if ports:
            return ports[0]
    return "/dev/ttyUSB0"


def main() -> None:
    ap = argparse.ArgumentParser(prog="ubxctl", description="Configure a u-blox GNSS receiver (unofficial)")
    ap.add_argument("-p", "--port", default=default_port(),
                    help="serial port (default: /dev/ttyUSB0, first /dev/cu.usbserial-* on macOS)")
    ap.add_argument("-b", "--baud", type=int, help="baud rate (default: auto-detect)")
    args = ap.parse_args()
    run(args.port, args.baud)


if __name__ == "__main__":
    main()
