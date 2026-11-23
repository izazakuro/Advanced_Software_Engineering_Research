#!/usr/bin/env python3
"""Independent validation for Phase 2.11F-3 temporal evidence isolation."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/phase2_11F_3"
F2 = ROOT / "outputs/phase2_11F_2/gold_references_human_revised.jsonl"
DECISIONS = ROOT / "gold_final_human_verification_decisions.json"
TARGETS = ROOT / "data/phase2_11E_evidence/phase2_11E_target_issues.jsonl"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def differences(old: object, new: object, path: str = "") -> list[str]:
    if type(old) is not type(new):
        return [path]
    if isinstance(old, dict):
        result: list[str] = []
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key
            result.extend([child] if key not in old or key not in new else differences(old[key], new[key], child))
        return result
    if isinstance(old, list):
        if len(old) != len(new):
            return [path]
        result: list[str] = []
        for index, (left, right) in enumerate(zip(old, new)):
            result.extend(differences(left, right, f"{path}[{index}]"))
        return result
    return [] if old == new else [path]


def content_sha(title: str, body: str) -> str:
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


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
        if evidence.get("temporal_role") == "evaluation_only_future":
            excerpt = " ".join((evidence.get("excerpt") or "").split()).casefold()
            if len(excerpt) >= 30 and excerpt in text:
                hits.append(f"future_excerpt:{evidence['evidence_id']}")
    return sorted(set(hits))


def approved_post_f3_runner_enforcement(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                and node.module == "phase2_11F_3_generation_input" for alias in node.names}
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    legacy_render = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "render"
                        for node in ast.walk(tree))
    return ({"load_safe_projection", "serialize_context_issue", "render_abc_prompt"} <= imported
            and {"load_safe_projection", "serialize_context_issue", "render_abc_prompt"} <= calls
            and "phase2_9_abc_target_issue_inputs" not in source and not legacy_render)


def main() -> int:
    errors: list[str] = []
    expected_files = {
        "gold_references_temporal_corrected.jsonl", "per_record_correction_log.jsonl",
        "per_record_correction_diff.md", "temporal_leakage_audit_all18.jsonl",
        "generation_input_allowlist.json", "generation_input_safe_projection.jsonl",
        "generation_input_validation_report.json", "invariant_validation_results.json",
        "human_reverification_queue.md", "phase2_11F_3_summary.md",
    }
    actual_files = {path.name for path in OUT.iterdir() if path.is_file()} if OUT.is_dir() else set()
    if actual_files != expected_files:
        errors.append(f"deliverable set mismatch: {sorted(actual_files ^ expected_files)}")

    records = jsonl(OUT / "gold_references_temporal_corrected.jsonl")
    logs = jsonl(OUT / "per_record_correction_log.jsonl")
    audits = jsonl(OUT / "temporal_leakage_audit_all18.jsonl")
    projections = jsonl(OUT / "generation_input_safe_projection.jsonl")
    f2 = {item["bug_id"]: item for item in jsonl(F2)}
    by_id = {item["bug_id"]: item for item in records}
    log_by_id = {item["bug_id"]: item for item in logs}
    audit_by_id = {item["bug_id"]: item for item in audits}
    projection_by_id = {item["bug_id"]: item for item in projections}
    decision_doc = json.loads(DECISIONS.read_text(encoding="utf-8"))
    decisions = {item["bug_id"]: item for item in decision_doc["decisions"]}
    targets = {item["doc_id"]: item for item in jsonl(TARGETS)}
    counts = Counter(item["decision"] for item in decisions.values())
    if len(decisions) != 10 or counts != Counter({"FINAL_VERIFY": 1, "NEEDS_CORRECTION": 9}):
        errors.append("authoritative decision structure/count mismatch")
    expected_ids = set(f2)
    for name, mapping in (("Gold", by_id), ("log", log_by_id), ("audit", audit_by_id), ("projection", projection_by_id)):
        if len(mapping) != 18 or set(mapping) != expected_ids:
            errors.append(f"{name} does not contain the same 18 unique IDs")

    generic_allowed = {"schema_version", "source_schema_version", "phase2_11F_3_human_verification",
                       "generation_input_policy", "runtime_interpretation_policy"}
    correction_ids = {bug_id for bug_id, item in decisions.items() if item["decision"] == "NEEDS_CORRECTION"}
    partial_ids = {"CorsixTH/CorsixTH#2881", "colobot/colobot#1787", "freeorion/freeorion#1897",
                   "freeorion/freeorion#2365", "gemrb/gemrb#1878", "longturn/freeciv21#1189",
                   "warzone2100/warzone2100#4125"}

    for bug_id in sorted(expected_ids):
        record, original = by_id[bug_id], f2[bug_id]
        decision = decisions.get(bug_id)
        if record.get("gold_frozen") is not False or record.get("runtime_reproduction_claimed") is not False:
            errors.append(f"{bug_id}: freeze/runtime invariant failed")
        policy = record.get("runtime_interpretation_policy", {})
        if any(policy.get(key) is not False for key in ("independent_runtime_reproduction_claimed",
                "single_non_failure_run_proves_repair", "structural_validation_proves_semantic_correctness")):
            errors.append(f"{bug_id}: runtime interpretation policy violation")
        if record["evidence"] != original["evidence"]:
            errors.append(f"{bug_id}: complete evidence/excerpts changed")
        if not any("Issue text has no edit history" in item for item in record["known_limitations"]):
            errors.append(f"{bug_id}: Issue snapshot limitation lost")
        if record["final_human_decision"] != original["final_human_decision"]:
            errors.append(f"{bug_id}: F-2 decision changed")

        for path in differences(original, record):
            allowed = path in generic_allowed or bool(re.fullmatch(r"trigger_conditions\[\d+\]\.generation_visibility", path))
            if bug_id in correction_ids:
                allowed = allowed or path in {"affected_component.value", "affected_component.evidence_ids",
                                             "affected_component.evaluation_only_supporting_evidence_ids"}
                allowed = allowed or bool(re.fullmatch(r"test_oracles\[\d+\]\.(evidence_ids|evaluation_only_supporting_evidence_ids)", path))
                allowed = allowed or bool(re.fullmatch(r"test_oracles\[\d+\]\.acceptable_expected_outcomes\[\d+\]\.(evidence_ids|evaluation_only_supporting_evidence_ids)", path))
            if bug_id == "freeorion/freeorion#2365":
                allowed = allowed or path in {"expected_behavior.evidence_ids", "expected_behavior.evaluation_only_supporting_evidence_ids"}
            if bug_id == "wolvenkit/wolvenkit#2279":
                allowed = allowed or path == "expected_behavior.value"
            if bug_id == "CorsixTH/CorsixTH#1799":
                allowed = allowed or path in {"human_review.final_verification_required",
                                              "human_review.final_verification_status", "annotation_status"}
            if not allowed:
                errors.append(f"{bug_id}: unauthorized changed field {path}")

        for key in ("trigger_paths", "evaluation_targets", "evaluation_targets_v2", "outcome_branches", "path_groups"):
            if record[key] != original[key]:
                errors.append(f"{bug_id}: unauthorized metric/path change in {key}")
        for old, new in zip(original["trigger_conditions"], record["trigger_conditions"]):
            for key in ("condition_id", "role", "necessity", "future_repair_only", "status"):
                if old.get(key) != new.get(key):
                    errors.append(f"{bug_id}/{new['condition_id']}: trigger classification changed")
        for old, new in zip(original["test_oracles"], record["test_oracles"]):
            for key in ("oracle_id", "status", "completeness", "strict_oracle_scoring_eligibility"):
                if old.get(key) != new.get(key):
                    errors.append(f"{bug_id}/{new['oracle_id']}: Oracle classification changed")
        conditions = {item["condition_id"]: item for item in record["trigger_conditions"]}
        for path in record["trigger_paths"]:
            if path["tcc_denominator"] != len(path["required_condition_ids"]):
                errors.append(f"{bug_id}/{path['path_id']}: TCC denominator mismatch")
            for cid in path["required_condition_ids"]:
                if conditions[cid].get("role") != "required_trigger" or conditions[cid].get("future_repair_only"):
                    errors.append(f"{bug_id}/{path['path_id']}: ineligible trigger counted in TCC")
            if bug_id in partial_ids and path["complete_path_metric_eligible"]:
                errors.append(f"{bug_id}/{path['path_id']}: partial path CSPC eligible")
            if any(conditions[cid].get("role") == "root_cause_only" for cid in path["required_condition_ids"]):
                errors.append(f"{bug_id}/{path['path_id']}: root cause counted in TCC")

        if decision:
            embedded = record.get("phase2_11F_3_human_verification") or {}
            if embedded.get("authoritative_decision") != decision["decision"] or embedded.get("accepted_invariants") != decision["accepted"]:
                errors.append(f"{bug_id}: decision not faithfully embedded")
            if decision["decision"] == "FINAL_VERIFY":
                if embedded.get("application_status") != "FINAL_VERIFIED_UNFROZEN" or record["human_review"].get("final_verification_required"):
                    errors.append(f"{bug_id}: FINAL_VERIFY not applied")
            else:
                if embedded.get("application_status") != "CORRECTED_AWAITING_HUMAN_REVERIFICATION":
                    errors.append(f"{bug_id}: correction not gated for re-verification")
                future_ids = {item["evidence_id"] for item in record["evidence"] if item.get("temporal_role") == "evaluation_only_future"}
                nodes = [("affected_component", record["affected_component"])]
                for oracle in record["test_oracles"]:
                    nodes.append((oracle["oracle_id"], oracle))
                    nodes.extend((outcome["outcome_id"], outcome) for outcome in oracle.get("acceptable_expected_outcomes", []))
                if bug_id == "freeorion/freeorion#2365":
                    nodes.append(("expected_behavior", record["expected_behavior"]))
                for name, node in nodes:
                    if set(node.get("evidence_ids", [])) & future_ids:
                        errors.append(f"{bug_id}/{name}: future evidence remains primary")
        if bug_id == "wolvenkit/wolvenkit#2279":
            value = record["expected_behavior"]["value"]
            if "successful export is not required" not in value or "archives are correctly loaded" in value:
                errors.append("WolvenKit#2279 expected_behavior correction missing")

        audit = audit_by_id[bug_id]
        if audit.get("generation_side_future_evidence_count") != 0 or not audit.get("issue_snapshot_temporal_limitation_preserved"):
            errors.append(f"{bug_id}: temporal audit failed")
        projection = projection_by_id[bug_id]
        allowed_projection = {"bug_id", "target_issue", "gold_fields_in_model_visible_content", "model_visible_sources", "generation_input_policy_version"}
        allowed_target = {"doc_id", "title", "body", "original_content_sha256", "created_at"}
        if set(projection) != allowed_projection or set(projection["target_issue"]) != allowed_target:
            errors.append(f"{bug_id}: projection allowlist violation")
        if projection["gold_fields_in_model_visible_content"] != []:
            errors.append(f"{bug_id}: Gold exposed to generation")
        target = targets[bug_id]
        direct_hits = target_future_text_hits(record, target)
        if direct_hits or audit.get("target_issue_generation_text_future_repair_direct_hits") != direct_hits:
            errors.append(f"{bug_id}: target Issue generation text future-repair scan failed: {direct_hits}")
        if projection["target_issue"]["title"] != target["title"] or projection["target_issue"]["body"] != target["body"]:
            errors.append(f"{bug_id}: projection not exact target Issue text")
        if content_sha(target["title"] or "", target["body"] or "") != projection["target_issue"]["original_content_sha256"]:
            errors.append(f"{bug_id}: target content hash mismatch")

    allowlist = json.loads((OUT / "generation_input_allowlist.json").read_text(encoding="utf-8"))
    if allowlist.get("gold_record_prompt_fields") != [] or allowlist.get("official_generation_authorized") is not False:
        errors.append("generation allowlist governance violation")
    input_report = json.loads((OUT / "generation_input_validation_report.json").read_text(encoding="utf-8"))
    if (input_report.get("status") != "PASS_EXPLICIT_ALLOWLIST_PROJECTION"
            or input_report.get("future_evidence_in_safe_projection") != 0
            or input_report.get("target_issue_future_repair_direct_text_hits") != 0
            or input_report.get("target_issue_creation_time_equivalence_verified") != 0):
        errors.append("generation input validation failed")
    invariant = json.loads((OUT / "invariant_validation_results.json").read_text(encoding="utf-8"))
    if (invariant.get("status") != "PASS_PHASE_2_11F_3_INVARIANTS"
            or invariant.get("official_model_calls") != 0
            or invariant.get("future_repair_evidence_in_generation_inputs") != 0
            or invariant.get("target_issue_creation_time_equivalence_claimed") is not False):
        errors.append("embedded invariant validation failed")
    authorized_post_f3_changes: list[str] = []
    for name, item in invariant.get("source_integrity", {}).items():
        current = sha(ROOT / name)
        if current != item.get("before") or item.get("before") != item.get("after"):
            if name == "scripts/phase2_10B_experiment_runner.py" and approved_post_f3_runner_enforcement(ROOT / name):
                authorized_post_f3_changes.append(name)
            else:
                errors.append(f"source integrity mismatch: {name}")
    queue = (OUT / "human_reverification_queue.md").read_text(encoding="utf-8")
    if queue.count("## ") != 9 or "CorsixTH/CorsixTH#1799" in queue:
        errors.append("human queue is not limited to nine corrected records")

    result = {
        "status": "PASS_PHASE_2_11F_3_INDEPENDENT_VALIDATION" if not errors else "FAIL_PHASE_2_11F_3_INDEPENDENT_VALIDATION",
        "gold_records": len(records), "records_audited": len(audits), "decisions": dict(counts),
        "safe_projections": len(projections), "future_repair_evidence_in_generation_inputs": 0 if not errors else None,
        "human_reverification_queue_records": 9, "gold_frozen": False, "official_model_calls": 0,
        "errors": errors,
        "authorized_post_f3_changes": authorized_post_f3_changes,
        "warnings": ["Nine corrected records require explicit human re-verification.",
                     "Full Gold retains evaluation-only evidence and must not be used as model input.",
                     "The Phase 2.10B runner changed post-F-3 only to enforce the approved serializer boundary.",
                     "Validation does not establish runtime reproduction or semantic correctness."],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
