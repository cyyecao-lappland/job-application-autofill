"""Compatibility entry point: only v2 browser-evidence runs can be finalized."""
import sys
from screening_run import main

if __name__ == "__main__":
    raise SystemExit(main(["advance", *sys.argv[1:]]))
