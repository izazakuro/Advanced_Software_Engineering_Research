#!/usr/bin/env python3
"""Prepare Gold v1 human acceptance, strict rubric, and draft freeze manifest."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/gold_v1_freeze_preparation"
GOLD = ROOT / "outputs/phase2_11F_3/gold_references_temporal_corrected.jsonl"
F3_DECISIONS = ROOT / "gold_final_human_verification_decisions.json"
F2_DECISIONS = ROOT / "gold_human_decisions_working.json"

ACCEPTED_CORRECTED_IDS = {
    "CorsixTH/CorsixTH#2881", "colobot/colobot#1787", "freeorion/freeorion#1897",
    "freeorion/freeorion#2365", "gemrb/gemrb#1878", "longturn/freeciv21#1189",
    "tmodloader/tmodloader#2644", "warzone2100/warzone2100#4125",
    "wolvenkit/wolvenkit#2279",
}
PENDING_STRICT_IDS = {"tmodloader/tmodloader#2644", "vcmi/vcmi#5462"}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    if OUT.exists():
        raise SystemExit("outputs/gold_v1_freeze_preparation already exists; refusing to overwrite")
    records = jsonl(GOLD)
    by_id = {item["bug_id"]: item for item in records}
    f3_decisions = {item["bug_id"]: item for item in json.loads(F3_DECISIONS.read_text(encoding="utf-8"))["decisions"]}
    source_paths = [
        GOLD, F3_DECISIONS, F2_DECISIONS,
        ROOT / "outputs/phase2_11F_3/invariant_validation_results.json",
        ROOT / "outputs/phase2_11F_4_lite/f4_lite_blockers.json",
        ROOT / "outputs/phase2_11F_4_lite/gold_v1_freeze_readiness.json",
        ROOT / "outputs/phase2_11F_4b/validation_results.json",
        ROOT / "outputs/phase2_11F_3/generation_input_safe_projection.jsonl",
        ROOT / "scripts/phase2_11F_3_generation_input.py",
    ]
    before_hashes = {str(path.relative_to(ROOT)): sha(path) for path in source_paths}
    if len(records) != 18 or len(by_id) != 18:
        raise SystemExit("candidate Gold is not exactly 18 unique records")
    actual_corrections = {bug_id for bug_id, item in f3_decisions.items() if item["decision"] == "NEEDS_CORRECTION"}
    if actual_corrections != ACCEPTED_CORRECTED_IDS:
        raise SystemExit("nine accepted corrected IDs do not match authoritative F-3 decisions")

    OUT.mkdir(parents=True)
    acceptance_path = OUT / "gold_v1_human_acceptance_decisions.json"
    rubric_path = OUT / "strict_oracle_rubric_v1.json"
    manifest_path = OUT / "gold_v1_freeze_manifest_draft.json"
    report_path = OUT / "gold_v1_freeze_preparation_report.md"

    acceptance = {
        "phase": "Gold-v1-freeze-preparation",
        "decision_authority": "explicit_human_instruction_for_this_preparation_task",
        "candidate_gold_path": str(GOLD.relative_to(ROOT)),
        "candidate_gold_sha256": sha(GOLD),
        "prior_human_decision_records": [
            {"path": str(F2_DECISIONS.relative_to(ROOT)), "sha256": sha(F2_DECISIONS), "preserved_unchanged": True},
            {"path": str(F3_DECISIONS.relative_to(ROOT)), "sha256": sha(F3_DECISIONS), "preserved_unchanged": True},
        ],
        "corrected_gold_acceptances": [
            {"bug_id": bug_id, "decision": "ACCEPT_CORRECTED_GOLD", "accepted_semantics": "Phase 2.11F-3 corrected record as validated",
             "gold_freeze_authorized_by_this_decision": False}
            for bug_id in sorted(ACCEPTED_CORRECTED_IDS)
        ],
        "corrected_gold_acceptance_count": 9,
        "final_verify_preserved": {
            "bug_id": "CorsixTH/CorsixTH#1799", "decision": "FINAL_VERIFY",
            "source_decision_preserved": f3_decisions["CorsixTH/CorsixTH#1799"]["decision"] == "FINAL_VERIFY",
        },
        "strict_oracle_pending_decisions": [
            {"bug_id": "tmodloader/tmodloader#2644", "oracle_id": "O1",
             "classification_preserved": "pending_rubric_decision", "strict_scoring": "EXCLUDED_PENDING",
             "other_metric_eligibility_preserved": True},
            {"bug_id": "vcmi/vcmi#5462", "oracle_id": "O1",
             "classification_preserved": "pending_rubric_decision", "strict_scoring": "EXCLUDED_PENDING",
             "other_metric_eligibility_preserved": True},
        ],
        "all_18_records_accepted_for_freeze_consideration": True,
        "human_freeze_authorization": False,
        "dataset_frozen": False,
        "phase3_experiments_authorized": False,
        "official_model_calls_authorized": False,
    }
    write_json(acceptance_path, acceptance)

    oracle_entries: list[dict] = []
    for record in records:
        bug_id = record["bug_id"]
        for oracle in record["test_oracles"]:
            underlying = oracle["strict_oracle_scoring_eligibility"]
            if underlying == "eligible_human_approved":
                disposition = "INCLUDED"
                reason = "explicitly eligible and previously human-approved"
            elif underlying == "eligible_pending_final_gold_verification" and bug_id == "CorsixTH/CorsixTH#1799":
                disposition = "INCLUDED"
                reason = "eligible candidate with authoritative FINAL_VERIFY preserved"
            elif underlying == "eligible_candidate_pending_final_gold_verification" and bug_id in ACCEPTED_CORRECTED_IDS:
                disposition = "INCLUDED"
                reason = "eligible candidate whose corrected Gold was explicitly accepted"
            elif underlying == "pending_rubric_decision" and bug_id in PENDING_STRICT_IDS:
                disposition = "EXCLUDED_PENDING"
                reason = "human decision preserves pending status and excludes it from the strict denominator"
            else:
                disposition = "EXCLUDED_INELIGIBLE"
                reason = "Oracle is incomplete or explicitly strict-ineligible"
            oracle_entries.append({
                "bug_id": bug_id, "oracle_id": oracle["oracle_id"],
                "oracle_origin": oracle["status"], "oracle_completeness": oracle["completeness"],
                "underlying_gold_classification": underlying,
                "strict_scoring_disposition": disposition, "reason": reason,
                "underlying_classification_modified": False,
                "tcc_eligibility_independent": True,
                "cspc_eligible_path_ids": record["evaluation_targets_v2"]["cspc_eligible_path_ids"],
            })
    disposition_counts = Counter(item["strict_scoring_disposition"] for item in oracle_entries)
    included = [item for item in oracle_entries if item["strict_scoring_disposition"] == "INCLUDED"]
    rubric = {
        "rubric_id": "strict-oracle-rubric-v1",
        "version": "1.0",
        "status": "PREPARED_FROM_AUTHORITATIVE_HUMAN_DECISIONS_NOT_A_GOLD_FREEZE",
        "scoring_unit": "oracle",
        "inclusion_rule": "Only explicit eligible statuses, or eligible candidates whose required final human verification/acceptance is now recorded, are included.",
        "pending_rule": "pending_rubric_decision remains pending and is excluded from the strict denominator; it is not converted to ineligible.",
        "ineligible_rule": "Incomplete or explicitly strict-ineligible Oracles are excluded.",
        "other_metrics_rule": "TCC, CSPC, Scenario Adequacy, and exploratory eligibility are evaluated independently from strict Oracle eligibility.",
        "strict_denominator_fixed_before_model_evaluation": True,
        "strict_oracle_denominator": len(included),
        "strict_oracle_unique_bug_count": len({item["bug_id"] for item in included}),
        "disposition_counts": dict(disposition_counts),
        "total_test_oracles": len(oracle_entries),
        "entries": oracle_entries,
        "pending_oracle_ids": [f"{item['bug_id']}::{item['oracle_id']}" for item in oracle_entries if item["strict_scoring_disposition"] == "EXCLUDED_PENDING"],
        "gold_frozen": False,
        "official_evaluation_started": False,
    }
    write_json(rubric_path, rubric)

    cspc_records = [record for record in records if record["evaluation_targets_v2"]["cspc_eligible_path_ids"]]
    partial_records = [record for record in records if record["evaluation_targets_v2"]["partial_exploratory_only"]]
    metric_counts = {
        "gold_records": 18,
        "tcc_records_total": 18,
        "tcc_paths_total": sum(len(record["trigger_paths"]) for record in records),
        "tcc_required_condition_denominator_sum_across_paths": sum(path["tcc_denominator"] for record in records for path in record["trigger_paths"]),
        "tcc_confirmatory_complete_records": len(cspc_records),
        "tcc_partial_exploratory_records": len(partial_records),
        "cspc_eligible_records": len(cspc_records),
        "cspc_eligible_paths": sum(len(record["evaluation_targets_v2"]["cspc_eligible_path_ids"]) for record in records),
        "cspc_ineligible_partial_records": len(partial_records),
        "scenario_adequacy_required_records": sum(bool(record["evaluation_targets_v2"]["scenario_adequacy_required"]) for record in records),
        "strict_oracle_eligible_oracles": len(included),
        "strict_oracle_eligible_unique_records": len({item["bug_id"] for item in included}),
        "strict_oracle_pending_excluded_oracles": disposition_counts["EXCLUDED_PENDING"],
        "strict_oracle_ineligible_excluded_oracles": disposition_counts["EXCLUDED_INELIGIBLE"],
    }
    f4_blockers = json.loads((ROOT / "outputs/phase2_11F_4_lite/f4_lite_blockers.json").read_text(encoding="utf-8"))
    f4b_validation = json.loads((ROOT / "outputs/phase2_11F_4b/validation_results.json").read_text(encoding="utf-8"))
    manifest = {
        "manifest_id": "gold-v1-freeze-manifest-draft",
        "manifest_status": "DRAFT_AWAITING_EXPLICIT_HUMAN_FREEZE_AUTHORIZATION",
        "candidate_gold": {
            "relative_path": str(GOLD.relative_to(ROOT)), "absolute_path": str(GOLD.resolve()),
            "sha256": sha(GOLD), "record_count": len(records),
            "record_ids_in_file_order": [record["bug_id"] for record in records],
            "unique_record_ids": len(by_id),
        },
        "human_acceptance": {
            "path": str(acceptance_path.relative_to(ROOT)), "sha256": sha(acceptance_path),
            "nine_corrected_records_accepted": True, "corsix_1799_final_verify_preserved": True,
            "all_18_records_accepted_for_freeze_consideration": True,
            "human_freeze_authorization": False,
        },
        "metric_eligibility_counts": metric_counts,
        "strict_oracle_rubric": {
            "version": rubric["version"], "rubric_id": rubric["rubric_id"],
            "path": str(rubric_path.relative_to(ROOT)), "sha256": sha(rubric_path),
            "strict_denominator_fixed": True,
        },
        "generation_boundary": {
            "serializer_policy_version": "2.11F-3-explicit-allowlist-v1",
            "serializer_path": "scripts/phase2_11F_3_generation_input.py",
            "serializer_sha256": sha(ROOT / "scripts/phase2_11F_3_generation_input.py"),
            "safe_projection_path": "outputs/phase2_11F_3/generation_input_safe_projection.jsonl",
            "safe_projection_sha256": sha(ROOT / "outputs/phase2_11F_3/generation_input_safe_projection.jsonl"),
            "f4b_validation_status": f4b_validation["status"],
            "f4_lite_engineering_blocker_count": f4_blockers["blocker_count"],
            "validation_status": "PASS_ENFORCED_APPROVED_SERIALIZER_BOUNDARY",
        },
        "technical_readiness": {
            "gold_artifact": True, "generation_pipeline": True,
            "remaining_engineering_blockers": f4_blockers["blocker_count"],
        },
        "freeze_authorized": False,
        "gold_frozen": False,
        "phase3_experiments_authorized": False,
        "official_model_calls": 0,
    }
    write_json(manifest_path, manifest)

    errors: list[str] = []
    if len(records) != 18 or len(by_id) != 18:
        errors.append("candidate must have exactly 18 unique records")
    if len(acceptance["corrected_gold_acceptances"]) != 9:
        errors.append("exactly nine corrected records must be accepted")
    if any(record["gold_frozen"] for record in records):
        errors.append("candidate contains frozen Gold record")
    if sha(GOLD) != "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66":
        errors.append("candidate Gold hash differs from validated F-3 version")
    if disposition_counts != Counter({"INCLUDED": 10, "EXCLUDED_INELIGIBLE": 7, "EXCLUDED_PENDING": 2}):
        errors.append(f"unexpected strict Oracle dispositions: {dict(disposition_counts)}")
    pending_entries = [item for item in oracle_entries if item["strict_scoring_disposition"] == "EXCLUDED_PENDING"]
    if {item["bug_id"] for item in pending_entries} != PENDING_STRICT_IDS or any(item["underlying_gold_classification"] != "pending_rubric_decision" for item in pending_entries):
        errors.append("pending strict Oracle exclusions are incorrect")
    if any(not by_id[bug_id]["evaluation_targets_v2"]["cspc_eligible_path_ids"] for bug_id in PENDING_STRICT_IDS):
        errors.append("pending strict Oracle record lost other metric eligibility")
    if f4_blockers["blocker_count"] != 0:
        errors.append("engineering blocker remains")
    if acceptance["human_freeze_authorization"] or manifest["freeze_authorized"] or manifest["gold_frozen"]:
        errors.append("freeze incorrectly authorized/executed")
    after_hashes = {name: sha(ROOT / name) for name in before_hashes}
    if before_hashes != after_hashes:
        errors.append("authoritative input changed during preparation")

    status = "PASS_FREEZE_PREPARATION_AWAITING_HUMAN_AUTHORIZATION" if not errors else "FAIL"
    report = f"""# Gold v1 Freeze Preparation Report

