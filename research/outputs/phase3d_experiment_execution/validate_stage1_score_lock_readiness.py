#!/usr/bin/env python3
"""Fail-closed readiness validation; this script never performs score lock."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
MANIFEST = EVAL / "stage1_score_lock_readiness_manifest.json"
REPORT = EVAL / "stage1_score_lock_readiness_validation.json"
FINAL_WB = EVAL / "stage1_secondary_qc_human_review_workbook_FINAL.csv"
FINAL_SUPPLEMENTAL = EVAL / "stage1_oracle_supplemental_adjudication_FINAL.csv"
FINAL_EFFECTIVE = EVAL / "stage1_oracle_effective_scores_FINAL.csv"
SUMMARY = EVAL / "stage1_secondary_qc_adjudication_summary_FINAL.md"
POLICY = EVAL / "stage1_secondary_qc_policy_v1_APPROVED.json"
RUBRIC = EVAL / "stage1_strict_oracle_applicability_amendment_v1_FROZEN.json"
STAT = EVAL / "stage1_statistical_plan_oracle_amendment_v1_FROZEN.md"
DEVIATION = EVAL / "stage1_mapping_access_deviation_disposition_v1_APPROVED.md"
SOURCE_WB = EVAL / "stage1_secondary_qc_human_review_workbook.csv"
SOURCE_QUEUE = EVAL / "stage1_secondary_qc_review_queue_immutable.jsonl"
SOURCE_PROPOSED = EVAL / "stage1_secondary_qc_adjudication_PROPOSED.csv"
SOURCE_SUPPLEMENTAL = EVAL / "stage1_oracle_supplemental_adjudication_PROPOSED.csv"
SOURCE_EFFECTIVE = EVAL / "stage1_oracle_effective_scores_PROPOSED.csv"
SOURCE_ORACLE_AUDIT = EVAL / "stage1_oracle_applicability_records.csv"
SOURCE_QC_MANIFEST = EVAL / "stage1_secondary_qc_review_manifest.json"
SOURCE_DEVIATION = EVAL / "stage1_mapping_access_deviation_audit.md"
GOLD = HERE.parent / "gold_v1_frozen" / "gold_v1.jsonl"
CSPC_WB = EVAL / "stage1_cspc_human_verification_preparation" / "cspc_human_verification_workbook.csv"
CSPC_VALIDATION = EVAL / "stage1_cspc_human_verification_preparation" / "human_verification_validation_report.json"
FINALIZER = HERE / "finalize_stage1_secondary_qc.py"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


checks: list[dict] = []


def check(name: str, passed: bool, detail: object) -> None:
    checks.append({"check": name, "status": "PASS" if passed else "FAIL", "detail": detail})


def main() -> int:
    required = [MANIFEST, FINAL_WB, FINAL_SUPPLEMENTAL, FINAL_EFFECTIVE, SUMMARY, POLICY, RUBRIC, STAT, DEVIATION]
    check("required_final_artifacts_exist", all(x.is_file() for x in required), [str(x) for x in required if not x.is_file()])
    if not all(x.is_file() for x in required):
        return finish(None)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    source = read_csv(SOURCE_WB)
    final = read_csv(FINAL_WB)
    proposed = {x["review_id"]: x for x in read_csv(SOURCE_PROPOSED)}
    source_by_id = {x["review_id"]: x for x in source}

    immutable_fields = [
        "review_id", "anonymous_response_id", "metric", "qc_category", "original_ai_score_json",
        "original_selected_scenario_ids_json", "original_gold_path_ids_json", "risk_classification",
        "risk_pattern_signature", "identified_metric_evidence_deficiency",
        "original_ai_assessment_and_evidence_json", "immutable_review_record_sha256",
    ]
    mismatches = []
    for row in final:
        src = source_by_id.get(row["review_id"])
        if not src:
            mismatches.append({"review_id": row["review_id"], "error": "not in source"})
            continue
        for field in immutable_fields:
            if row[field] != src[field]:
                mismatches.append({"review_id": row["review_id"], "field": field})
    check("all_43_immutable_qc_fields_preserved", len(final) == 43 and len({x["review_id"] for x in final}) == 43 and not mismatches, mismatches)

    decisions = Counter(x["human_decision"] for x in final)
    check("all_43_authorized_decisions_final", decisions == {"ACCEPT": 41, "CORRECT": 2} and all(x["reviewer_id"] and x["review_timestamp_utc"] for x in final), dict(decisions))
    check("no_unresolved_qc_decisions", not any(x["human_decision"] in {"", "UNRESOLVED"} for x in final), [x["review_id"] for x in final if x["human_decision"] in {"", "UNRESOLVED"}])

    correction_errors = []
    corrected = [x for x in final if x["human_decision"] == "CORRECT"]
    for row in corrected:
        prop = proposed[row["review_id"]]
        try:
            correction = json.loads(row["human_corrected_score_or_label_json"])
            evidence = json.loads(row["human_evidence_reference"])
        except Exception as exc:
            correction_errors.append({"review_id": row["review_id"], "error": str(exc)})
            continue
        if row["metric"] != "STRICT_ORACLE" or prop["proposed_decision"] != "CORRECT":
            correction_errors.append({"review_id": row["review_id"], "error": "unauthorized correction"})
        if len(correction) != 1 or correction[0].get("oracle_id") != "O2" or correction[0].get("derived_applicability") != "NOT_APPLICABLE" or correction[0].get("effective_score", "sentinel") is not None or correction[0].get("denominator_contribution") != 0 or correction[0].get("numerator_contribution") != 0:
            correction_errors.append({"review_id": row["review_id"], "error": "incorrect O2 correction"})
        if evidence.get("selected_scenario_id") != "S1" or evidence.get("selected_gold_path_id") != "P1" or evidence.get("oracle_gold_path_id") != "P2" or evidence.get("oracle_id") != "O2":
            correction_errors.append({"review_id": row["review_id"], "error": "path/scenario evidence mismatch"})
    check("two_in_queue_oracle_corrections_exact", len(corrected) == 2 and not correction_errors, correction_errors)

    supplement = read_csv(FINAL_SUPPLEMENTAL)
    source_supplement = read_csv(SOURCE_SUPPLEMENTAL)
    check("supplemental_correction_final_and_separate", len(supplement) == 1 and len(source_supplement) == 1 and supplement[0]["oracle_entry_id"] == source_supplement[0]["oracle_entry_id"] and supplement[0]["human_decision"] == "CORRECT" and supplement[0]["researcher_confirmation"] == "CONFIRMED" and supplement[0]["reviewer_id"] and supplement[0]["review_timestamp_utc"], supplement)

    source_effective = {x["oracle_entry_id"]: x for x in read_csv(SOURCE_EFFECTIVE)}
    effective = read_csv(FINAL_EFFECTIVE)
    effective_errors = []
    classes = Counter(x["human_approved_applicability"] for x in effective)
    for row in effective:
        src = source_effective.get(row["oracle_entry_id"])
        if not src:
            effective_errors.append({"id": row["oracle_entry_id"], "error": "not in source"})
            continue
        if row["original_ai_semantic_label"] != src["original_ai_semantic_label"] or row["original_ai_binary_score"] != src["original_ai_binary_score"]:
            effective_errors.append({"id": row["oracle_entry_id"], "error": "original AI score/label changed"})
        if row["human_approved_applicability"] == "APPLICABLE":
            if row["effective_score"] != row["original_ai_binary_score"] or row["denominator_contribution"] != "1" or row["numerator_contribution"] != row["original_ai_binary_score"]:
                effective_errors.append({"id": row["oracle_entry_id"], "error": "applicable contribution mismatch"})
        elif row["human_approved_applicability"] == "NOT_APPLICABLE":
            if row["effective_score"] != "" or row["denominator_contribution"] != "0" or row["numerator_contribution"] != "0" or not row["correction_reason"]:
                effective_errors.append({"id": row["oracle_entry_id"], "error": "exclusion mismatch"})
        else:
            effective_errors.append({"id": row["oracle_entry_id"], "error": "unresolved/unknown applicability"})
    check("all_30_final_oracle_entries_reconcile", len(effective) == 30 and classes == {"APPLICABLE": 27, "NOT_APPLICABLE": 3} and not effective_errors, {"classes": dict(classes), "errors": effective_errors})
    denominator = sum(int(x["denominator_contribution"]) for x in effective)
    numerator = sum(int(x["numerator_contribution"]) for x in effective)
    applicable_zeros = [x for x in effective if x["human_approved_applicability"] == "APPLICABLE" and x["original_ai_binary_score"] == "0"]
    check("oracle_effective_counts_24_of_27", numerator == 24 and denominator == 27 and len(applicable_zeros) == 3 and all(x["denominator_contribution"] == "1" for x in applicable_zeros), {"numerator": numerator, "denominator": denominator, "applicable_zeros": [x["oracle_entry_id"] for x in applicable_zeros]})
    excluded_ids = {x["oracle_entry_id"] for x in effective if x["human_approved_applicability"] == "NOT_APPLICABLE"}
    approved_ids = {json.loads(x["human_evidence_reference"])["oracle_entry_id"] for x in corrected} | {supplement[0]["oracle_entry_id"]}
    check("all_three_exclusions_are_authorized", excluded_ids == approved_ids and len(excluded_ids) == 3, {"excluded": sorted(excluded_ids), "authorized": sorted(approved_ids)})

    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    check("approved_sampling_and_escalation_policy", policy["status"] == "APPROVED" and policy["targeted"] == {"records": 20, "major_errors": 2, "minor_errors": 0, "accepted_without_change": 18} and policy["random"] == {"records": 23, "major_errors": 0, "minor_errors": 0, "accepted_without_change": 23} and policy["random_frame"] == 113 and policy["sampling_unit"] == "response_x_metric_record" and policy["stratification"] == "metric_x_ai_polarity" and policy["seed"] == 29012026 and policy["systematic_error_escalation"]["triggered"] is True and "30" in policy["systematic_error_escalation"]["disposition"], policy)

    rubric = json.loads(RUBRIC.read_text(encoding="utf-8"))
    stat_text = STAT.read_text(encoding="utf-8")
    check("rubric_amendment_frozen", rubric["status"] == "FROZEN" and rubric["unchanged_metrics"] == ["CSPC", "TCC"] and rubric["planned_analysis_unit"] == "Bug-level paired comparison" and rubric["applied_stage1_counts"] == {"original": {"numerator": 25, "denominator": 30}, "effective": {"numerator": 24, "denominator": 27}}, rubric)
    stat_requirements = ["Status: **FROZEN AMENDMENT**", "Only Oracle branches applicable", "Applicable missing, partial, incorrect, or unusable", "CSPC and TCC definitions are unchanged", "Bug remains the paired statistical analysis unit"]
    check("statistical_plan_amendment_frozen", all(x in stat_text for x in stat_requirements), [x for x in stat_requirements if x not in stat_text])

    deviation_text = DEVIATION.read_text(encoding="utf-8")
    deviation_requirements = [
        "APPROVED DOCUMENTED DEVIATION", "condition assignments did not enter AI evaluator inputs",
        "36/36 CSPC accept-all decision before the access", "possible cognitive influence",
        "fresh process", "Prohibit semantic score/evidence edits", "condition-blind score lock before opening the mapping",
    ]
    check("mapping_deviation_disposition_approved_with_safeguards", all(x in deviation_text for x in deviation_requirements), [x for x in deviation_requirements if x not in deviation_text])

    # Original input hashes must still match those recorded before finalization.
    input_paths = {
        GOLD.name: GOLD, SOURCE_WB.name: SOURCE_WB, SOURCE_QUEUE.name: SOURCE_QUEUE,
        SOURCE_PROPOSED.name: SOURCE_PROPOSED, SOURCE_SUPPLEMENTAL.name: SOURCE_SUPPLEMENTAL,
        SOURCE_EFFECTIVE.name: SOURCE_EFFECTIVE, SOURCE_ORACLE_AUDIT.name: SOURCE_ORACLE_AUDIT,
        SOURCE_QC_MANIFEST.name: SOURCE_QC_MANIFEST, SOURCE_DEVIATION.name: SOURCE_DEVIATION,
        CSPC_WB.name: CSPC_WB, CSPC_VALIDATION.name: CSPC_VALIDATION,
    }
    hash_errors = {name: {"recorded": manifest["input_hashes"].get(name), "actual": sha(path)} for name, path in input_paths.items() if manifest["input_hashes"].get(name) != sha(path)}
    check("immutable_inputs_and_gold_unchanged", not hash_errors, hash_errors)
    qc_manifest = json.loads(SOURCE_QC_MANIFEST.read_text(encoding="utf-8"))
    check("original_queue_hash_unchanged", sha(SOURCE_QUEUE) == qc_manifest["output_hashes"]["immutable_queue"], sha(SOURCE_QUEUE))
    cspc_validation = json.loads(CSPC_VALIDATION.read_text(encoding="utf-8"))
    cspc_valid = (
        cspc_validation.get("status") == "PASS_READY_FOR_SEPARATE_SCORE_LOCK_STEP"
        and cspc_validation.get("expected_items") == 36
        and cspc_validation.get("actual_items") == 36
        and cspc_validation.get("explicit_valid_decisions") == 36
        and cspc_validation.get("unresolved_count") == 0
        and cspc_validation.get("override_count") == 0
        and cspc_validation.get("ai_assessment_overwrite_detected") is False
        and cspc_validation.get("condition_mapping_exposure_detected") is False
        and cspc_validation.get("workbook_sha256") == sha(CSPC_WB)
    )
    check("original_36_cspc_decisions_remain_valid", cspc_valid, {
        "status": cspc_validation.get("status"),
        "expected_items": cspc_validation.get("expected_items"),
        "actual_items": cspc_validation.get("actual_items"),
        "explicit_valid_decisions": cspc_validation.get("explicit_valid_decisions"),
        "unresolved_count": cspc_validation.get("unresolved_count"),
        "override_count": cspc_validation.get("override_count"),
        "workbook_hash_matches": cspc_validation.get("workbook_sha256") == sha(CSPC_WB),
    })

    output_paths = {x.name: x for x in [FINAL_WB, FINAL_SUPPLEMENTAL, FINAL_EFFECTIVE, SUMMARY, POLICY, RUBRIC, STAT, DEVIATION]}
    output_hash_errors = {name: {"recorded": manifest["output_hashes"].get(name), "actual": sha(path)} for name, path in output_paths.items() if manifest["output_hashes"].get(name) != sha(path)}
    check("final_output_hashes_match_manifest", not output_hash_errors, output_hash_errors)

    new_text = "\n".join(x.read_text(encoding="utf-8") for x in output_paths.values())
    actual_task_ids = sorted(set(re.findall(r"\bP3-\d{3}-[ABC]-R1\b", new_text)))
    condition_assignment_fields = [x for x in ["condition_label", "group_assignment", "bm25_score", "retrieval_rank"] if re.search(rf"\b{x}\b", new_text, re.IGNORECASE)]
    check("no_condition_mapping_or_assignment_exposed", not actual_task_ids and not condition_assignment_fields, {"task_ids": actual_task_ids, "fields": condition_assignment_fields})
    check("no_mapping_access_lock_unblind_or_comparison", manifest["restricted_mapping_accessed_during_task"] is False and manifest["score_lock_performed"] is False and manifest["unblinding_performed"] is False and manifest["condition_comparison_performed"] is False, {k: manifest[k] for k in ["restricted_mapping_accessed_during_task", "score_lock_performed", "unblinding_performed", "condition_comparison_performed"]})

    return finish(manifest)


def finish(manifest: dict | None) -> int:
    passed = all(x["status"] == "PASS" for x in checks)
    status = "PASS_READY_FOR_SCORE_LOCK" if passed else "BLOCKED_SCORE_LOCK"
    blockers = [x for x in checks if x["status"] == "FAIL"]
    report = {
        "artifact_version": "stage1-score-lock-readiness-validation-v1",
        "status": status,
        "checks_passed": sum(x["status"] == "PASS" for x in checks),
        "checks_failed": len(blockers),
        "blocking_reasons": blockers,
        "checks": checks,
        "score_lock_performed": False,
        "restricted_mapping_accessed_during_task": False,
        "unblinding_performed": False,
        "condition_comparison_performed": False,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if manifest is not None:
        manifest["status"] = status
        manifest["validation_artifacts"] = {
            FINALIZER.name: sha(FINALIZER),
            Path(__file__).name: sha(Path(__file__)),
            REPORT.name: sha(REPORT),
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "checks_passed": report["checks_passed"], "checks_failed": report["checks_failed"]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
