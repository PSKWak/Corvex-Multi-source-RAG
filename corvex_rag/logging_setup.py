"""Structured logging + the JSONL query log (satisfies sch.md requirement 6).

`logs/queries.jsonl` — one line per answered query with the sources used, contradiction
count, self-check result, correction count, latency, tokens, and cost estimate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog


_CONFIGURED = False


def configure(level: str = "INFO", jsonl: bool = True) -> None:
    """Configure structlog once at process start. `jsonl=True` → JSON lines to stderr
    (machine-readable); `jsonl=False` → coloured console output for interactive use."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    import logging

    renderer = structlog.processors.JSONRenderer() if jsonl else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )
    _CONFIGURED = True


def get_logger(name: str) -> "structlog.BoundLogger":
    return structlog.get_logger(name)


def append_query_log(logs_dir: Path, record: dict[str, Any]) -> None:
    """Append one line to logs/queries.jsonl.

    Expected keys: query_id, question, sources_used, n_contradictions, self_check_result,
    n_corrections, latency_ms, tokens, cost_estimate, confidence.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with (logs_dir / "queries.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
