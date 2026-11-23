#!/usr/bin/env python3
"""Independent read-only validation for Gold v1 freeze preparation."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/gold_v1_freeze_preparation"
GOLD = ROOT / "outputs/phase2_11F_3/gold_references_temporal_corrected.jsonl"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    errors: list[str] = []
    expected = {"gold_v1_human_acceptance_decisions.json", "strict_oracle_rubric_v1.json",
                "gold_v1_freeze_manifest_draft.json", "gold_v1_freeze_preparation_report.md"}
    actual = {path.name for path in OUT.iterdir() if path.is_file()} if OUT.is_dir() else set()
    if actual != expected:
        errors.append(f"deliverable set mismatch: {sorted(actual ^ expected)}")
    acceptance = json.loads((OUT / "gold_v1_human_acceptance_decisions.json").read_text(encoding="utf-8"))
    rubric = json.loads((OUT / "strict_oracle_rubric_v1.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUT / "gold_v1_freeze_manifest_draft.json").read_text(encoding="utf-8"))
    gold = jsonl(GOLD)
    by_id = {item["bug_id"]: item for item in gold}
    if len(gold) != 18 or len(by_id) != 18:
        errors.append("Gold is not exactly 18 unique records")
    expected_gold_hash = "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66"
    if sha(GOLD) != expected_gold_hash or manifest["candidate_gold"]["sha256"] != expected_gold_hash:
        errors.append("candidate Gold hash mismatch")
    if manifest["candidate_gold"]["record_ids_in_file_order"] != [item["bug_id"] for item in gold]:
        errors.append("manifest record IDs/order mismatch")
    accepted = acceptance["corrected_gold_acceptances"]
    if len(accepted) != 9 or len({item["bug_id"] for item in accepted}) != 9 or any(item["decision"] != "ACCEPT_CORRECTED_GOLD" for item in accepted):
        errors.append("nine corrected acceptances invalid")
    if acceptance["final_verify_preserved"] != {"bug_id": "CorsixTH/CorsixTH#1799", "decision": "FINAL_VERIFY", "source_decision_preserved": True}:
        errors.append("CorsixTH#1799 FINAL_VERIFY not preserved")
    for prior in acceptance["prior_human_decision_records"]:
        if sha(ROOT / prior["path"]) != prior["sha256"] or prior["preserved_unchanged"] is not True:
            errors.append(f"prior decision integrity mismatch: {prior['path']}")

    entries = rubric["entries"]
    dispositions = Counter(item["strict_scoring_disposition"] for item in entries)
    if len(entries) != 19 or dispositions != Counter({"INCLUDED": 10, "EXCLUDED_INELIGIBLE": 7, "EXCLUDED_PENDING": 2}):
        errors.append(f"strict Oracle counts invalid: {len(entries)}, {dict(dispositions)}")
    pending = [item for item in entries if item["strict_scoring_disposition"] == "EXCLUDED_PENDING"]
    expected_pending = {"tmodloader/tmodloader#2644", "vcmi/vcmi#5462"}
    if {item["bug_id"] for item in pending} != expected_pending:
        errors.append("pending exclusion IDs invalid")
    if any(item["underlying_gold_classification"] != "pending_rubric_decision"
           or item["underlying_classification_modified"] is not False for item in pending):
        errors.append("pending status converted or modified")
    if rubric["strict_oracle_denominator"] != 10 or rubric["strict_oracle_unique_bug_count"] != 9:
        errors.append("strict denominator not fixed to 10 Oracles / 9 bugs")
    if rubric["strict_denominator_fixed_before_model_evaluation"] is not True:
        errors.append("strict denominator is not fixed")
    for entry in entries:
        source = next(item for item in by_id[entry["bug_id"]]["test_oracles"] if item["oracle_id"] == entry["oracle_id"])
        if entry["underlying_gold_classification"] != source["strict_oracle_scoring_eligibility"]:
            errors.append(f"underlying classification mismatch: {entry['bug_id']}/{entry['oracle_id']}")
    for bug_id in expected_pending:
        record = by_id[bug_id]
        if not record["evaluation_targets_v2"]["cspc_eligible_path_ids"]:
            errors.append(f"{bug_id}: pending strict record lost CSPC eligibility")
        if not all(path["tcc_denominator"] == len(path["required_condition_ids"]) for path in record["trigger_paths"]):
            errors.append(f"{bug_id}: pending strict record TCC invalid")

    counts = manifest["metric_eligibility_counts"]
    expected_counts = {
        "gold_records": 18, "tcc_records_total": 18, "tcc_paths_total": 19,
        "tcc_required_condition_denominator_sum_across_paths": 24,
        "tcc_confirmatory_complete_records": 11, "tcc_partial_exploratory_records": 7,
        "cspc_eligible_records": 11, "cspc_eligible_paths": 12,
        "cspc_ineligible_partial_records": 7, "scenario_adequacy_required_records": 18,
        "strict_oracle_eligible_oracles": 10, "strict_oracle_eligible_unique_records": 9,
        "strict_oracle_pending_excluded_oracles": 2, "strict_oracle_ineligible_excluded_oracles": 7,
    }
    if counts != expected_counts:
        errors.append(f"metric eligibility counts mismatch: {counts}")
    if manifest["strict_oracle_rubric"]["sha256"] != sha(OUT / "strict_oracle_rubric_v1.json"):
        errors.append("rubric hash mismatch")
    if manifest["human_acceptance"]["sha256"] != sha(OUT / "gold_v1_human_acceptance_decisions.json"):
        errors.append("acceptance hash mismatch")
    boundary = manifest["generation_boundary"]
    if sha(ROOT / boundary["serializer_path"]) != boundary["serializer_sha256"]:
        errors.append("serializer hash mismatch")
    if sha(ROOT / boundary["safe_projection_path"]) != boundary["safe_projection_sha256"]:
        errors.append("safe projection hash mismatch")
    if boundary["f4_lite_engineering_blocker_count"] != 0 or boundary["validation_status"] != "PASS_ENFORCED_APPROVED_SERIALIZER_BOUNDARY":
        errors.append("generation boundary not technically ready")
    if any(item["gold_frozen"] for item in gold):
        errors.append("a Gold record is frozen")
    forbidden_true = [acceptance["human_freeze_authorization"], acceptance["dataset_frozen"],
                      acceptance["phase3_experiments_authorized"], manifest["freeze_authorized"],
                      manifest["gold_frozen"], manifest["phase3_experiments_authorized"]]
    if any(forbidden_true) or manifest["official_model_calls"] != 0:
        errors.append("freeze/experiment governance violation")

    result = {
        "status": "PASS_GOLD_V1_FREEZE_PREPARATION" if not errors else "FAIL_GOLD_V1_FREEZE_PREPARATION",
        "gold_records": len(gold), "accepted_corrected_records": len(accepted),
        "strict_oracle_denominator": rubric["strict_oracle_denominator"],
        "strict_pending_excluded": dispositions["EXCLUDED_PENDING"],
        "engineering_blockers": boundary["f4_lite_engineering_blocker_count"],
        "freeze_authorized": False, "gold_frozen": False, "phase3_authorized": False,
        "official_model_calls": 0, "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
