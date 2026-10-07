"""Thin wrapper: `python scripts/evaluate.py [--sample N] [--skip-ragas]` == `corvex-rag eval`."""
import sys
from corvex_rag.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["eval", *sys.argv[1:]]))
