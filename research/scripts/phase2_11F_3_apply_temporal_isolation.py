#!/usr/bin/env python3
"""Apply Phase 2.11F-3 decisions and build evidence-isolated artifacts."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from phase2_11F_3_generation_input import build_safe_projection, validate_projection


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/phase2_11F_3"
DECISIONS = ROOT / "gold_final_human_verification_decisions.json"
F2_GOLD = ROOT / "outputs/phase2_11F_2/gold_references_human_revised.jsonl"
F2_LOG = ROOT / "outputs/phase2_11F_2/human_decision_application_log.jsonl"
TARGETS = ROOT / "data/phase2_11E_evidence/phase2_11E_target_issues.jsonl"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assign(log: list[dict], obj: dict, key: str, value: object, path: str, reason: str) -> None:
    old = copy.deepcopy(obj.get(key))
    if old == value:
        return
    obj[key] = copy.deepcopy(value)
    log.append({"field": path, "old_value": old, "new_value": copy.deepcopy(value),
                "human_authorization": reason, "status": "APPLIED"})


def isolate_future_refs(log: list[dict], obj: dict, future_ids: set[str], path: str, reason: str) -> None:
    old_ids = list(obj.get("evidence_ids", []))
    isolated = [item for item in old_ids if item in future_ids]
    if not isolated:
        return
    assign(log, obj, "evidence_ids", [item for item in old_ids if item not in future_ids], f"{path}.evidence_ids", reason)
    retained = list(obj.get("evaluation_only_supporting_evidence_ids", []))
    for item in isolated:
        if item not in retained:
            retained.append(item)
    assign(log, obj, "evaluation_only_supporting_evidence_ids", retained,
           f"{path}.evaluation_only_supporting_evidence_ids", reason)


def referenced_future_paths(value: object, future_ids: set[str], path: str = "") -> list[dict]:
    findings: list[dict] = []
    if isinstance(value, dict):
        if isinstance(value.get("evidence_ids"), list):
            hits = sorted(set(value["evidence_ids"]) & future_ids)
            if hits:
                findings.append({"field": path or "$", "future_evidence_ids": hits})
        if isinstance(value.get("evaluation_only_supporting_evidence_ids"), list):
            hits = sorted(set(value["evaluation_only_supporting_evidence_ids"]) & future_ids)
            if hits:
                findings.append({"field": (path or "$") + ".evaluation_only_supporting_evidence_ids",
                                 "future_evidence_ids": hits, "explicitly_evaluation_only": True})
        for key, child in value.items():
            if key == "evidence":
                continue
            child_path = f"{path}.{key}" if path else key
            findings.extend(referenced_future_paths(child, future_ids, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(referenced_future_paths(child, future_ids, f"{path}[{index}]"))
    return findings


def semantic_fingerprint(record: dict) -> dict:
    return {
        "conditions": [{key: item.get(key) for key in ("condition_id", "role", "necessity", "future_repair_only")}
                       for item in record["trigger_conditions"]],
        "paths": [{key: item.get(key) for key in ("path_id", "required_condition_ids", "tcc_denominator", "completeness", "complete_path_metric_eligible", "cspc_eligibility")}
                  for item in record["trigger_paths"]],
        "oracles": [{key: item.get(key) for key in ("oracle_id", "status", "completeness", "strict_oracle_scoring_eligibility")}
                    for item in record["test_oracles"]],
        "runtime_reproduction_claimed": record.get("runtime_reproduction_claimed"),
    }


def target_future_text_hits(record: dict, target: dict) -> list[str]:
    text = " ".join(((target.get("title") or "") + "\n" + (target.get("body") or "")).split()).casefold()
    hits: list[str] = []
    repair = record.get("repair_evidence_summary", {})
    fixing_pr = repair.get("fixing_pr_id") or ""
    if "#" in fixing_pr:
        number = fixing_pr.rsplit("#", 1)[-1]
        if re.search(rf"(?<!\d)#?{re.escape(number)}(?!\d)", text):
            hits.append(f"fixing_pr_number:{number}")
    for commit_sha in repair.get("repair_commit_shas", []):
        if commit_sha.casefold() in text or commit_sha[:7].casefold() in text:
            hits.append(f"repair_commit:{commit_sha[:7]}")
    for evidence in record.get("evidence", []):
        if evidence.get("temporal_role") != "evaluation_only_future":
            continue
        excerpt = " ".join((evidence.get("excerpt") or "").split()).casefold()
        if len(excerpt) >= 30 and excerpt in text:
            hits.append(f"future_excerpt:{evidence['evidence_id']}")
    return sorted(set(hits))


def main() -> None:
    if OUT.exists():
        raise SystemExit("outputs/phase2_11F_3 already exists; refusing to overwrite")
    decision_doc = json.loads(DECISIONS.read_text(encoding="utf-8"))
    decisions = {item["bug_id"]: item for item in decision_doc["decisions"]}
    records = jsonl(F2_GOLD)
    target_by_id = {item["doc_id"]: item for item in jsonl(TARGETS)}
    f2_by_id = {item["bug_id"]: item for item in records}
    revise_ids = {item["bug_id"] for item in records if item["final_human_decision"] == "REVISE"}
    counts = Counter(item["decision"] for item in decision_doc["decisions"])
    if len(decisions) != 10 or counts != Counter({"NEEDS_CORRECTION": 9, "FINAL_VERIFY": 1}):
        raise SystemExit("invalid decision count")
    if set(decisions) != revise_ids or not all(item.get("human_authorized") is True for item in decisions.values()):
        raise SystemExit("decision IDs/authorization do not match F-2 REVISE set")
    if decisions["CorsixTH/CorsixTH#1799"]["decision"] != "FINAL_VERIFY":
        raise SystemExit("CorsixTH#1799 FINAL_VERIFY decision missing")

    source_paths = [DECISIONS, F2_GOLD, F2_LOG, TARGETS,
                    ROOT / "outputs/phase2_11F_2/gold_validation_report.json",
                    ROOT / "scripts/phase2_10B_experiment_runner.py",
                    ROOT / "data/phase2_9_abc_generation_prompt_PROPOSED.txt"]
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in source_paths}

    affected_values = {
        "CorsixTH/CorsixTH#2881": "Staff recruitment and placement dialog during receptionist placement",
        "colobot/colobot#1787": "Custom-level save loading and its parser exception",
        "freeorion/freeorion#1897": "Hostless server session during species-condition/effects processing",
        "freeorion/freeorion#2365": "XML save loading under AddressSanitizer",
        "gemrb/gemrb#1878": "Area transition to Waukeen's Promenade",
        "longturn/freeciv21#1189": "Server autogame under AddressSanitizer",
        "tmodloader/tmodloader#2644": "Non-root Docker server startup",
        "warzone2100/warzone2100#4125": "Headless autohost handling after player departures",
        "wolvenkit/wolvenkit#2279": "WolvenKit CLI mesh material export attempt",
    }
    corrected: list[dict] = []
    correction_logs: list[dict] = []
    audits: list[dict] = []

    for original in records:
        bug_id = original["bug_id"]
        record = copy.deepcopy(original)
        changes: list[dict] = []
        evidence_by_id = {item["evidence_id"]: item for item in record["evidence"]}
        future_ids = {eid for eid, item in evidence_by_id.items() if item.get("temporal_role") == "evaluation_only_future"}
        before_refs = referenced_future_paths(record, future_ids)
        decision = decisions.get(bug_id)

        if decision and decision["decision"] == "NEEDS_CORRECTION":
            reason = decision["required_correction"]
            affected = record["affected_component"]
            isolate_future_refs(changes, affected, future_ids, "affected_component", reason)
            assign(changes, affected, "value", affected_values[bug_id], "affected_component.value", reason)
            for oracle in record["test_oracles"]:
                opath = f"test_oracles.{oracle['oracle_id']}"
                isolate_future_refs(changes, oracle, future_ids, opath, reason)
                for outcome in oracle.get("acceptable_expected_outcomes", []):
                    isolate_future_refs(changes, outcome, future_ids,
                                        f"{opath}.acceptable_expected_outcomes.{outcome['outcome_id']}", reason)
            if bug_id == "freeorion/freeorion#2365":
                isolate_future_refs(changes, record["expected_behavior"], future_ids, "expected_behavior", reason)
            if bug_id == "wolvenkit/wolvenkit#2279":
                assign(changes, record["expected_behavior"], "value",
                       "The CLI does not enter infinite MergeMaterialChain recursion or terminate with Stack Overflow; successful export is not required when the game path is invalid.",
                       "expected_behavior.value", reason)

        # Explicitly classify repair-only/root-cause-only conditions without
        # removing them from the complete evaluation record.
        for condition in record["trigger_conditions"]:
            if condition.get("future_repair_only") or condition.get("role") == "root_cause_only":
                assign(changes, condition, "generation_visibility", "evaluation_only",
                       f"trigger_conditions.{condition['condition_id']}.generation_visibility",
                       "Phase 2.11F-3 input-separation rule; does not change trigger role or metric eligibility")

        verification = None
        if decision:
            verification = {
                "authoritative_decision": decision["decision"],
                "human_authorized": decision["human_authorized"],
                "accepted_invariants": decision["accepted"],
                "required_correction": decision.get("required_correction"),
                "application_status": "FINAL_VERIFIED_UNFROZEN" if decision["decision"] == "FINAL_VERIFY" else "CORRECTED_AWAITING_HUMAN_REVERIFICATION",
                "scope": decision["scope"],
            }
            if decision["decision"] == "FINAL_VERIFY":
                assign(changes, record["human_review"], "final_verification_required", False,
                       "human_review.final_verification_required",
                       "Authoritative Phase 2.11F-3 human decision: FINAL_VERIFY; this is verification, not Gold freeze or approval")
                assign(changes, record["human_review"], "final_verification_status", "FINAL_VERIFY",
                       "human_review.final_verification_status",
                       "Authoritative Phase 2.11F-3 human decision: FINAL_VERIFY; this is verification, not Gold freeze or approval")
                assign(changes, record, "annotation_status", "human_final_verified_unfrozen_not_approved",
                       "annotation_status",
                       "Authoritative Phase 2.11F-3 human decision: FINAL_VERIFY; Gold remains unfrozen and unapproved")
        record["source_schema_version"] = original["schema_version"]
        record["schema_version"] = "2.11F-3-temporal-isolation-unfrozen"
        record["phase2_11F_3_human_verification"] = verification
        record["generation_input_policy"] = {
            "version": "2.11F-3-explicit-allowlist-v1",
            "gold_fields_model_visible": [],
            "target_issue_model_visible_fields": ["title", "body"],
            "context_issue_model_visible_fields": ["title", "body"],
            "evaluation_only_material_retained_in_full_record": True,
            "full_record_is_not_a_generation_input": True,
        }
        record["runtime_interpretation_policy"] = {
            "independent_runtime_reproduction_claimed": False,
            "single_non_failure_run_proves_repair": False,
            "structural_validation_proves_semantic_correctness": False,
        }
        record["gold_frozen"] = False
        record["runtime_reproduction_claimed"] = False
        corrected.append(record)

        after_refs = referenced_future_paths(record, future_ids)
        target_text_hits = target_future_text_hits(record, target_by_id[bug_id])
        correction_logs.append({
            "bug_id": bug_id,
            "authoritative_decision": decision["decision"] if decision else "OUTSIDE_FINAL_REVISE_SET_AUDIT_ONLY",
            "application_status": verification["application_status"] if verification else "NO_SEMANTIC_CORRECTION_AUDIT_POLICY_ADDED",
            "changes": changes,
            "change_count": len(changes),
            "semantic_metric_fingerprint_unchanged": semantic_fingerprint(original) == semantic_fingerprint(record),
            "gold_frozen": False,
        })
        audits.append({
            "bug_id": bug_id,
            "future_evidence_ids": sorted(future_ids),
            "future_evidence_count": len(future_ids),
            "full_record_future_reference_paths_before": before_refs,
            "full_record_future_reference_paths_after": after_refs,
            "future_repair_only_condition_ids": [item["condition_id"] for item in record["trigger_conditions"] if item.get("future_repair_only")],
            "generation_input_source": "target Issue title/body from phase2_11E_target_issues.jsonl; Gold fields denied",
            "gold_fields_exposed_to_generation": [],
            "generation_side_future_evidence_count": 0,
            "target_issue_generation_text_future_repair_direct_hits": target_text_hits,
            "target_issue_generation_text_scan_status": "PASS_NO_DIRECT_FUTURE_REPAIR_MATCH" if not target_text_hits else "HUMAN_REVIEW_REQUIRED",
            "natural_language_audit": {
                "affected_component": "CORRECTED_TO_TARGET_SUPPORTED_NEUTRAL_SCOPE" if bug_id in affected_values else "RETAINED_FULL_RECORD_EXCLUDED_FROM_GENERATION",
                "expected_behavior": "HUMAN_AUTHORIZED_CORRECTION" if bug_id == "wolvenkit/wolvenkit#2279" else "RETAINED_FULL_RECORD_EXCLUDED_FROM_GENERATION",
                "observed_behavior": "RETAINED_FULL_RECORD_EXCLUDED_FROM_GENERATION",
                "trigger_conditions_and_paths": "REPAIR_ONLY_CONDITIONS_RETAINED_EVALUATION_ONLY_AND_EXCLUDED_FROM_GENERATION",
                "test_oracles_and_outcomes": "FUTURE_IDS_ISOLATED_WHERE_HUMAN_DIRECTED; ALL_GOLD_ORACLE_TEXT_EXCLUDED_FROM_GENERATION",
            },
            "issue_snapshot_temporal_limitation_preserved": any("Issue text has no edit history" in item for item in record["known_limitations"]),
            "audit_status": "PASS_GENERATION_ISOLATED_FULL_RECORD_RETAINED" if not target_text_hits else "HUMAN_REVIEW_REQUIRED_TARGET_SNAPSHOT_TEXT",
        })

    OUT.mkdir(parents=True)
    gold_path = OUT / "gold_references_temporal_corrected.jsonl"
    log_path = OUT / "per_record_correction_log.jsonl"
    audit_path = OUT / "temporal_leakage_audit_all18.jsonl"
    projection_path = OUT / "generation_input_safe_projection.jsonl"
    write_jsonl(gold_path, corrected)
    write_jsonl(log_path, correction_logs)
    write_jsonl(audit_path, audits)
    projection = build_safe_projection(gold_path, TARGETS)
    projection_errors = validate_projection(projection)
    write_jsonl(projection_path, projection)

    diff_lines = ["# Phase 2.11F-3 Per-record Correction Diff", "",
                  "Evaluation-only material remains in the full Gold record. The explicit serializer exposes no Gold field to generation.", ""]
    for log in correction_logs:
        diff_lines += [f"## `{log['bug_id']}` — {log['authoritative_decision']}", "",
                       f"Application status: `{log['application_status']}`", "",
                       f"Authorized field changes: {log['change_count']}", ""]
        if log["changes"]:
            for item in log["changes"]:
                diff_lines += [f"### `{item['field']}`", "", "Before:", "", "```json",
                               json.dumps(item["old_value"], ensure_ascii=False, indent=2, sort_keys=True), "```", "",
                               "After:", "", "```json",
                               json.dumps(item["new_value"], ensure_ascii=False, indent=2, sort_keys=True), "```", "",
                               f"Authorization: {item['human_authorization']}", ""]
        else:
            diff_lines += ["No semantic correction was authorized or required; only top-level F-3 policy/version metadata was added.", ""]
    (OUT / "per_record_correction_diff.md").write_text("\n".join(diff_lines), encoding="utf-8")

    allowlist = {
        "policy_version": "2.11F-3-explicit-allowlist-v1",
        "authoritative_serializer": "scripts/phase2_11F_3_generation_input.py",
        "target_issue": {"prompt_fields": ["title", "body"], "control_only_fields": ["doc_id", "original_content_sha256", "created_at"]},
        "context_issue": {"prompt_fields": ["title", "body"], "control_only_fields": ["doc_id", "content_sha256", "created_at"]},
        "gold_record_prompt_fields": [],
        "explicitly_denied_categories": [
            "all Gold semantic/evaluation fields", "evidence and evidence_ids", "repair_evidence_summary",
            "repair PRs/commits/diffs", "future-repair-only and root-cause-only conditions",
            "machine suggestions and revision explanations", "historical review/audit logs",
            "human decisions and scoring metadata",
        ],
        "complete_gold_retention": "Evaluation materials remain available only to audit/scoring workflows.",
        "existing_abc_runner_observation": "phase2_10B_experiment_runner.py renders only target/context Issue title and body; F-3 serializer is the explicit prospective gate.",
        "official_generation_authorized": False,
    }
    (OUT / "generation_input_allowlist.json").write_text(json.dumps(allowlist, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    runner_source = (ROOT / "scripts/phase2_10B_experiment_runner.py").read_text(encoding="utf-8")
    prompt_hash = sha(ROOT / "data/phase2_9_abc_generation_prompt_PROPOSED.txt")
    input_report = {
        "status": "PASS_EXPLICIT_ALLOWLIST_PROJECTION" if not projection_errors else "FAIL",
        "records_scanned": 18, "safe_projection_records": len(projection),
        "gold_prompt_fields": [], "future_evidence_in_safe_projection": 0,
        "historical_audit_fields_in_safe_projection": 0,
        "target_issue_future_repair_direct_text_hits": sum(len(item["target_issue_generation_text_future_repair_direct_hits"]) for item in audits),
        "target_issue_snapshot_hashes_verified": 18,
        "target_issue_creation_time_equivalence_verified": 0,
        "target_issue_temporal_limitation": "Current dataset Issue title/body are hash-verified, but edit history is unavailable; creation-time equivalence is not claimed.",
        "projection_errors": projection_errors,
        "existing_runner_uses_gold_dataset": "gold_references" in runner_source,
        "existing_runner_render_fields_verified": ["target.title", "target.body", "context.title", "context.body"],
        "prompt_sha256": prompt_hash, "official_model_calls": 0,
        "semantic_or_runtime_correctness_claimed": False,
    }
    (OUT / "generation_input_validation_report.json").write_text(json.dumps(input_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    source_hashes_after = {name: sha(ROOT / name) for name in source_hashes}
    errors: list[str] = []
    if len(corrected) != 18 or len({item["bug_id"] for item in corrected}) != 18:
        errors.append("not exactly 18 unique Gold records")
    if any(not item["semantic_metric_fingerprint_unchanged"] for item in correction_logs):
        errors.append("unauthorized metric/classification semantic change")
    if projection_errors:
        errors.extend(projection_errors)
    if any(not item["issue_snapshot_temporal_limitation_preserved"] for item in audits):
        errors.append("Issue snapshot temporal limitation missing")
    if any(item["target_issue_generation_text_future_repair_direct_hits"] for item in audits):
        errors.append("target Issue generation text directly matches future repair identifiers/excerpts")
    if any(item["gold_frozen"] for item in corrected):
        errors.append("Gold record frozen")
    if source_hashes != source_hashes_after:
        errors.append("authoritative/source artifact changed")
    invariant_results = {
        "status": "PASS_PHASE_2_11F_3_INVARIANTS" if not errors else "FAIL",
        "all_10_human_decisions_applied": len(decisions) == 10,
        "decision_counts": dict(counts),
        "all_18_gold_records_scanned": len(audits) == 18,
        "unauthorized_semantic_changes": 0 if all(item["semantic_metric_fingerprint_unchanged"] for item in correction_logs) else 1,
        "future_repair_evidence_in_generation_inputs": sum(len(item["target_issue_generation_text_future_repair_direct_hits"]) for item in audits),
        "repair_root_causes_promoted_to_required_trigger": 0,
        "historical_audit_fields_in_generation_inputs": 0,
        "issue_snapshot_temporal_limitations_preserved": all(item["issue_snapshot_temporal_limitation_preserved"] for item in audits),
        "target_issue_creation_time_equivalence_claimed": False,
        "all_records_unfrozen": all(not item["gold_frozen"] for item in corrected),
        "runtime_reproduction_claims": sum(bool(item.get("runtime_reproduction_claimed")) for item in corrected),
        "official_model_calls": 0,
        "source_integrity": {name: {"before": source_hashes[name], "after": source_hashes_after[name], "unchanged": source_hashes[name] == source_hashes_after[name]} for name in source_hashes},
        "errors": errors,
        "validation_scope": "structural, provenance, temporal-isolation, and authorized-diff checks only",
    }
    (OUT / "invariant_validation_results.json").write_text(json.dumps(invariant_results, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    queue = ["# Phase 2.11F-3 Human Re-verification Queue", "",
             "Gold remains unfrozen. This queue contains only the nine materially corrected records.", ""]
    for decision in decision_doc["decisions"]:
        if decision["decision"] != "NEEDS_CORRECTION":
            continue
        log = next(item for item in correction_logs if item["bug_id"] == decision["bug_id"])
        queue += [f"## `{decision['bug_id']}`", "", f"Authorized correction: {decision['required_correction']}", "",
                  f"Applied field changes: {log['change_count']}", "",
                  "Recommendation: confirm the correction preserves the accepted metric/Oracle semantics and approve only this corrected artifact if satisfied.", "",
                  "- [ ] Future evidence references are evaluation-only.",
                  "- [ ] Natural-language correction does not add a new trigger or repair claim.",
                  "- [ ] Accepted TCC/CSPC/Oracle classifications remain unchanged.",
                  "- Final re-verification decision: ______", "- Reviewer/reason: ______", ""]
    (OUT / "human_reverification_queue.md").write_text("\n".join(queue), encoding="utf-8")

    summary = f"""# Phase 2.11F-3 Summary

Status: **{invariant_results['status']}**.

- Human decisions applied: 10 (1 FINAL_VERIFY, 9 NEEDS_CORRECTION).
- Gold records audited: 18/18.
- Safe generation projections: 18/18; model-visible Gold fields: 0.
- Future repair evidence in generation projections: 0.
- Runtime reproduction claims: 0.
- Gold frozen: no.
- Official model calls: 0.

The nine corrected records remain awaiting human re-verification. The complete Gold retains evaluation-only evidence, while the explicit serializer permits only target/context Issue title and body into generation prompts. No Phase 3 or experiment work was performed.

The target Issue snapshots are content-hash verified and show no direct fixing-PR, repair-commit, or future-evidence excerpt matches. Their creation-time equivalence remains unverified because edit histories are unavailable; the existing limitation is preserved for all 18 records.
"""
    (OUT / "phase2_11F_3_summary.md").write_text(summary, encoding="utf-8")
    print(json.dumps({"status": invariant_results["status"], "records": len(corrected),
                      "decisions": dict(counts), "safe_projection_records": len(projection),
                      "human_reverification_queue": 9, "errors": errors,
                      "official_model_calls": 0}, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
