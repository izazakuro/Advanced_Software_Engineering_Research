#!/usr/bin/env python3
"""Build provisional results and the blinded Repeat-1 CSPC human queue."""

from __future__ import annotations

from collections import Counter
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
ROOT = HERE / "stage1_ai_evaluation"
RESPONSES = ROOT / "responses"
PUBLIC = ROOT / "public"
RESTRICTED = ROOT / "restricted"
REVIEW = ROOT / "human_cspc_review_package"
P3H = RESEARCH / "outputs/phase3c_h_ai_first_amendment"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
STAGE1 = HERE / "phase3d_stage1_54_generation_manifest.jsonl"
VALID_GEN = {"STRUCTURALLY_VALID", "SEMANTICALLY_INCOMPLETE"}


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIPE = load_module("phase3d_deliverable_pipeline", P3H / "phase3c_h_evaluation_pipeline.py")


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def effective_evaluation(anonymous_id: str) -> dict:
    directory = RESPONSES / anonymous_id
    retry_summary_path = directory / "authorized_retry_1_summary.json"
    if retry_summary_path.is_file():
        retry_summary = json.loads(retry_summary_path.read_text(encoding="utf-8"))
        retry_dir = directory / "authorized_retry_1"
        if retry_summary["effective_status"] == "AI_VALID_PROVISIONAL":
            return {"status": "AI_VALID_PROVISIONAL", "historical_status": retry_summary["original_effective_status"], "errors": [],
                    "scores_path": retry_dir / "provisional_deterministic_scores.json", "assessment_path": retry_dir / "ai_assessment.json"}
        assessment_path = retry_dir / "ai_assessment.json"
        return {"status": "EVALUATION_FAILED_AFTER_AUTHORIZED_RETRY", "historical_status": retry_summary["original_effective_status"],
                "errors": retry_summary["retry"].get("validation_errors", []), "scores_path": None,
                "assessment_path": assessment_path if assessment_path.is_file() else None}
    historical = json.loads((directory / "evaluation_summary.json").read_text(encoding="utf-8"))
    final_validation = directory / "semantic_validation_v3_final.json"
    if final_validation.is_file():
        final = json.loads(final_validation.read_text(encoding="utf-8"))
        if final["status"] == "SEMANTIC_VALID_FINAL":
            return {"status": "AI_VALID_PROVISIONAL", "historical_status": historical["status"], "errors": [],
                    "scores_path": directory / "provisional_deterministic_scores_v3_final.json", "assessment_path": directory / "ai_assessment.json"}
        return {"status": "EVALUATION_FAILED_SEMANTIC", "historical_status": historical["status"], "errors": final["errors"],
                "scores_path": None, "assessment_path": directory / "ai_assessment.json"}
    return {"status": "EVALUATION_FAILED_INFRASTRUCTURE", "historical_status": historical["status"], "errors": [],
            "scores_path": None, "assessment_path": None}


