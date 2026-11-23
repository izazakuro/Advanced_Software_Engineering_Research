#!/usr/bin/env python3
"""Fail-closed validation for the completed Stage 1 CSPC human workbook."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "stage1_ai_evaluation" / "stage1_cspc_human_verification_preparation"
DEFAULT_WORKBOOK = PACKAGE / "cspc_human_verification_workbook.csv"
BASELINE = PACKAGE / "cspc_review_baseline_immutable.jsonl"
ALLOWED = {"ACCEPT", "OVERRIDE_TO_0", "OVERRIDE_TO_1", "UNRESOLVED"}
IMMUTABLE_CSV_FIELDS = {
    "review_item_id", "anonymous_response_id", "bug_id", "gold_path_id",
    "required_setup_json", "required_triggers_json", "required_action_order_json",
    "expected_observation_json", "selected_generated_scenario_json",
    "generated_scenarios_considered_when_no_selection_json",
    "ai_cspc_judgment", "exact_ai_supporting_evidence_json",
    "missing_or_contradictory_requirements_json", "immutable_ai_and_evidence_sha256",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def expected_csv_projection(r: dict) -> dict[str, str]:
    def compact(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    return {
        "review_item_id": r["review_item_id"],
        "anonymous_response_id": r["anonymous_response_id"],
        "bug_id": r["bug_id"],
        "gold_path_id": r["gold_path_id"],
        "required_setup_json": compact(r["required_setup"]),
        "required_triggers_json": compact(r["required_triggers"]),
        "required_action_order_json": compact(r["required_action_order"]),
        "expected_observation_json": compact(r["expected_observation"]),
        "selected_generated_scenario_json": compact(r["selected_generated_scenario"]),
        "generated_scenarios_considered_when_no_selection_json": compact(r["generated_scenarios_considered_when_no_selection"]),
        "ai_cspc_judgment": str(r["ai_cspc_judgment"]),
        "exact_ai_supporting_evidence_json": compact(r["exact_ai_supporting_evidence"]),
        "missing_or_contradictory_requirements_json": compact(r["missing_or_contradictory_requirements"]),
        "immutable_ai_and_evidence_sha256": r["immutable_ai_and_evidence_sha256"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--report", type=Path, default=PACKAGE / "human_verification_validation_report.json")
    args = parser.parse_args()

    baseline = load_jsonl(BASELINE)
    expected = {r["review_item_id"]: expected_csv_projection(r) for r in baseline}
    errors: list[dict] = []
    with args.workbook.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        headers = reader.fieldnames or []

    forbidden_headers = [h for h in headers if h.lower() in {
        "condition", "condition_label", "experimental_condition", "task_id",
        "bm25_score", "bm25_rank", "retrieval_rank", "group", "arm",
    }]
    if forbidden_headers:
        errors.append({"code": "BLINDING_FIELD_EXPOSED", "fields": forbidden_headers})
    if len(rows) != 36 or len({r.get("review_item_id") for r in rows}) != 36:
        errors.append({"code": "RECORD_COUNT_OR_UNIQUENESS", "expected": 36, "actual": len(rows)})

    seen = set()
    unresolved = 0
    overrides = 0
    for row_number, row in enumerate(rows, 2):
        rid = row.get("review_item_id", "")
        seen.add(rid)
        if rid not in expected:
            errors.append({"code": "UNKNOWN_REVIEW_ITEM", "row": row_number, "review_item_id": rid})
            continue
        for field in IMMUTABLE_CSV_FIELDS:
            if row.get(field) != expected[rid][field]:
                errors.append({"code": "IMMUTABLE_AI_DATA_CHANGED", "row": row_number, "review_item_id": rid, "field": field})
        decision = (row.get("human_decision") or "").strip().upper()
        reason = (row.get("human_correction_reason") or "").strip()
        if decision not in ALLOWED:
            errors.append({"code": "MISSING_OR_INVALID_HUMAN_DECISION", "row": row_number, "review_item_id": rid, "actual": decision})
        if decision.startswith("OVERRIDE_TO_"):
            overrides += 1
            if not reason:
                errors.append({"code": "OVERRIDE_WITHOUT_REASON", "row": row_number, "review_item_id": rid})
        if decision == "UNRESOLVED":
            unresolved += 1
            errors.append({"code": "UNRESOLVED_NOT_FINALIZABLE", "row": row_number, "review_item_id": rid})
        combined = "\n".join(str(v) for v in row.values())
        if re.search(r"\bP3-\d{3}-[ABC]-R1\b", combined) or re.search(r"\bBM25\b", combined, re.I):
            errors.append({"code": "POTENTIAL_CONDITION_OR_RETRIEVAL_EXPOSURE", "row": row_number, "review_item_id": rid})

    for rid in sorted(set(expected) - seen):
        errors.append({"code": "MISSING_REVIEW_ITEM", "review_item_id": rid})

    report = {
        "status": "PASS_READY_FOR_SEPARATE_SCORE_LOCK_STEP" if not errors else "FAIL_NOT_FINALIZABLE",
        "workbook": str(args.workbook),
        "workbook_sha256": hashlib.sha256(args.workbook.read_bytes()).hexdigest(),
        "baseline_sha256": hashlib.sha256(BASELINE.read_bytes()).hexdigest(),
        "expected_items": 36,
        "actual_items": len(rows),
        "explicit_valid_decisions": sum((r.get("human_decision") or "").strip().upper() in ALLOWED for r in rows),
        "override_count": overrides,
        "unresolved_count": unresolved,
        "ai_assessment_overwrite_detected": any(e["code"] == "IMMUTABLE_AI_DATA_CHANGED" for e in errors),
        "condition_mapping_exposure_detected": any(e["code"] in {"BLINDING_FIELD_EXPOSED", "POTENTIAL_CONDITION_OR_RETRIEVAL_EXPOSURE"} for e in errors),
        "score_lock_performed": False,
        "errors": errors,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
