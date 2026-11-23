#!/usr/bin/env python3
"""Independent, read-only validator for Phase 2.11F-2 outputs."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/phase2_11F_2"
F1 = ROOT / "data/phase2_11F_1_gold_evidence/phase2_11F_1_gold_evidence.jsonl"
DECISIONS = ROOT / "gold_human_decisions_working.json"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_evidence_references(value: object, *, inside_evidence: bool = False) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        now_inside = inside_evidence or "evidence_id" in value and "excerpt" in value
        if not now_inside:
            ids = value.get("evidence_ids")
            if isinstance(ids, list):
                refs.update(item for item in ids if isinstance(item, str))
        for child in value.values():
            refs.update(collect_evidence_references(child, inside_evidence=now_inside))
    elif isinstance(value, list):
        for child in value:
            refs.update(collect_evidence_references(child, inside_evidence=inside_evidence))
    return refs


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    required_names = {
        "gold_references_human_revised.jsonl", "human_decision_application_log.jsonl",
        "gold_revision_diff.md", "gold_validation_report.json",
        "gold_final_human_review_queue.md", "phase2_11F_2_summary.md",
    }
    if not OUT.is_dir():
        print(json.dumps({"status": "FAIL", "errors": ["output directory missing"]}, indent=2))
        return 1
    actual_names = {path.name for path in OUT.iterdir() if path.is_file()}
    if actual_names != required_names:
        errors.append(f"deliverable set mismatch: {sorted(actual_names ^ required_names)}")

    records = jsonl(OUT / "gold_references_human_revised.jsonl")
    logs = jsonl(OUT / "human_decision_application_log.jsonl")
    decisions_doc = json.loads(DECISIONS.read_text(encoding="utf-8"))
    decisions = {item["bug_id"]: item for item in decisions_doc["decisions"]}
    f1_rows = jsonl(F1)
    originals = {item["bug_id"]: item["gold_candidate"] for item in f1_rows}
    by_id = {item["bug_id"]: item for item in records}
    log_by_id = {item["bug_id"]: item for item in logs}

    if len(records) != 18 or len(by_id) != 18:
        errors.append("revised Gold must contain exactly 18 unique bug IDs")
    if len(logs) != 18 or len(log_by_id) != 18:
        errors.append("application log must contain exactly 18 unique bug IDs")
    if set(by_id) != set(decisions) or set(log_by_id) != set(decisions):
        errors.append("record/log IDs do not exactly match human decision IDs")
    counts = Counter(item.get("final_human_decision") for item in records)
    if counts != Counter({"APPROVE": 8, "REVISE": 10}):
        errors.append(f"decision counts mismatch: {dict(counts)}")

    partial_ids = {
        "CorsixTH/CorsixTH#2881", "colobot/colobot#1787", "freeorion/freeorion#1897",
        "freeorion/freeorion#2365", "gemrb/gemrb#1878", "longturn/freeciv21#1189",
        "warzone2100/warzone2100#4125",
    }
    approved_explicit_complete = {
        "flame-engine/flame#1003", "flame-engine/flame#1264",
        "gameservermanagers/linuxgsm#4059", "gameservermanagers/linuxgsm#4251",
        "lichess-org/lila#12907", "rptools/maptool#2393", "rptools/maptool#2416",
    }

    for bug_id, decision in decisions.items():
        if bug_id not in by_id or bug_id not in log_by_id:
            continue
        record = by_id[bug_id]
        log = log_by_id[bug_id]
        original = originals[bug_id]
        if record.get("final_human_decision") != decision["human_decision"]:
            errors.append(f"{bug_id}: human decision changed")
        review = record.get("human_review", {})
        if review.get("decision_notes_verbatim") != decision["notes"] or log.get("human_notes_verbatim") != decision["notes"]:
            errors.append(f"{bug_id}: exact human note not preserved")
        if record.get("gold_frozen") is not False or log.get("gold_frozen") is not False:
            errors.append(f"{bug_id}: Gold was frozen")
        if record.get("runtime_reproduction_claimed") is not False:
            errors.append(f"{bug_id}: runtime reproduction was claimed")
        if record.get("evidence") != original.get("evidence"):
            errors.append(f"{bug_id}: original evidence/excerpts/provenance changed")
        evidence = {item["evidence_id"]: item for item in record["evidence"]}
        unresolved = collect_evidence_references(record) - set(evidence)
        if unresolved:
            errors.append(f"{bug_id}: unresolved evidence IDs {sorted(unresolved)}")
        for condition in record["trigger_conditions"]:
            condition_evidence = [evidence[eid] for eid in condition.get("evidence_ids", []) if eid in evidence]
            if condition.get("role") == "required_trigger" and any(
                item.get("temporal_role") == "evaluation_only_future" for item in condition_evidence
            ):
                errors.append(f"{bug_id}: required trigger depends on future repair evidence")
            if condition.get("future_repair_only") and condition.get("role") != "root_cause_only":
                errors.append(f"{bug_id}: future repair condition promoted beyond root cause")

        condition_ids = {item["condition_id"] for item in record["trigger_conditions"]}
        original_condition_ids = [item["condition_id"] for item in original["trigger_conditions"]]
        if [item["condition_id"] for item in record["trigger_conditions"]] != original_condition_ids:
            errors.append(f"{bug_id}: unsupported trigger condition added/removed/reordered")
        eligible_paths = []
        for path in record["trigger_paths"]:
            required = path["required_condition_ids"]
            if path["tcc_denominator"] != len(required):
                errors.append(f"{bug_id}/{path['path_id']}: TCC denominator mismatch")
            if not set(required) <= condition_ids:
                errors.append(f"{bug_id}/{path['path_id']}: unknown required condition")
            expression_ids = set(re.findall(r"T(?:_RC)?\w+", path["expression"]))
            if expression_ids != set(required):
                errors.append(f"{bug_id}/{path['path_id']}: expression/required-condition mismatch")
            if any(next(item for item in record["trigger_conditions"] if item["condition_id"] == cid).get("future_repair_only") for cid in required):
                errors.append(f"{bug_id}/{path['path_id']}: repair-only condition counted in TCC")
            is_partial = bug_id in partial_ids
            if is_partial and (path["complete_path_metric_eligible"] or path["cspc_eligibility"] != "not_applicable_partial_or_unsupported"):
                errors.append(f"{bug_id}/{path['path_id']}: partial path included in CSPC")
            if path["complete_path_metric_eligible"]:
                eligible_paths.append(path["path_id"])
        if record["evaluation_targets_v2"]["cspc_eligible_path_ids"] != eligible_paths:
            errors.append(f"{bug_id}: CSPC eligible-path metadata mismatch")
        denominators = {path["path_id"]: path["tcc_denominator"] for path in record["trigger_paths"]}
        if record["evaluation_targets_v2"]["tcc"]["path_denominators"] != denominators:
            errors.append(f"{bug_id}: path denominator metadata mismatch")

        for oracle in record["test_oracles"]:
            if oracle.get("acceptable_expected_outcomes") and len(oracle["acceptable_expected_outcomes"]) == 1:
                if oracle["acceptable_expected_outcomes"][0]["description"] != oracle["expected_result"]:
                    errors.append(f"{bug_id}/{oracle['oracle_id']}: expected/outcome text mismatch")
            if bug_id in approved_explicit_complete and (oracle["status"], oracle["completeness"]) != ("explicit", "complete"):
                errors.append(f"{bug_id}/{oracle['oracle_id']}: approved Oracle dimensions changed")
        if decision["human_decision"] == "REVISE":
            if not review.get("final_verification_required"):
                errors.append(f"{bug_id}: REVISE lacks final verification gate")
            if log.get("application_status") != "APPLIED_AWAITING_FINAL_HUMAN_VERIFICATION":
                errors.append(f"{bug_id}: incorrect REVISE application status")
            if not log.get("field_level_modifications"):
                errors.append(f"{bug_id}: REVISE has no field-level modifications")
        else:
            if review.get("final_verification_required"):
                errors.append(f"{bug_id}: APPROVE incorrectly queued as revised")
        for modification in log.get("field_level_modifications", []):
            if modification.get("old_value") == modification.get("new_value"):
                errors.append(f"{bug_id}: no-op modification logged at {modification.get('field')}")
            if decision["notes"] not in modification.get("human_decision_justification", ""):
                errors.append(f"{bug_id}: modification missing exact human justification")
            if not set(modification.get("supporting_evidence_ids", [])) <= set(evidence):
                errors.append(f"{bug_id}: modification cites unknown evidence")

    # Explicit high-risk adjudication boundaries.
    c1799 = by_id.get("CorsixTH/CorsixTH#1799", {})
    if c1799:
        o1 = next(item for item in c1799["test_oracles"] if item["oracle_id"] == "O1")
        op1 = next(item for item in c1799["preventive_oracles"] if item["oracle_id"] == "OP1")
        branches = {item["branch_id"]: item for item in c1799["outcome_branches"]}
        if o1.get("applicable_branch_ids") != ["B_POST"] or o1.get("ambiguity_status") != "clear":
            errors.append("CorsixTH#1799: O1 branch/ambiguity revision missing")
        if op1.get("applicable_branch_ids") != ["B_PREVENT"] or branches["B_PREVENT"].get("cspc_eligible"):
            errors.append("CorsixTH#1799: preventive branch improperly counted")
    expected_strict = {
        "longturn/freeciv21#1189": "eligible_candidate_pending_final_gold_verification",
        "tmodloader/tmodloader#2644": "pending_rubric_decision",
        "vcmi/vcmi#5462": "pending_rubric_decision",
        "warzone2100/warzone2100#4125": "ineligible_incomplete_oracle",
        "wolvenkit/wolvenkit#2279": "ineligible_incomplete_oracle",
    }
    for bug_id, expected in expected_strict.items():
        if bug_id in by_id and by_id[bug_id]["test_oracles"][0].get("strict_oracle_scoring_eligibility") != expected:
            errors.append(f"{bug_id}: strict Oracle boundary mismatch")

    report = json.loads((OUT / "gold_validation_report.json").read_text(encoding="utf-8"))
    if report.get("official_model_calls") != 0 or report.get("gold_frozen") or report.get("rubric_frozen") or report.get("ABC_manifest_frozen"):
        errors.append("governance invariant mismatch")
    for name, item in report.get("source_hashes", {}).items():
        path = ROOT / name
        if not path.is_file() or sha(path) != item.get("before") or item.get("before") != item.get("after"):
            errors.append(f"source integrity mismatch: {name}")
    if report.get("status") != "PASS_HUMAN_DECISIONS_APPLIED_UNFROZEN":
        errors.append("embedded validation report does not pass")

    warnings.extend([
        "All ten REVISE records still require final human verification.",
        "Strict Oracle eligibility remains Rubric-pending where recorded.",
        "Structural validation does not establish runtime or semantic correctness.",
    ])
    result = {
        "status": "PASS_PHASE_2_11F_2_INDEPENDENT_VALIDATION" if not errors else "FAIL_PHASE_2_11F_2_INDEPENDENT_VALIDATION",
        "gold_records": len(records), "unique_bug_ids": len(by_id),
        "human_decisions": dict(counts),
        "revise_logs": sum(item.get("human_decision") == "REVISE" and bool(item.get("field_level_modifications")) for item in logs),
        "original_evidence_preserved": not any("original evidence" in item for item in errors),
        "official_model_calls": 0, "errors": errors, "warnings": warnings,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
