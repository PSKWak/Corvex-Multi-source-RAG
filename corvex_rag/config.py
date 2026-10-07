"""Load, validate, and hash `config.yaml`. Fully implemented — the config surface is part
of the design under review."""
from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"


@dc.dataclass(slots=True)
class Config:
    raw: dict[str, Any]
    path: Path

    # -- convenience accessors (kept thin; stages read `cfg.section(...)`) --
    def section(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    @property
    def seed(self) -> int:
        return int(self.raw.get("seed", 0))

    @property
    def logs_dir(self) -> Path:
        return self._resolve(self.section("paths", "logs_dir", default="logs"))

    @property
    def data_dir(self) -> Path:
        return self._resolve(self.section("paths", "data_dir", default="data"))

    @property
    def index_dir(self) -> Path:
        return self._resolve(self.section("paths", "index_dir", default=".index"))

    @property
    def model_cache_dir(self) -> Path:
        return self._resolve(self.section("paths", "model_cache_dir", default=".models"))

    @property
    def evaluation_dir(self) -> Path:
        return self._resolve(self.section("paths", "evaluation_dir", default="evaluation"))

    def _resolve(self, p: str | os.PathLike) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (REPO_ROOT / p)

    def hash(self) -> str:
        """Stable hash of the resolved config, stamped into every provenance record."""
        blob = json.dumps(self.raw, sort_keys=True, default=str).encode()
        return hashlib.sha1(blob).hexdigest()[:12]

    def require_env(self, env_var: str) -> str:
        val = os.environ.get(env_var, "")
        if not val:
            raise RuntimeError(
                f"Config expects environment variable {env_var!r} but it is unset. "
                f"Copy .env.example to .env and fill it in, or switch the relevant "
                f"component to its offline default (local / hashing / mock)."
            )
        return val


def load_config(path: str | os.PathLike | None = None) -> Config:
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env")
    except ImportError:
        pass
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not path.exists():
        raise FileNotFoundError(f"config not found: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = Config(raw=raw, path=path)
    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    """Cheap sanity checks — fail fast on obviously broken config.

    TODO: replace with a pydantic model once the surface stabilises.
    """
    backend = cfg.section("index", "backend")
    if backend not in {"local", "elastic"}:
        raise ValueError(f"index.backend must be local|elastic, got {backend!r}")
    if cfg.section("embedding", "provider") not in {"hashing", "bge"}:
        raise ValueError("embedding.provider must be hashing|bge")
    if cfg.section("reranker", "provider") not in {"bge", "cohere", "identity", "lexical"}:
        raise ValueError("reranker.provider must be bge|cohere|identity|lexical")
    if cfg.section("generation", "llm") not in {"mock", "groq", "gemini", "anthropic", "openai"}:
        raise ValueError("generation.llm must be mock|groq|gemini|anthropic|openai")
    if cfg.section("source_weighting", "mode") not in {"off", "static", "adaptive"}:
        raise ValueError("source_weighting.mode must be off|static|adaptive")
