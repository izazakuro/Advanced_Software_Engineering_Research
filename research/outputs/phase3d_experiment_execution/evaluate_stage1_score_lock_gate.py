#!/usr/bin/env python3
"""Evaluate the Stage 1 lock gate without releasing or using arm assignments."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
PKG = HERE / "stage1_ai_evaluation" / "stage1_cspc_human_verification_preparation"
WORKBOOK = PKG / "cspc_human_verification_workbook.csv"
VALIDATION = PKG / "human_verification_validation_report.json"
BASELINE = PKG / "cspc_review_baseline_immutable.jsonl"
SECONDARY_POLICY = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_human_verification_and_qc_policy.md"
STAT_PLAN = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_statistical_analysis_plan_v2_PROPOSED.md"
LOCK_POLICY = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_adjudication_and_score_lock_policy.md"
PIPELINE = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_evaluation_pipeline.py"
PROVISIONAL = HERE / "stage1_ai_evaluation" / "public" / "provisional_ai_metric_results.jsonl"
GEN_MANIFEST = HERE / "phase3d_stage1_54_generation_manifest.jsonl"
RESTRICTED_MAPPING = HERE / "stage1_ai_evaluation" / "restricted" / "stage1_task_mapping.json"
OUT_JSON = HERE / "stage1_ai_evaluation" / "stage1_score_lock_gate_v1.json"
OUT_MD = HERE / "stage1_ai_evaluation" / "stage1_score_lock_gate_report.md"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_module():
    spec = importlib.util.spec_from_file_location("phase3c_h_pipeline_gate", PIPELINE)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def main() -> None:
    validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
    if validation["status"] != "PASS_READY_FOR_SEPARATE_SCORE_LOCK_STEP":
        raise ValueError("Human CSPC validation did not pass")
    baseline = [json.loads(x) for x in BASELINE.read_text(encoding="utf-8").splitlines() if x]
    with WORKBOOK.open(encoding="utf-8", newline="") as fh:
        decisions = list(csv.DictReader(fh))
    primary_queue = [{"review_item_id": x["review_item_id"]} for x in baseline]
    adjudications = [
        {
            "review_item_id": x["review_item_id"],
            "final_status": "HUMAN_VERIFIED" if x["human_decision"] == "ACCEPT" else "UNRESOLVED",
        }
        for x in decisions
    ]
    unresolved = sum(x["human_decision"] == "UNRESOLVED" for x in decisions)

    secondary_text = SECONDARY_POLICY.read_text(encoding="utf-8")
    stat_text = STAT_PLAN.read_text(encoding="utf-8")
    secondary_qc_complete = False  # No completed QC artifact; parameters are explicitly Pending.
    statistical_plan_frozen = False  # File is PROPOSED and says choices remain pending approval.
    if "Proposed—not approved" not in secondary_text or "remain pending human approval" not in stat_text:
        raise ValueError("Expected fail-closed protocol markers changed; re-audit required")

    pipeline = load_module()
    gate = pipeline.score_lock_gate(
        primary_queue,
        adjudications,
        secondary_qc_complete=secondary_qc_complete,
        unresolved_count=unresolved,
        statistical_plan_frozen=statistical_plan_frozen,
    )
    protocol_deviations = [
        {
            "code": "PRE_LOCK_RESTRICTED_MAPPING_INSPECTION",
            "description": "The restricted Stage 1 mapping was inspected during repository preflight before a successful lock. No mapping-derived analysis or group result was produced.",
            "requires_methodological_disposition": True,
        }
    ]
    blockers = list(gate["blockers"]) + ["PRE_LOCK_MAPPING_INSPECTION_PROTOCOL_DEVIATION"]
    provisional_rows = [json.loads(x) for x in PROVISIONAL.read_text(encoding="utf-8").splitlines() if x]
    report = {
        "artifact_version": "stage1-score-lock-gate-v1",
        "status": "BLOCKED_BEFORE_SCORE_LOCK_AND_UNBLINDING",
        "human_cspc_validation": {
            "passed": True,
            "records": len(decisions),
            "accepted": sum(x["human_decision"] == "ACCEPT" for x in decisions),
            "unresolved": unresolved,
            "workbook_sha256": sha(WORKBOOK),
            "validation_report_sha256": sha(VALIDATION),
            "immutable_baseline_sha256": sha(BASELINE),
        },
        "secondary_qc": {
            "complete": secondary_qc_complete,
            "policy_sha256": sha(SECONDARY_POLICY),
            "approved_random_audit_parameters_found": False,
            "completed_secondary_review_artifact_found": False,
            "provisional_evaluation_rows": len(provisional_rows),
            "provisional_secondary_metrics_may_not_be_silently_finalized": True,
        },
        "statistical_plan": {
            "frozen": statistical_plan_frozen,
            "path": str(STAT_PLAN),
            "sha256": sha(STAT_PLAN),
            "file_status": "PROPOSED_WITH_PENDING_HUMAN_APPROVAL",
        },
        "gate_result": {
            **gate,
            "score_locked": False,
            "arm_mapping_release_allowed": False,
            "blockers": blockers,
        },
        "protocol_deviations": protocol_deviations,
        "unblinding": {
            "conditional_authorization_received": True,
            "prerequisites_satisfied": False,
            "mapping_used_for_analysis": False,
            "group_comparison_performed": False,
        },
        "provenance": {
            "generation_manifest_sha256": sha(GEN_MANIFEST),
            "restricted_mapping_sha256": sha(RESTRICTED_MAPPING),
            "lock_policy_sha256": sha(LOCK_POLICY),
            "pipeline_sha256": sha(PIPELINE),
        },
        "outputs_not_created_due_to_gate": [
            "stage1_final_group_comparison.md",
            "stage1_bug_level_scores.csv",
            "stage1_group_summary.csv",
            "stage1_paired_contrasts.csv",
            "stage1_statistical_results.json",
            "stage1_missingness_report.md",
            "CSPC comparison figures",
        ],
    }
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_MD.write_text(f"""# Phase 3D Stage 1 score-lock gate

Status: **BLOCKED BEFORE SCORE LOCK AND UNBLINDING**

Human CSPC verification passed: 36/36 accepted, no overrides, no unresolved items, and immutable evidence validation passed.

Required blockers:

- `SECONDARY_QC_INCOMPLETE`: the secondary-QC policy still marks audit parameters pending, and no completed secondary-QC artifact exists.
- `STATISTICAL_PLAN_NOT_FROZEN`: the v2 statistical plan remains explicitly proposed and pending approval.
- `PRE_LOCK_MAPPING_INSPECTION_PROTOCOL_DEVIATION`: the restricted mapping was inspected during preflight before lock; it was not used for group analysis. This requires a methodological disposition.

Consequences:

- `score_locked=false`
- `arm_mapping_release_allowed=false`
- no A/B/C comparison, p-value, confidence interval, or figure was produced
- the infrastructure-missing observation remains missing and was not converted to zero
- Batch 2 and Batch 3 remain excluded

Required next actions: approve and execute the secondary-QC plan; explicitly freeze a versioned statistical plan; decide how to document/handle the pre-lock mapping-access deviation; then rerun the lock gate before any analysis.
""", encoding="utf-8")
    print(json.dumps(report["gate_result"], ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
