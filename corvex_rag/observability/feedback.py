"""Feedback store (DESIGN.md §10). JSONL by default; SQLite adapter optional."""
from __future__ import annotations

import abc
import json
from pathlib import Path

from ..schema import Feedback


class FeedbackStore(abc.ABC):
    @abc.abstractmethod
    def record(self, fb: Feedback) -> None: ...
    @abc.abstractmethod
    def all(self) -> list[Feedback]: ...


class JsonlFeedbackStore(FeedbackStore):
    def __init__(self, logs_dir: Path) -> None:
        self.path = logs_dir / "feedback.jsonl"
        self.candidates_path = logs_dir / "eval_candidates.jsonl"

    def record(self, fb: Feedback) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "ts": fb.ts.isoformat(), "query_id": fb.query_id, "rating": fb.rating,
            "note": fb.note, "better_answer": fb.better_answer,
        }
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        if fb.rating == "down":
            with self.candidates_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"query_id": fb.query_id, "note": fb.note, "ts": fb.ts.isoformat()}) + "\n")

    def all(self) -> list[Feedback]:
        if not self.path.exists():
            return []
        out: list[Feedback] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            out.append(Feedback(query_id=d["query_id"], rating=d["rating"],
                                note=d.get("note", ""), better_answer=d.get("better_answer")))
        return out


def get_feedback_store(cfg) -> FeedbackStore:
    return JsonlFeedbackStore(cfg.logs_dir)
