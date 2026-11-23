#!/usr/bin/env python3
"""Fail-closed validation of the prepared 43-record secondary-QC package."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
BUILDER = HERE / "prepare_stage1_secondary_qc_human_review.py"
QUEUE = EVAL / "stage1_secondary_qc_review_queue_immutable.jsonl"
REVIEW = EVAL / "stage1_secondary_qc_human_review.md"
WORKBOOK = EVAL / "stage1_secondary_qc_human_review_workbook.csv"
SUMMARY = EVAL / "stage1_secondary_qc_review_summary.md"
MANIFEST = EVAL / "stage1_secondary_qc_review_manifest.json"
REPORT = EVAL / "stage1_secondary_qc_review_validation.json"
HUMAN_FIELDS = (
    "human_decision", "human_corrected_score_or_label_json", "human_correction_reason",
    "human_evidence_reference", "reviewer_id", "review_timestamp_utc",
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_builder():
    spec = importlib.util.spec_from_file_location("secondary_qc_builder", BUILDER)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    errors = []
    stored = load_jsonl(QUEUE)
    builder = load_builder()
    regenerated, regenerated_sampling = builder.build_queue()
    if stored != regenerated:
        errors.append("IMMUTABLE_QUEUE_DIFFERS_FROM_REGENERATED_SOURCES")
    ids = [x["review_id"] for x in stored]
    categories = Counter(x["qc_category"] for x in stored)
    targeted = {x["review_id"] for x in stored if x["qc_category"] == "TARGETED"}
    random_ids = {x["review_id"] for x in stored if x["qc_category"] == "RANDOM"}
    if len(stored) != 43 or len(set(ids)) != 43:
        errors.append("REVIEW_COUNT_OR_UNIQUENESS")
    if categories != {"TARGETED": 20, "RANDOM": 23}:
        errors.append("CATEGORY_COUNTS")
    if targeted & random_ids:
        errors.append("TARGETED_RANDOM_OVERLAP")
    if Counter(x["metric"] for x in stored if x["qc_category"] == "TARGETED") != {"SCENARIO_ADEQUACY": 15, "STRICT_ORACLE": 5}:
        errors.append("TARGETED_METRIC_COUNTS")
    if Counter(x["metric"] for x in stored if x["qc_category"] == "RANDOM") != {"TCC": 11, "SCENARIO_ADEQUACY": 8, "STRICT_ORACLE": 4}:
        errors.append("RANDOM_METRIC_COUNTS")
    for row in stored:
        if not row["selected_generated_scenarios_full"]:
            errors.append(f"MISSING_SOURCE_SCENARIO:{row['review_id']}")
        if not row["gold_reference"]["selected_paths"]:
            errors.append(f"MISSING_GOLD_REFERENCE:{row['review_id']}")
        if any(row.get(k) not in {"", None} for k in HUMAN_FIELDS):
            errors.append(f"HUMAN_FIELD_NOT_BLANK:{row['review_id']}")

    with WORKBOOK.open(encoding="utf-8", newline="") as fh:
        workbook_rows = list(csv.DictReader(fh))
    if len(workbook_rows) != 43:
        errors.append("WORKBOOK_ROW_COUNT")
    if any((r.get(k) or "").strip() for r in workbook_rows for k in HUMAN_FIELDS):
        errors.append("WORKBOOK_HUMAN_FIELD_NOT_BLANK")

    forbidden_hits = []
    for path in (QUEUE, REVIEW, WORKBOOK, SUMMARY, MANIFEST):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\bP3-\d{3}-[ABC]-R\d+\b", text):
            forbidden_hits.append({"file": str(path), "pattern": "restricted_task_id"})
        if re.search(r"[\"'](?:condition|condition_label|experimental_condition|bm25_score|bm25_rank|retrieval_rank|original_bm25_rank)[\"']\s*:", text, re.I):
            forbidden_hits.append({"file": str(path), "pattern": "condition_or_retrieval_field"})
    if forbidden_hits:
        errors.append("BLINDING_OR_RETRIEVAL_METADATA_EXPOSED")

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest["sampling"] != regenerated_sampling:
        errors.append("SAMPLING_NOT_REPRODUCIBLE")
    expected_output_hashes = {
        "immutable_queue": sha(QUEUE),
        "human_review_markdown": sha(REVIEW),
        "workbook": sha(WORKBOOK),
        "summary": sha(SUMMARY),
    }
    if manifest["output_hashes"] != expected_output_hashes:
        errors.append("OUTPUT_HASH_MISMATCH")
    input_paths = {
        "provisional_results": EVAL / "public" / "provisional_ai_metric_results.jsonl",
        "missing_evidence_classification": EVAL / "stage1_missing_evidence_classification.csv",
        "frozen_gold": HERE.parent / "gold_v1_frozen" / "gold_v1.jsonl",
        "qc_policy": HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_human_verification_and_qc_policy.md",
    }
    if manifest["input_hashes"] != {k: sha(v) for k, v in input_paths.items()}:
        errors.append("INPUT_HASH_MISMATCH")

    report = {
        "status": "PASS_SECONDARY_QC_REVIEW_PACKAGE_PREPARED" if not errors else "FAIL",
        "checks": {
            "exactly_43_unique": len(stored) == len(set(ids)) == 43,
            "exactly_20_targeted": categories["TARGETED"] == 20,
            "exactly_23_random": categories["RANDOM"] == 23,
            "no_overlap": not bool(targeted & random_ids),
            "source_scenarios_complete": not any(x.startswith("MISSING_SOURCE_SCENARIO") for x in errors),
            "gold_references_complete": not any(x.startswith("MISSING_GOLD_REFERENCE") for x in errors),
            "ai_scores_and_evidence_unchanged": stored == regenerated,
            "sampling_reproducible": manifest["sampling"] == regenerated_sampling,
            "no_condition_or_retrieval_exposure": not forbidden_hits,
            "human_fields_blank": not any("HUMAN_FIELD_NOT_BLANK" in x for x in errors),
        },
        "counts": {
            "total": len(stored),
            "targeted": categories["TARGETED"],
            "random": categories["RANDOM"],
            "targeted_by_metric": dict(Counter(x["metric"] for x in stored if x["qc_category"] == "TARGETED")),
            "random_by_metric": dict(Counter(x["metric"] for x in stored if x["qc_category"] == "RANDOM")),
        },
        "forbidden_hits": forbidden_hits,
        "errors": errors,
        "score_lock_performed": False,
        "unblinding_performed": False,
        "restricted_mapping_accessed": False,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if not errors else 1)


if __name__ == "__main__":
    main()
