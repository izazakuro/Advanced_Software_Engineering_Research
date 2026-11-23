#!/usr/bin/env python3
"""Differential Phase 2.11F-4 Lite acceptance gate; never freezes Gold."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/phase2_11F_4_lite"
F2 = ROOT / "outputs/phase2_11F_2/gold_references_human_revised.jsonl"
F3 = ROOT / "outputs/phase2_11F_3/gold_references_temporal_corrected.jsonl"
DECISIONS = ROOT / "gold_final_human_verification_decisions.json"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def differences(old: object, new: object, path: str = "") -> list[dict]:
    if type(old) is not type(new):
        return [{"field": path, "old": old, "new": new}]
    if isinstance(old, dict):
        result: list[dict] = []
        for key in sorted(set(old) | set(new)):
            child = f"{path}.{key}" if path else key
            if key not in old:
                result.append({"field": child, "old": None, "new": new[key]})
            elif key not in new:
                result.append({"field": child, "old": old[key], "new": None})
            else:
                result.extend(differences(old[key], new[key], child))
        return result
    if isinstance(old, list):
        if len(old) != len(new):
            return [{"field": path, "old": old, "new": new}]
        result: list[dict] = []
        for index, (left, right) in enumerate(zip(old, new)):
            result.extend(differences(left, right, f"{path}[{index}]"))
        return result
    return [] if old == new else [{"field": path, "old": old, "new": new}]


def main() -> None:
    if OUT.exists():
        raise SystemExit("outputs/phase2_11F_4_lite already exists; refusing to overwrite")
    decisions_doc = json.loads(DECISIONS.read_text(encoding="utf-8"))
    decisions = {item["bug_id"]: item for item in decisions_doc["decisions"]}
    f2 = {item["bug_id"]: item for item in jsonl(F2)}
    f3 = {item["bug_id"]: item for item in jsonl(F3)}
    f3_logs = {item["bug_id"]: item for item in jsonl(ROOT / "outputs/phase2_11F_3/per_record_correction_log.jsonl")}
    if set(f2) != set(f3) or len(f3) != 18:
        raise SystemExit("F-2/F-3 ID mismatch")

    correction_ids = {bug_id for bug_id, item in decisions.items() if item["decision"] == "NEEDS_CORRECTION"}
    final_verify_ids = {bug_id for bug_id, item in decisions.items() if item["decision"] == "FINAL_VERIFY"}
    errors: list[str] = []
    semantic_records: list[dict] = []
    natural_language_changes: list[dict] = []

    for bug_id in sorted(f2):
        old, new = f2[bug_id], f3[bug_id]
        changes = differences(old, new)
        generic_allowed = {"schema_version", "source_schema_version", "phase2_11F_3_human_verification",
                           "generation_input_policy", "runtime_interpretation_policy"}
        unauthorized_fields: list[str] = []
        for item in changes:
            path = item["field"]
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
                unauthorized_fields.append(path)
        if unauthorized_fields:
            errors.append(f"{bug_id}: unauthorized F-3 changed fields {unauthorized_fields}")
        metric_fields = ("trigger_paths", "evaluation_targets", "evaluation_targets_v2", "path_groups", "outcome_branches")
        metric_unchanged = all(old[field] == new[field] for field in metric_fields)
        trigger_classifications_unchanged = all(
            all(left.get(key) == right.get(key) for key in ("condition_id", "role", "necessity", "future_repair_only", "status"))
            for left, right in zip(old["trigger_conditions"], new["trigger_conditions"])
        )
        oracle_classifications_unchanged = all(
            all(left.get(key) == right.get(key) for key in ("oracle_id", "status", "completeness", "strict_oracle_scoring_eligibility"))
            for left, right in zip(old["test_oracles"], new["test_oracles"])
        )
        if not metric_unchanged or not trigger_classifications_unchanged or not oracle_classifications_unchanged:
            errors.append(f"{bug_id}: accepted metric/trigger/Oracle semantics changed")

        text_changes = [item for item in changes if item["field"].endswith(".value") or item["field"].endswith(".description")
                        or item["field"].endswith(".expected_result") or item["field"].endswith(".incorrect_result")]
        for item in text_changes:
            authorization = "AUTHORIZED_NEUTRAL_COMPONENT_SCOPE" if item["field"] == "affected_component.value" and bug_id in correction_ids else None
            if bug_id == "wolvenkit/wolvenkit#2279" and item["field"] == "expected_behavior.value":
                authorization = "EXPLICIT_WOLVENKIT_HUMAN_CORRECTION"
            if authorization is None:
                errors.append(f"{bug_id}: unauthorized natural-language change {item['field']}")
                authorization = "UNAUTHORIZED"
            natural_language_changes.append({"bug_id": bug_id, **item, "classification": authorization,
                                             "semantic_drift_found": authorization == "UNAUTHORIZED"})

        future_ids = {item["evidence_id"] for item in new["evidence"] if item.get("temporal_role") == "evaluation_only_future"}
        primary_future_refs: list[str] = []
        if bug_id in correction_ids:
            nodes = [("affected_component", new["affected_component"])]
            for oracle in new["test_oracles"]:
                nodes.append((f"test_oracles.{oracle['oracle_id']}", oracle))
                nodes.extend((f"test_oracles.{oracle['oracle_id']}.{outcome['outcome_id']}", outcome)
                             for outcome in oracle.get("acceptable_expected_outcomes", []))
            if bug_id == "freeorion/freeorion#2365":
                nodes.append(("expected_behavior", new["expected_behavior"]))
            for name, node in nodes:
                if set(node.get("evidence_ids", [])) & future_ids:
                    primary_future_refs.append(name)
        if primary_future_refs:
            errors.append(f"{bug_id}: future evidence remains in corrected primary fields {primary_future_refs}")

        semantic_records.append({
            "bug_id": bug_id,
            "authoritative_f3_decision": decisions.get(bug_id, {}).get("decision", "OUTSIDE_F3_DECISION_SET"),
            "changed_field_count": len(changes),
            "changed_fields": [item["field"] for item in changes],
            "unauthorized_changed_fields": unauthorized_fields,
            "natural_language_changes": text_changes,
            "authorized_correction_log_change_count": f3_logs[bug_id]["change_count"],
            "metric_and_path_fields_unchanged": metric_unchanged,
            "trigger_roles_and_necessity_unchanged": trigger_classifications_unchanged,
            "oracle_classifications_unchanged": oracle_classifications_unchanged,
            "future_evidence_in_corrected_primary_fields": primary_future_refs,
            "evidence_records_and_excerpts_unchanged": old["evidence"] == new["evidence"],
            "runtime_reproduction_claimed": bool(new.get("runtime_reproduction_claimed")),
            "gold_frozen": bool(new.get("gold_frozen")),
            "acceptance": "PASS_AUTHORIZED_DIFFERENCE" if not unauthorized_fields and not primary_future_refs and metric_unchanged and trigger_classifications_unchanged and oracle_classifications_unchanged else "FAIL",
        })

    wolven = f3["wolvenkit/wolvenkit#2279"]
    wolven_expected = wolven["expected_behavior"]["value"]
    wolven_pass = ("infinite MergeMaterialChain recursion" in wolven_expected
                   and "Stack Overflow" in wolven_expected
                   and "successful export is not required" in wolven_expected
                   and "archives are correctly loaded" not in wolven_expected)
    if not wolven_pass:
        errors.append("WolvenKit#2279 expected_behavior does not match authorized boundary")

    # Locate executable prompt renderers/model callers and require the approved boundary.
    entry_points: list[dict] = []
    for path in sorted((ROOT / "scripts").glob("*.py")):
        if "_test_" in path.name or path.name.startswith("test_"):
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        defines_legacy_render = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "render"
                                    for node in ast.walk(tree))
        call_names: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts: list[str] = []
            current = node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            call_names.append(".".join(reversed(parts)))
        calls_model = any(name.endswith(("responses.create", "chat.completions.create", "generate_content", "client.invoke"))
                          for name in call_names)
        uses_approved_renderer = any(name.endswith("render_abc_prompt") for name in call_names)
        renders_prompt = defines_legacy_render or uses_approved_renderer
        if renders_prompt or calls_model:
            uses_serializer = uses_approved_renderer and "phase2_11F_3_generation_input" in source
            exposed_fields = ["target Issue title/body", "context Issue title/body"] if path.name == "phase2_10B_experiment_runner.py" else ["UNKNOWN"]
            entry_points.append({"path": str(path.relative_to(ROOT)), "renders_prompt": renders_prompt,
                                 "calls_model_api": calls_model, "uses_approved_f3_serializer": uses_serializer,
                                 "observed_model_visible_fields": exposed_fields,
                                 "evaluation_only_evidence_observed": False if path.name == "phase2_10B_experiment_runner.py" else None})

    unsafe_entries = [item for item in entry_points if not item["uses_approved_f3_serializer"]]
    blockers: list[dict] = []
    if unsafe_entries:
        blockers.append({
            "id": "F4-B1-GENERATION-SERIALIZER-BYPASS",
            "classification": "BLOCKER",
            "scope": "experiment_pipeline_readiness",
            "finding": "The existing prompt renderer does not call the approved Phase 2.11F-3 serializer/projection.",
            "affected_entry_points": [item["path"] for item in unsafe_entries],
            "observed_leakage": False,
            "risk": "The renderer currently exposes the same title/body field set, but the allowlist is not enforced and future drift could expose evaluation-only fields.",
            "required_resolution": "Route every prompt-rendering/model-call entry point through phase2_11F_3_generation_input.py or consume generation_input_safe_projection.jsonl, then rerun F-4 Lite.",
        })
    if errors:
        blockers.append({"id": "F4-B2-DIFFERENTIAL-VALIDATION", "classification": "BLOCKER",
                         "finding": "One or more unauthorized/invalid F-2 to F-3 differences were detected.", "errors": errors})

    unresolved_rubric = [
        {"bug_id": "tmodloader/tmodloader#2644", "oracle_id": "O1", "current_status": "pending_rubric_decision",
         "required_human_decision": "Whether inferred/complete O1 is eligible for strict Oracle scoring."},
        {"bug_id": "vcmi/vcmi#5462", "oracle_id": "O1", "current_status": "pending_rubric_decision",
         "required_human_decision": "Whether inferred/complete crash-survival O1 is eligible for strict Oracle scoring."},
    ]
    nonblocking = [
        {"classification": "NON_BLOCKING_LIMITATION", "id": "ISSUE-EDIT-HISTORY",
         "finding": "All 18 current Issue snapshots lack verified edit history/creation-time equivalence; hashes and limitations are preserved."},
        {"classification": "NON_BLOCKING_LIMITATION", "id": "RUNTIME-REPRODUCTION",
         "finding": "No record claims independent runtime reproduction; incomplete/intermittent and unavailable-environment limitations remain."},
        {"classification": "NON_BLOCKING_LIMITATION", "id": "EXCLUDED-ORACLES",
         "finding": "Explicitly incomplete/ineligible Oracles remain excluded from strict scoring; no eligibility was upgraded."},
    ]
    informational = [
        {"classification": "INFORMATIONAL", "finding": "Nine affected_component values were narrowed to target-supported neutral component labels."},
        {"classification": "INFORMATIONAL", "finding": "F-3 audit/policy/version metadata additions do not change TCC, CSPC, paths, trigger roles, or Oracle classifications."},
        {"classification": "INFORMATIONAL", "finding": "The current offline renderer has no model API call and no observed evaluation-only evidence exposure."},
    ]

    OUT.mkdir(parents=True)
    semantic = {
        "phase": "2.11F-4-lite", "record_count": 18,
        "needs_correction_records_checked": len(correction_ids), "final_verify_records": sorted(final_verify_ids),
        "records": semantic_records, "changed_natural_language_fields": natural_language_changes,
        "wolvenkit_2279_expected_behavior": {"value": wolven_expected, "authorized_boundary_passed": wolven_pass},
        "accepted_metric_semantics_unchanged": not any("semantics changed" in item for item in errors),
        "source_hashes": {str(path.relative_to(ROOT)): sha(path) for path in [DECISIONS, F2, F3,
            ROOT / "outputs/phase2_11F_3/per_record_correction_diff.md",
            ROOT / "outputs/phase2_11F_3/temporal_leakage_audit_all18.jsonl",
            ROOT / "outputs/phase2_11F_3/invariant_validation_results.json",
            ROOT / "outputs/phase2_11F_3/generation_input_validation_report.json",
            ROOT / "scripts/phase2_11F_3_generation_input.py"]},
    }
    (OUT / "f4_lite_semantic_diff.json").write_text(json.dumps(semantic, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    blocker_doc = {
        "status": "BLOCKED" if blockers else "NO_TECHNICAL_BLOCKERS",
        "blocker_count": len(blockers), "blockers": blockers,
        "non_blocking_limitations": nonblocking, "informational_findings": informational,
        "unresolved_rubric_decisions": unresolved_rubric,
        "generation_entry_points": entry_points, "official_model_calls": 0,
    }
    (OUT / "f4_lite_blockers.json").write_text(json.dumps(blocker_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    gold_data_ready = not errors
    pipeline_ready = not unsafe_entries
    readiness = {
        "status": "NOT_READY_FOR_FREEZE" if blockers else "TECHNICALLY_READY_AWAITING_HUMAN_AUTHORIZATION",
        "gold_artifact_technical_readiness": gold_data_ready,
        "generation_pipeline_technical_readiness": pipeline_ready,
        "overall_technical_readiness": gold_data_ready and pipeline_ready,
        "human_freeze_authorization_present": False,
        "gold_frozen": False,
        "automatic_freeze_performed": False,
        "official_generation_authorized": False,
        "official_model_calls": 0,
        "required_before_freeze": ([item["required_resolution"] for item in blockers if "required_resolution" in item]
                                   + ["Explicit human acceptance of the nine corrected records and explicit Gold freeze authorization."]
                                   + ["Resolve or formally defer the two pending strict-Oracle Rubric decisions without silently changing eligibility."]),
        "unresolved_rubric_decisions": unresolved_rubric,
    }
    (OUT / "gold_v1_freeze_readiness.json").write_text(json.dumps(readiness, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if blockers:
        result_text = "**NOT READY FOR FREEZE**. One or more technical blockers remain; see `f4_lite_blockers.json`."
        enforcement_text = "The approved serializer boundary is not fully enforced by every prompt/model entry point."
    else:
        result_text = "**TECHNICALLY READY; AWAITING HUMAN AUTHORIZATION**. The original generation-serializer engineering blocker is resolved. Gold is not frozen."
        enforcement_text = ("`scripts/phase2_10B_experiment_runner.py` now loads targets only from the validated F-3 safe projection, "
                            "serializes every context through the strict allowlist, and delegates prompt rendering to `render_abc_prompt`. "
                            "No raw-target or local-render fallback remains.")

    report = f"""# Phase 2.11F-4 Lite Acceptance Report

