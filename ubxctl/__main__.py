import argparse

from .app import run


def main() -> None:
    ap = argparse.ArgumentParser(prog="ubxctl", description="Configure a u-blox GNSS receiver (unofficial)")
    ap.add_argument("-p", "--port", default="/dev/ttyUSB0", help="serial port (default: /dev/ttyUSB0)")
    ap.add_argument("-b", "--baud", type=int, help="baud rate (default: auto-detect)")
    args = ap.parse_args()
    run(args.port, args.baud)


if __name__ == "__main__":
    main()
