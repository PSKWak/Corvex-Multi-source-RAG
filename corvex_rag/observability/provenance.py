"""One JSON provenance record per query — the full, replayable trace (DESIGN.md §10)."""
from __future__ import annotations

import dataclasses as dc
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dc.dataclass
class ProvenanceRecorder:
    logs_dir: Path
    config_hash: str
    query_id: str = dc.field(default_factory=lambda: uuid.uuid4().hex[:12])
    record: dict[str, Any] = dc.field(default_factory=dict)

    def __post_init__(self) -> None:
        self.record.update(
            query_id=self.query_id,
            ts=datetime.now(timezone.utc).isoformat(),
            config_hash=self.config_hash,
            stages={},
        )

    def stage(self, name: str, **payload: Any) -> None:
        """Attach a stage's inputs/outputs/timing. Called by every pipeline stage.
        Large objects (chunks) are stored as {chunk_id, source_type, score, components}."""
        self.record["stages"][name] = payload

    def set(self, **top_level: Any) -> None:
        self.record.update(top_level)

    def write(self) -> Path:
        out_dir = self.logs_dir / "provenance"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{self.query_id}.json"
        path.write_text(json.dumps(self.record, indent=2, default=str), encoding="utf-8")
        return path

    # convenience for the logs/queries.jsonl line
    def summary(self) -> dict[str, Any]:
        r = self.record
        ans = r.get("answer", {})
        return {
            "query_id": self.query_id,
            "question": r.get("question", ""),
            "sources_used": ans.get("sources_used", {}),
            "n_contradictions": len(r.get("stages", {}).get("contradiction", {}).get("items", [])),
            "self_check_result": "PASS" if ans.get("self_check_passed") else "FAIL",
            "n_corrections": ans.get("n_corrections", 0),
            "latency_ms": r.get("latency_ms", 0.0),
            "tokens": r.get("tokens", {}),
            "cost_estimate_usd": r.get("cost_estimate_usd", 0.0),
            "confidence": ans.get("confidence", 0.0),
            "llm_provider": r.get("stages", {}).get("generation", {}).get("provider", ""),
            "config_hash": r.get("config_hash", ""),
        }