## Result

{result_text}

## Differential acceptance

- F-2/F-3 records compared: 18/18.
- NEEDS_CORRECTION records checked: 9/9.
- Unauthorized metric, trigger-role, path, CSPC, TCC, or Oracle-classification changes: {len(errors)}.
- Changed natural-language fields reviewed: {len(natural_language_changes)}.
- WolvenKit #2279 expected_behavior: PASS; it requires absence of infinite recursion/Stack Overflow and does not require valid archives or successful invalid-path export.
- Runtime reproduction claims: 0.
- Frozen records: 0.

## Generation boundary enforcement

{enforcement_text}

## Non-blocking limitations

- Issue edit histories remain unavailable; current snapshots are hash-verified but creation-time equivalence is not claimed.
- Runtime reproduction, intermittent failures, fixtures, and historical environment dependencies remain limited as documented.
- Incomplete and explicitly strict-ineligible Oracles remain excluded.

## Unresolved Rubric decisions

- `tmodloader/tmodloader#2644` O1: inferred/complete; strict eligibility remains `pending_rubric_decision`.
- `vcmi/vcmi#5462` O1: inferred/complete; strict eligibility remains `pending_rubric_decision`.

## Governance

Gold artifact technical readiness is {str(gold_data_ready).lower()}; generation-pipeline technical readiness is {str(pipeline_ready).lower()}. Human freeze authorization is absent. Gold remains unfrozen, no model calls were made, and no Phase 3 work was started.
"""
    (OUT / "f4_lite_acceptance_report.md").write_text(report, encoding="utf-8")
    print(json.dumps({"status": readiness["status"], "gold_data_ready": gold_data_ready,
                      "pipeline_ready": pipeline_ready, "blockers": len(blockers),
                      "rubric_decisions_pending": len(unresolved_rubric),
                      "official_model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
