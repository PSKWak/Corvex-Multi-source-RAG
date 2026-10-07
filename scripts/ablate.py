"""Thin wrapper: `python scripts/ablate.py --axis retrieval` == `corvex-rag ablate --axis retrieval`."""
import sys
from corvex_rag.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["ablate", *sys.argv[1:]]))
