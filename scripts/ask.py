"""Thin wrapper: `python scripts/ask.py "question"` == `corvex-rag ask "question"`."""
import sys
from corvex_rag.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["ask", *sys.argv[1:]]))
