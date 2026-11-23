#!/usr/bin/env python3
"""Versioned offline revalidation of all saved Stage-1 evaluator assessments."""

from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
EVAL_ROOT = HERE / "stage1_ai_evaluation"
RESPONSES = EVAL_ROOT / "responses"
P3H = RESEARCH / "outputs/phase3c_h_ai_first_amendment"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIPE = load_module("phase3d_revalidation_v2_pipeline", P3H / "phase3c_h_evaluation_pipeline.py")


def write_new(path: Path, value: object) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(f"versioned revalidation conflict: {path}")
        return
    path.write_bytes(data)


def main() -> None:
    gold = {row["bug_id"]: row for row in PIPE.jsonl(FROZEN / "gold_v1.jsonl")}
    eligibility = PIPE.load_eligibility()
    records = []
    for directory in sorted(RESPONSES.iterdir()):
        summary_path = directory / "evaluation_summary.json"
        assessment_path = directory / "ai_assessment.json"
        if not summary_path.is_file() or not assessment_path.is_file():
            continue
        historical = json.loads(summary_path.read_text(encoding="utf-8"))
        assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
        catalog = PIPE.gold_requirement_catalog(gold[assessment["bug_id"]])
        errors = PIPE.validate_ai_assessment(assessment, eligibility, catalog)
        result = {
            "validation_version": "v2-outcome-id-scoped",
            "anonymous_response_id": assessment["anonymous_response_id"],
            "bug_id": assessment["bug_id"],
            "historical_status": historical["status"],
            "historical_validation_preserved": True,
            "status": "SEMANTIC_VALID_V2" if not errors else "SEMANTIC_INVALID_V2",
            "errors": errors,
            "outcome_id_scope": "test_oracles[].acceptable_expected_outcomes[].outcome_id only",
            "raw_assessment_modified": False,
            "model_calls": 0,
        }
        write_new(directory / "semantic_validation_v2_outcome_id_scoped.json", result)
        if not errors:
            scores = PIPE.deterministic_scores(assessment, eligibility, catalog)
            existing = directory / "provisional_deterministic_scores.json"
            if existing.is_file() and json.loads(existing.read_text(encoding="utf-8")) != scores:
                raise RuntimeError(f"{directory.name}: deterministic score changed after catalog-only patch")
            write_new(directory / "provisional_deterministic_scores_v2.json", scores)
            result["deterministic_scores_v2_written"] = True
        else:
            result["deterministic_scores_v2_written"] = False
        records.append(result)
    counts = Counter(row["status"] for row in records)
    report = {
        "status": "PASS_OFFLINE_REVALIDATION_V2",
        "records_revalidated": len(records),
        "valid_v2": counts["SEMANTIC_VALID_V2"],
        "invalid_v2": counts["SEMANTIC_INVALID_V2"],
        "model_calls": 0,
        "historical_files_overwritten": 0,
        "known_A1_records_now_valid": {
            aid: next(row for row in records if row["anonymous_response_id"] == aid)["status"]
            for aid in [
                "AR1-ff2f42626deb281f38b6206b",
                "AR1-15faaa933672b0ce93ccc447",
                "AR1-28068bf56767b6caac7208a2",
                "AR1-856e26ca1302a7a09f5eaf0d",
            ]
        },
        "maptool_missing_O2_status": next(
            row for row in records if row["anonymous_response_id"] == "AR1-7040966ac3892a71ccd95184"
        ),
        "unresolved": [row for row in records if row["status"] == "SEMANTIC_INVALID_V2"],
    }
    write_new(EVAL_ROOT / "offline_revalidation_v2_summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
