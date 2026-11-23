#!/usr/bin/env python3
"""Final offline revalidation of every preserved Stage-1 AI assessment."""

from __future__ import annotations

from collections import Counter
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
ROOT = HERE / "stage1_ai_evaluation"
RESPONSES = ROOT / "responses"
P3H = RESEARCH / "outputs/phase3c_h_ai_first_amendment"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIPE = load_module("phase3d_final_revalidation_pipeline", P3H / "phase3c_h_evaluation_pipeline.py")


def write_new(path: Path, value: object) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    if path.exists():
        if path.read_bytes() != data:
            raise FileExistsError(path)
        return
    path.write_bytes(data)


def main() -> None:
    gold = {row["bug_id"]: row for row in PIPE.jsonl(FROZEN / "gold_v1.jsonl")}
    eligibility = PIPE.load_eligibility()
    records = []
    for directory in sorted(RESPONSES.iterdir()):
        assessment_path = directory / "ai_assessment.json"
        summary_path = directory / "evaluation_summary.json"
        if not assessment_path.is_file() or not summary_path.is_file():
            continue
        assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
        historical = json.loads(summary_path.read_text(encoding="utf-8"))
        catalog = PIPE.gold_requirement_catalog(gold[assessment["bug_id"]])
        errors = PIPE.validate_ai_assessment(assessment, eligibility, catalog)
        record = {
            "validation_version": "v3-final-scoped-outcome-and-exact-sets",
            "anonymous_response_id": assessment["anonymous_response_id"],
            "bug_id": assessment["bug_id"],
            "historical_status": historical["status"],
            "status": "SEMANTIC_VALID_FINAL" if not errors else "SEMANTIC_INVALID_FINAL",
            "errors": errors,
            "raw_assessment_modified": False,
            "historical_validation_preserved": True,
            "model_calls": 0,
        }
        write_new(directory / "semantic_validation_v3_final.json", record)
        if not errors:
            scores = PIPE.deterministic_scores(assessment, eligibility, catalog)
            for candidate in (directory / "provisional_deterministic_scores.json", directory / "provisional_deterministic_scores_v2.json"):
                if candidate.is_file() and json.loads(candidate.read_text(encoding="utf-8")) != scores:
                    raise RuntimeError(f"{directory.name}: deterministic scoring drift")
            write_new(directory / "provisional_deterministic_scores_v3_final.json", scores)
            record["deterministic_scores_written"] = True
        else:
            record["deterministic_scores_written"] = False
        records.append(record)
    counts = Counter(row["status"] for row in records)
    infrastructure = []
    for directory in sorted(RESPONSES.iterdir()):
        summary = directory / "evaluation_summary.json"
        if summary.is_file():
            value = json.loads(summary.read_text(encoding="utf-8"))
            if value["status"] == "AI_EVALUATION_INFRASTRUCTURE_FAILURE":
                infrastructure.append({"anonymous_response_id": value["anonymous_response_id"], "bug_id": value["bug_id"], "status": value["status"]})
    report = {
        "status": "PASS_FINAL_OFFLINE_REVALIDATION",
        "assessments_revalidated": len(records),
        "valid_assessments": counts["SEMANTIC_VALID_FINAL"],
        "invalid_assessments": counts["SEMANTIC_INVALID_FINAL"],
        "infrastructure_failures_without_assessment": len(infrastructure),
        "model_calls": 0,
        "historical_files_overwritten": 0,
        "invalid_records": [row for row in records if row["status"] == "SEMANTIC_INVALID_FINAL"],
        "infrastructure_records": infrastructure,
    }
    write_new(ROOT / "final_offline_revalidation_summary.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
