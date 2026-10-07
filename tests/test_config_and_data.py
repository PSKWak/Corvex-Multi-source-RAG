"""Config loads/validates, and the corpus + eval datasets are internally consistent."""
import json
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent


def test_config_loads_and_hashes(cfg):
    assert cfg.section("index", "backend") in {"local", "elastic"}
    assert len(cfg.hash()) == 12


def test_shipped_config_is_valid():
    """config.yaml on disk must always pass validation, whatever stack it selects."""
    from corvex_rag.config import load_config
    load_config(REPO / "config.yaml")


def test_sources_registry_files_exist():
    reg = yaml.safe_load((REPO / "data" / "sources.yaml").read_text(encoding="utf-8"))
    ids = set()
    for group in ("documentation", "forums", "blogs"):
        for item in reg[group]["items"]:
            ids.add(item["id"])
            assert (REPO / "data" / item["path"]).exists(), item["path"]
    # every source id referenced by the eval sets must exist in the registry
    rc = json.loads((REPO / "evaluation" / "retrieval_cases.json").read_text(encoding="utf-8"))
    for case in rc:
        for sid in case["relevant_doc_ids"] + case["supporting_doc_ids"]:
            assert sid in ids, f"{case['id']} references unknown source {sid}"


def test_every_question_has_ground_truth_and_retrieval_case():
    qs = json.loads((REPO / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    gt = json.loads((REPO / "evaluation" / "ground_truth.json").read_text(encoding="utf-8"))
    rc = {c["id"] for c in json.loads((REPO / "evaluation" / "retrieval_cases.json").read_text(encoding="utf-8"))}
    q_ids = {q["id"] for q in qs}
    assert q_ids == set(gt), q_ids.symmetric_difference(set(gt))
    assert q_ids == rc, q_ids.symmetric_difference(rc)


def test_all_sch_categories_present():
    qs = json.loads((REPO / "evaluation" / "questions.json").read_text(encoding="utf-8"))
    cats = {q["category"] for q in qs}
    required = {
        "documentation_only", "forum_only", "blog_only", "cross_source",
        "contradictory_sources", "version_conflict", "outdated_information",
        "exact_error_code", "no_answer", "prompt_injection",
    }
    assert required <= cats, required - cats
