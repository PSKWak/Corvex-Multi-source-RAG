import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _offline(c, tmp_path):
    """Force the zero-dependency stack + tmp writable paths, regardless of what the
    shipped config.yaml currently selects."""
    c.raw["paths"]["logs_dir"] = str(tmp_path / "logs")
    c.raw["paths"]["index_dir"] = str(tmp_path / "index")
    c.raw["index"]["backend"] = "local"
    c.raw["embedding"]["provider"] = "hashing"
    c.raw["reranker"]["provider"] = "lexical"
    c.raw["generation"]["llm"] = "mock"
    c.raw["contradiction"]["nli"]["enabled"] = False
    c.raw["contradiction"]["llm_adjudication"] = False
    return c


@pytest.fixture
def cfg(tmp_path):
    from corvex_rag.config import load_config
    return _offline(load_config(REPO_ROOT / "config.yaml"), tmp_path)


@pytest.fixture
def offline_cfg(cfg):
    return cfg
