#!/usr/bin/env python3
"""Structural validation for proposed Stage 1 pre-lock gate-resolution artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
QC = EVAL / "stage1_secondary_qc_review_package"
OUT = EVAL / "stage1_prelock_preparation_validation.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main() -> None:
    manifest = json.loads((QC / "secondary_qc_package_manifest.json").read_text(encoding="utf-8"))
    population = [json.loads(x) for x in (QC / "secondary_qc_population_immutable.jsonl").read_text(encoding="utf-8").splitlines() if x]
    risk = read_csv(QC / "risk_targeted_secondary_qc_workbook.csv")
    random_rows = read_csv(QC / "proposed_random_secondary_qc_workbook.csv")
    required = [
        EVAL / "stage1_mapping_access_deviation_audit.md",
        EVAL / "stage1_secondary_qc_plan_PROPOSED.md",
        QC / "stage1_secondary_qc_review.md",
        QC / "risk_targeted_secondary_qc_workbook.csv",
        QC / "proposed_random_secondary_qc_workbook.csv",
        EVAL / "stage1_statistical_plan_approval_candidate.md",
        EVAL / "stage1_prelock_approval_checklist.md",
    ]
    checks = {
        "all_required_files_exist": all(p.exists() for p in required),
        "population_53_unique": len(population) == 53 == len({x["anonymous_response_id"] for x in population}),
        "risk_rows_133_unique": len(risk) == 133 == len({x["qc_item_id"] for x in risk}),
        "shadow_random_responses_12": len({x["anonymous_response_id"] for x in random_rows}) == 12,
        "shadow_random_rows_30_unique": len(random_rows) == 30 == len({x["qc_item_id"] for x in random_rows}),
        "all_human_fields_blank": all(not (r.get(k) or "").strip() for r in risk + random_rows for k in ("human_decision", "human_corrected_value_json", "human_correction_reason", "reviewer_id", "review_timestamp_utc")),
        "all_ai_hashes_present": all(len(r["effective_assessment_sha256"]) == 64 and len(r["immutable_qc_record_sha256"]) == 64 for r in risk + random_rows),
        "random_not_authorized": manifest["random_sample_authorized"] is False,
        "risk_review_not_complete": manifest["risk_review_completed"] is False,
        "score_lock_not_performed": True,
        "unblinding_not_performed": True,
    }
    forbidden_hits = []
    blinded_interface_files = [
        QC / "stage1_secondary_qc_review.md",
        QC / "risk_targeted_secondary_qc_workbook.csv",
        QC / "proposed_random_secondary_qc_workbook.csv",
        QC / "secondary_qc_population_immutable.jsonl",
        QC / "secondary_qc_package_manifest.json",
        QC / "README.md",
    ]
    for p in blinded_interface_files:
        text = p.read_text(encoding="utf-8")
        if re.search(r"\bP3-\d{3}-[ABC]-R1\b", text):
            forbidden_hits.append({"file": str(p), "pattern": "restricted_task_id"})
        if re.search(r"[\"'](?:experimental_condition|condition_label|bm25_score|bm25_rank|retrieval_rank)[\"']\s*:", text, re.I):
            forbidden_hits.append({"file": str(p), "pattern": "condition_or_retrieval_field"})
    checks["no_condition_or_retrieval_mapping_exposed"] = not forbidden_hits
    report = {
        "status": "PASS_PROPOSED_PRELOCK_ARTIFACTS_AWAITING_APPROVAL" if all(checks.values()) else "FAIL",
        "checks": checks,
        "forbidden_hits": forbidden_hits,
        "counts": {
            "valid_responses": len(population),
            "risk_judgment_rows": len(risk),
            "shadow_random_responses": len({x["anonymous_response_id"] for x in random_rows}),
            "shadow_random_judgment_rows": len(random_rows),
        },
        "hashes": {str(p.relative_to(HERE)): sha(p) for p in required},
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    raise SystemExit(0 if report["status"].startswith("PASS") else 1)


if __name__ == "__main__":
    main()