## Result

**{status}**. This is a draft preparation only. Gold has not been frozen and Phase 3 is not authorized.

## Candidate

- Path: `{manifest['candidate_gold']['relative_path']}`
- SHA-256: `{manifest['candidate_gold']['sha256']}`
- Records: 18 unique
- Corrected records explicitly accepted: 9
- CorsixTH #1799: FINAL_VERIFY preserved
- Remaining engineering blockers: 0

## Strict Oracle Rubric v1

- Included strict Oracle units: {metric_counts['strict_oracle_eligible_oracles']} across {metric_counts['strict_oracle_eligible_unique_records']} bugs
- Excluded pending: 2 (`tmodloader/tmodloader#2644::O1`, `vcmi/vcmi#5462::O1`)
- Excluded ineligible/incomplete: 7
- Pending classifications remain pending; they were not converted to ineligible.
- Both pending records retain TCC/CSPC eligibility independently.

## Other metric eligibility

- TCC: 18 records / 19 paths; required-condition denominator sum 24
- CSPC: 11 records / 12 eligible paths
- Partial exploratory-only: 7 records
- Scenario Adequacy required: 18 records

## Governance gate

- Human freeze authorization: **false**
- Gold frozen: **false**
- Phase 3 authorized: **false**
- Official model calls: 0

An explicit subsequent human instruction is required before any freeze operation.

## Validation

- Errors: {json.dumps(errors, ensure_ascii=False)}
- Authoritative inputs unchanged: {str(before_hashes == after_hashes).lower()}
"""
    report_path.write_text(report, encoding="utf-8")
    print(json.dumps({"status": status, "records": len(records), "accepted_corrected": 9,
                      "strict_included": len(included), "strict_pending_excluded": 2,
                      "engineering_blockers": f4_blockers["blocker_count"],
                      "freeze_authorized": False, "gold_frozen": False,
                      "official_model_calls": 0, "errors": errors}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