def main() -> None:
    stage1 = jsonl(STAGE1)
    mapping = json.loads((RESTRICTED / "stage1_task_mapping.json").read_text(encoding="utf-8"))
    task_to_anon = {value["task_id"]: key for key, value in mapping.items()}
    gold = {row["bug_id"]: row for row in jsonl(FROZEN / "gold_v1.jsonl")}
    eligibility = PIPE.eligibility_by_bug(PIPE.load_eligibility())

    provisional = []
    for row in stage1:
        if row["effective_status"] not in VALID_GEN:
            provisional.append({
                "bug_id": row["bug_id"], "anonymous_response_id": None,
                "generation_status": "INFRASTRUCTURE_MISSING", "evaluation_status": "NOT_EVALUATED_NO_GENERATION",
                "missingness_treatment": "MISSING_NOT_ZERO", "provisional_scores": None,
            })
            continue
        anonymous_id = task_to_anon[row["task_id"]]
        effective = effective_evaluation(anonymous_id)
        scores = json.loads(effective["scores_path"].read_text(encoding="utf-8")) if effective["scores_path"] else None
        provisional.append({
            "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"],
            "generation_status": row["effective_status"], "evaluation_status": effective["status"],
            "historical_evaluation_status": effective["historical_status"],
            "evaluation_errors": effective["errors"], "provisional_scores": scores,
            "human_verification_complete": False, "score_locked": False,
        })
    provisional_path = PUBLIC / "provisional_ai_metric_results.jsonl"
    write(provisional_path, "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in provisional).encode())

    queue = []
    for row in stage1:
        paths = eligibility[row["bug_id"]]["cspc_eligible_path_ids"]
        if not paths:
            continue
        if row["effective_status"] not in VALID_GEN:
            raise RuntimeError(f"CSPC-eligible generation unexpectedly missing: {row['task_id']}")
        anonymous_id = task_to_anon[row["task_id"]]
        effective = effective_evaluation(anonymous_id)
        generated = json.loads((RESEARCH / row["effective_output_path"]).read_text(encoding="utf-8"))
        projection = PIPE.project_gold_for_evaluation(gold[row["bug_id"]])
        assessment = json.loads(effective["assessment_path"].read_text(encoding="utf-8")) if effective["assessment_path"] else None
        for path_id in paths:
            ai_path = next((item for item in assessment.get("cspc_path_assessments", []) if item["gold_path_id"] == path_id), None) if assessment else None
            ai_uncertain = bool(ai_path and any(item.get("support_type") == "UNCERTAIN" for item in ai_path.get("evidence", [])))
            response_flags = assessment.get("uncertainty_flags", []) if assessment else []
            gold_path = next(item for item in projection.get("trigger_paths", []) if item["path_id"] == path_id)
            gold_oracles = [item for item in projection.get("test_oracles", []) if item.get("trigger_path_id") == path_id]
            if effective["status"] == "AI_VALID_PROVISIONAL":
                eval_state = "AI_VALID"
            elif ai_path is not None:
                eval_state = "EVALUATION_FAILED_AI_JUDGMENT_ADVISORY_ONLY"
            else:
                eval_state = "EVALUATION_FAILED_NO_AI_JUDGMENT"
            queue.append({
                "review_item_id": f"CSPC::{anonymous_id}::{path_id}",
                "anonymous_response_id": anonymous_id, "bug_id": row["bug_id"], "gold_path_id": path_id,
                "evaluation_state": eval_state,
                "evaluation_errors": effective["errors"],
                "ai_polarity": ("POSITIVE" if ai_path.get("path_complete") else "NEGATIVE") if ai_path else None,
                "ai_path_uncertain": ai_uncertain,
                "ai_response_uncertainty_flags": response_flags,
                "ai_cspc_path_assessment": ai_path,
                "generated_scenarios": generated["scenarios"],
                "gold_path": gold_path, "gold_oracles_for_path": gold_oracles,
                "affected_component": projection.get("affected_component"),
                "expected_behavior": projection.get("expected_behavior"),
                "observed_behavior": projection.get("observed_behavior"),
                "human_verification_status": "PENDING_HUMAN_VERIFICATION",
                "human_decision": None, "human_path_complete": None,
                "human_matching_scenario_id": None, "human_evidence_note": None,
                "reviewer_id": None, "review_timestamp_utc": None,
                "condition_visible": False, "restricted_task_id_visible": False,
            })
    if len(queue) != 36 or len({item["review_item_id"] for item in queue}) != 36:
        raise RuntimeError(f"Repeat-1 CSPC workload mismatch: {len(queue)}")
    queue_path = REVIEW / "repeat1_cspc_human_verification_queue.jsonl"
    write(queue_path, "".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in queue).encode())
    out = io.StringIO(); writer = csv.DictWriter(out, fieldnames=[
        "review_item_id", "anonymous_response_id", "bug_id", "gold_path_id", "evaluation_state",
        "ai_polarity", "ai_path_uncertain", "ai_response_uncertainty_flags", "human_verification_status",
        "human_decision", "human_path_complete", "human_matching_scenario_id", "human_evidence_note", "reviewer_id", "review_timestamp_utc",
    ])
    writer.writeheader()
    for item in queue:
        writer.writerow({key: json.dumps(item[key]) if isinstance(item[key], list) else item[key] for key in writer.fieldnames})
    write(REVIEW / "repeat1_cspc_human_verification_workbook.csv", out.getvalue().encode())

    eval_counts = Counter(item["evaluation_state"] for item in queue)
    polarity = Counter(item["ai_polarity"] for item in queue)
    uncertain = sum(item["ai_path_uncertain"] or bool(item["ai_response_uncertainty_flags"]) for item in queue)
    provisional_counts = Counter(item["evaluation_status"] for item in provisional)
    missingness = {
        "generation_infrastructure_missing": 1,
        "generation_missing_task": "P3-025-B-R1",
        "generation_missing_scored_as_zero": False,
        "evaluation_infrastructure_failures": provisional_counts["EVALUATION_FAILED_INFRASTRUCTURE"],
        "evaluation_semantic_failures": provisional_counts["EVALUATION_FAILED_SEMANTIC"],
        "evaluation_failures_after_authorized_retry": provisional_counts["EVALUATION_FAILED_AFTER_AUTHORIZED_RETRY"],
        "evaluation_failures_scored_as_generation_zero": False,
        "batch2_outputs_included": False,
    }
    write(PUBLIC / "stage1_evaluation_missingness.json", (json.dumps(missingness, indent=2, sort_keys=True) + "\n").encode())
    workload = {
        "total_cspc_review_items": len(queue),
        "ai_valid_items": eval_counts["AI_VALID"],
        "evaluation_failed_advisory_items": eval_counts["EVALUATION_FAILED_AI_JUDGMENT_ADVISORY_ONLY"],
        "evaluation_failed_no_ai_items": eval_counts["EVALUATION_FAILED_NO_AI_JUDGMENT"],
        "ai_positive_items": polarity["POSITIVE"], "ai_negative_items": polarity["NEGATIVE"],
        "ai_uncertain_items_overlap": uncertain,
        "human_pending_items": sum(item["human_verification_status"] == "PENDING_HUMAN_VERIFICATION" for item in queue),
        "automatically_approved_items": 0,
    }
    write(REVIEW / "cspc_workload_summary.json", (json.dumps(workload, indent=2, sort_keys=True) + "\n").encode())
    readme = f"""# Repeat 1 CSPC Human Verification Package

This blinded package contains all {len(queue)} applicable Repeat-1 CSPC path judgments from the frozen 12-path/11-bug mapping across A/B/C responses. It contains AI-positive, AI-negative, uncertain, evaluation-failed advisory, and evaluation-failed-without-AI cases. Every item remains `PENDING_HUMAN_VERIFICATION`; no decision is prefilled.

- AI-valid items: {workload['ai_valid_items']}
- Evaluation-failed with advisory AI path judgment: {workload['evaluation_failed_advisory_items']}
- Evaluation-failed without an AI path judgment: {workload['evaluation_failed_no_ai_items']}
- AI-positive: {workload['ai_positive_items']}
- AI-negative: {workload['ai_negative_items']}
- Items carrying path- or response-level uncertainty (overlapping): {workload['ai_uncertain_items_overlap']}
- Human pending: {workload['human_pending_items']}

Conditions and restricted task IDs are deliberately absent. Reviewers must inspect each path independently and may accept, override, or mark unresolved in a later authorized adjudication phase.
"""
    write(REVIEW / "README.md", readme.encode())

    valid_metrics = provisional_counts["AI_VALID_PROVISIONAL"]
    report = {
        "status": "STAGE1_AI_EVALUATION_COMPLETE_AWAITING_HUMAN_CSPC_VERIFICATION",
        "stage1_manifest_sha256": sha(STAGE1), "generation_records": 54,
        "generation_valid_inputs": 53, "generation_infrastructure_missing": 1,
        "evaluator_attempts": 53, "effective_valid_evaluations": valid_metrics,
        "unresolved_semantic_evaluations": provisional_counts["EVALUATION_FAILED_SEMANTIC"],
        "unresolved_infrastructure_evaluations": provisional_counts["EVALUATION_FAILED_INFRASTRUCTURE"],
        "unresolved_evaluation_total": provisional_counts["EVALUATION_FAILED_SEMANTIC"] + provisional_counts["EVALUATION_FAILED_INFRASTRUCTURE"] + provisional_counts["EVALUATION_FAILED_AFTER_AUTHORIZED_RETRY"],
        "provisional_metric_records": valid_metrics,
        "cspc_human_workload": workload,
        "human_verification_ready": True,
        "human_verification_complete": False,
        "formal_statistics_run": False, "score_locked": False, "arms_unblinded": False,
        "provisional_results_sha256": sha(provisional_path), "cspc_queue_sha256": sha(queue_path),
    }
    write(ROOT / "stage1_evaluation_completion_summary.json", (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())
    md = f"""# Phase 3D Stage 1 AI Evaluation Completion

Status: `{report['status']}`

- Generation inputs: 53 valid; P3-025-B-R1 remains infrastructure missing, not zero.
- Evaluator attempts: 53.
- Effective valid evaluations after scoped offline revalidation: {valid_metrics}.
- Unresolved semantic evaluations: {report['unresolved_semantic_evaluations']}.
- Unresolved evaluator infrastructure failures: {report['unresolved_infrastructure_evaluations']}.
- CSPC human workload: {workload['total_cspc_review_items']} items; all pending, none automatically approved.

No Batch 2/3 generation output was included. No formal statistical inference, score lock, or unblinding was performed.
"""
    write(ROOT / "stage1_evaluation_completion_report.md", md.encode())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
