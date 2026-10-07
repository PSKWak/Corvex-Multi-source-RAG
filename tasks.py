"""Cross-platform task runner (works where `make` isn't available).

    python tasks.py ingest
    python tasks.py ask "What is the default retention period?"
    python tasks.py eval [--sample 8] [--skip-ragas]
    python tasks.py ablate --axis retrieval
    python tasks.py test
    python tasks.py doctor
"""
from __future__ import annotations

import subprocess
import sys


def _run(args: list[str]) -> int:
    return subprocess.call([sys.executable, "-m", "corvex_rag.cli", *args])


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "test":
        return subprocess.call([sys.executable, "-m", "pytest"])
    return _run([cmd, *rest])


if __name__ == "__main__":
    raise SystemExit(main())
