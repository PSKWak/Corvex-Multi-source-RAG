"""Thin wrapper: `python scripts/ingest.py` == `corvex-rag ingest`."""
import sys
from corvex_rag.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["ingest", *sys.argv[1:]]))
