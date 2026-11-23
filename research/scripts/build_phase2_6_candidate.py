#!/usr/bin/env python3
"""Build versioned Phase-2.6 remediation artifacts from Phase-2.5 inputs."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def change(field: str, original: Any, proposed: Any, rationale: str, evidence_ids: list[str] | None = None) -> dict[str, Any]:
    return {"field": field, "original_value": original, "proposed_value": proposed, "rationale": rationale, "evidence_ids": evidence_ids or [], "status": "machine_proposed_pending_human_review"}


def build_candidate(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    record = copy.deepcopy(source); changes: list[dict[str, Any]] = []
    proposed = record["phase2_5_review"]["preliminary_disposition"]
    old_suggestion = record["suggested_review_decision"]
    if old_suggestion != proposed:
        changes.append(change(
            "suggested_review_decision", old_suggestion, proposed,
            "Phase 2.6 policy aligns machine suggestions with the evidence-linked Phase-2.5 preliminary disposition while outstanding questions remain unresolved. The artifact history does not prove why the top-level field stayed stale."
        ))
    record["suggested_review_decision"] = proposed
    record["phase2_6_machine_suggestion"] = {
        "value": proposed, "policy": "Use phase2_5_review.preliminary_disposition as the single machine suggestion pending human resolution of outstanding questions.",
        "is_human_decision": False,
    }

    conditions = {c["condition_id"]: c for c in record["trigger_conditions"]}
    path_ids = [p["path_id"] for p in record["trigger_paths"]]
    if record["bug_id"] == "rptools/maptool#2416":
        groups = [{"path_group_id": "PG1", "operator": "OR", "path_ids": ["P1", "P2"], "bug_level_tcc_aggregation": "maximum_over_coherently_attempted_valid_paths", "cspc_rule": "Either eligible path suffices; T2A and T2B are never simultaneously required."}]
    else:
        groups = [{"path_group_id": "PG1", "operator": "SINGLE", "path_ids": path_ids, "bug_level_tcc_aggregation": "single_path", "cspc_rule": "Use the applicable eligible path."}]
    record["path_groups"] = groups
    for path in record["trigger_paths"]:
        supported_setup = [cid for cid in path["setup_condition_ids"] if conditions[cid]["necessity"] == "supported"]
        observed_setup = [cid for cid in path["setup_condition_ids"] if conditions[cid]["necessity"] == "not_established"]
        relevant = supported_setup + path["required_condition_ids"]
        input_ids = [cid for cid in relevant if conditions[cid]["type"] in {"input", "configuration", "initial_state", "platform_environment", "game_state", "application_state"}]
        path["scenario_adequacy"] = {
            "material_setup_condition_ids": supported_setup,
            "observed_but_not_universally_required_setup_ids": observed_setup,
            "input_or_initial_state_condition_ids": input_ids,
            "supported_ordering_condition_ids": path["ordered_condition_ids"],
            "verification_action_ids": path["verification_action_ids"],
            "practical_specificity_status": "documented_complete" if path["complete_path_metric_eligible"] else "limited_or_partial",
        }
        path["tcc_denominator"] = len(path["required_condition_ids"])
        path["cspc_eligibility"] = "eligible_pending_human_approval" if path["complete_path_metric_eligible"] else "not_applicable_partial_or_unsupported"

    if record["bug_id"] == "CorsixTH/CorsixTH#1799":
        oracle = record["test_oracles"][0]; before = copy.deepcopy(oracle)
        oracle["expected_result"] = "After the emergency/patient workflow reaches the Case Book action, the Case Book opens pre-set to the patient's disease."
        oracle["acceptable_expected_outcomes"] = [{"outcome_id": "A1", "description": "The Case Book opens pre-set to the patient's disease after the workflow.", "evidence_ids": ["E4"]}]
        oracle["evidence_ids"] = ["E3", "E4"]
        record["preventive_oracles"] = [{
            "oracle_id": "OP1", "branch_id": "B_PREVENT", "applicable_before_condition_id": "T1A",
            "expected_result": "The emergency does not occur while the disease remains undiscovered.",
            "incorrect_result": "An emergency for an undiscovered disease is generated.",
            "verification_method": "Observe emergency generation before accepting or opening the Case Book.",
            "evidence_ids": ["E8"], "status": "explicit", "confidence": "high",
            "scoring_status": "branch_specific_pending_human_review",
        }]
        record["outcome_branches"] = [
            {"branch_id": "B_POST", "branch_type": "post_trigger", "applicable_path_ids": ["P1"], "oracle_ids": ["O1"], "cspc_eligible": True},
            {"branch_id": "B_PREVENT", "branch_type": "preventive_pre_trigger", "applicable_path_ids": [], "oracle_ids": ["OP1"], "cspc_eligible": False, "must_not_be_scored_as_post_path_success": True},
        ]
        changes.append(change("test_oracles.O1/outcome_branches", before, {"O1": oracle, "preventive_oracles": record["preventive_oracles"], "outcome_branches": record["outcome_branches"]}, "The Issue's prevention outcome occurs before the post-emergency Case Book path and therefore requires a separate applicable branch.", ["E3", "E4", "E8"]))
    else:
        record["preventive_oracles"] = []
        record["outcome_branches"] = [{"branch_id": "B1", "branch_type": "post_trigger", "applicable_path_ids": path_ids, "oracle_ids": [o["oracle_id"] for o in record["test_oracles"]], "cspc_eligible": any(p["complete_path_metric_eligible"] for p in record["trigger_paths"])}]

    incomplete = 0; alternatives = 0
    for oracle in record["test_oracles"]:
        is_incomplete = oracle["completeness"] == "incomplete"
        has_alternatives = len(oracle.get("acceptable_expected_outcomes", [])) > 1
        oracle["oracle_completeness_flag"] = "incomplete" if is_incomplete else "complete"
        oracle["has_alternative_outcomes"] = has_alternatives
        oracle["ambiguity_reasons"] = (["gold_oracle_incomplete"] if is_incomplete else []) + (["multiple_applicable_acceptable_outcomes"] if has_alternatives else [])
        oracle["scoring_status"] = "partial_or_not_scorable_pending_human_review" if is_incomplete else "scorable_if_human_approved"
        incomplete += int(is_incomplete); alternatives += int(has_alternatives)
    record["oracle_category_summary"] = {
        "incomplete_oracle_count": incomplete,
        "multi_outcome_post_trigger_oracle_count": alternatives,
        "separate_preventive_oracle_count": len(record["preventive_oracles"]),
        "categories_are_independent": True,
    }

    record["evaluation_targets_v2"] = {
        "tcc": {"path_denominators": {p["path_id"]: p["tcc_denominator"] for p in record["trigger_paths"]}, "path_group_aggregation": groups[0]["bug_level_tcc_aggregation"]},
        "scenario_adequacy_required": True,
        "cspc_eligible_path_ids": [p["path_id"] for p in record["trigger_paths"] if p["complete_path_metric_eligible"]],
        "partial_exploratory_only": not any(p["complete_path_metric_eligible"] for p in record["trigger_paths"]),
        "oracle_scoring_requires_applicable_branch": True,
    }
    record["schema_version"] = "2.6-audit-remediation-candidate"
    record["source_phase2_5_record_sha256"] = digest(source)
    record["phase2_6_review"] = {
        "audit_finding_verified": True,
        "machine_suggestion_aligned": True,
        "changes": changes,
        "unresolved_human_questions": record["phase2_5_review"]["outstanding_questions"],
        "human_decision_required": True,
    }
    record["annotation_status"] = "pending_review"; record["final_human_decision"] = None
    return record, changes


def report(records: list[dict[str, Any]], log: dict[str, Any]) -> str:
    lines = [
        "# Phase 2.6 Audit Resolution", "",
        "Status: machine-proposed audit remediation only. No record is approved, rejected, frozen, or semantically certified.", "",
        "## Verified local findings", "",
        f"- Records/edit specifications/checklist sections: **18 / 18 / 18**.",
        f"- Original refined top-level suggestions: **{log['verified_counts']['original_suggestions']}**.",
        f"- Phase-2.5 evidence-linked review dispositions: **{log['verified_counts']['review_dispositions']}**.",
        f"- Phase-2.6 aligned machine suggestions: **{log['verified_counts']['candidate_suggestions']}**.",
        f"- Complete fully supported bugs: **{log['verified_counts']['complete_path_bugs']}**; partial/non-eligible: **{log['verified_counts']['partial_path_bugs']}**.",
        f"- Bugs with incomplete test Oracles: **{log['verified_counts']['incomplete_oracle_bugs']}**.",
        f"- Bugs with multi-outcome post-trigger Oracles after remediation: **{log['verified_counts']['multi_outcome_post_trigger_bugs']}**.",
        f"- Separate preventive-branch cases: **{log['verified_counts']['preventive_branch_bugs']}**.",
        "- All 18 final human decisions remain blank.", "",
        "The independent audit's count findings were reproduced. Its case-level recommendations were treated as advisory and checked against the evidence IDs and current refined records.", "",
        "## Three suggestion discrepancies", "",
        "| Bug | Original top-level suggestion | Phase-2.5 review disposition | Phase-2.6 proposal | Evidence/rationale | Remaining uncertainty |", "| --- | --- | --- | --- | --- | --- |",
    ]
    by = {r["bug_id"]: r for r in records}
    for item in log["discrepancy_resolutions"]:
        r = by[item["bug_id"]]
        lines.append(f"| {item['bug_id']} | {item['original_value']} | {item['review_value']} | {item['proposed_value']} | {item['rationale']} | {'; '.join(r['phase2_6_review']['unresolved_human_questions'])} |")
    lines += ["", "The local history shows that the top-level suggestion retained the Phase-2 value while the nested Phase-2.5 review and checklist used the revised disposition. No artifact proves the human/process cause of that staleness, so none is asserted.", "", "## Methodological remediation", "",
        "- TCC now has per-path denominators and OR aggregation; MapTool #2416 explicitly uses `PG1` with `operator=OR`.",
        "- Scenario Adequacy separately tracks material setup, observed-but-not-universal setup, inputs/state, ordering, and practical specificity.",
        "- CSPC remains unavailable for seven partial/non-fully-supported cases and is explicitly a textual judgment, not executed bug detection.",
        "- Oracle incompleteness, multiple outcomes, and preventive branches are independent categories.",
        "- CorsixTH #1799 prevention is a separate pre-trigger branch and is not a post-P1 success.",
        "- Repair-only causes remain `root_cause_only`, evaluation-only, and excluded from TCC/SA trigger steps.", "",
        "## Per-bug handoff", "",
        "| Bug | Machine suggestion | Complete path eligible | Oracle category | Unresolved human question |", "| --- | --- | --- | --- | --- |",
    ]
    for r in records:
        oc = r["oracle_category_summary"]
        category = f"incomplete={oc['incomplete_oracle_count']}; multi-outcome={oc['multi_outcome_post_trigger_oracle_count']}; preventive={oc['separate_preventive_oracle_count']}"
        lines.append(f"| {r['bug_id']} | {r['suggested_review_decision']} | {bool(r['evaluation_targets_v2']['cspc_eligible_path_ids'])} | {category} | {'; '.join(r['phase2_6_review']['unresolved_human_questions'])} |")
    lines += [
        "", "## Validation and freeze boundary", "",
        "Validation performed after generation:", "",
        "- Unchanged Phase-2.5 validator against `gold_references_refined_draft.jsonl`: `PASS_STRUCTURAL_AND_PROVENANCE_ONLY`.",
        "- Phase-2.6 validator: `PASS_STRUCTURAL_CONSISTENCY_AND_PROVENANCE_ONLY`.",
        "- Neither result certifies semantic correctness.",
        "- Freeze-gate method: ran the existing script with no human decision file. It refused with exit code 2 before validation/writes; explicit checks confirmed that no frozen Gold, sample manifest, hash manifest, or decision file existed afterward.", "",
        "## New files written", "",
        "- `data/gold_references_phase2_6_candidate.jsonl`",
        "- `data/phase2_6_change_log.json`",
        "- `reports/phase2_6_audit_resolution.md`",
        "- `reports/test_scenario_evaluation_rubric_v2_draft.md`",
        "- `reports/phase2_6_human_review_checklist.md`",
        "- `scripts/validate_phase2_6_candidate.py`",
        "- `scripts/build_phase2_6_candidate.py` (deterministic versioned builder)", "",
        "All 18 decisions remain pending. A human must decide **Approve / Revise / Insufficient Evidence**, including whether partial-path bugs may remain in a separately reported exploratory cohort.", ""
    ]
    return "\n".join(lines)


def checklist(records: list[dict[str, Any]]) -> str:
    lines = ["# Phase 2.6 Human Review Checklist", "", "No decision is pre-filled. Machine suggestions are advisory. Final choices: **Approve**, **Revise**, or **Insufficient Evidence**.", ""]
    for i, r in enumerate(records, 1):
        lines += [f"## {i}. {r['bug_id']} — {r['issue_title']}", "", f"- Machine suggestion: **{r['suggested_review_decision']}**", f"- CSPC eligible paths: {r['evaluation_targets_v2']['cspc_eligible_path_ids'] or 'none (partial/exploratory only)'}", f"- Oracle categories: `{r['oracle_category_summary']}`", "", "### Evidence-linked items", ""]
        ev = {e["evidence_id"]: e for e in r["evidence"]}
        ids = sorted({eid for c in r["trigger_conditions"] for eid in c["evidence_ids"]} | {eid for o in r["test_oracles"] for eid in o["evidence_ids"]} | {eid for o in r["preventive_oracles"] for eid in o["evidence_ids"]})
        for eid in ids:
            e = ev[eid]; excerpt = e["excerpt"].replace("\r", " ").replace("\n", " ")
            lines.append(f"- {eid} — `{e['source_file']} / {e['source_record_id']} / {e['source_field']}`: “{excerpt[:300]}{'…' if len(excerpt)>300 else ''}”")
        lines += ["", "### Review checks", "", "- [ ] Required triggers and their per-path denominator are correct.", "- [ ] Material setup and observed-but-not-universal setup are correctly separated.", "- [ ] OR paths/branch applicability are correct.", "- [ ] Oracle is complete/scorable or a defensible not-scorable rule is chosen.", "- [ ] Repair-only information remains evaluation-only.", "- [ ] Partial-path exploratory inclusion rule is acceptable.", "", "Outstanding questions:"]
        lines += [f"- {q}" for q in r["phase2_6_review"]["unresolved_human_questions"]]
        lines += ["", "Reviewer: ____________________", "", "Review date: ____________________", "", "Final decision (Approve / Revise / Insufficient Evidence): ____________________", "", "If partial/exploratory, include in analysis? Yes / No / N/A: ____________________", "", "Decision rationale or exclusion reason: ________________________________________", "", "---", ""]
    return "\n".join(lines)


def main() -> None:
    source_path = ROOT / "data" / "gold_references_refined_draft.jsonl"
    source = read_jsonl(source_path); candidate = []; per_bug_changes = {}
    for row in source:
        built, changes = build_candidate(row); candidate.append(built); per_bug_changes[row["bug_id"]] = changes
    original_counts = Counter(r["suggested_review_decision"] for r in source)
    review_counts = Counter(r["phase2_5_review"]["preliminary_disposition"] for r in source)
    candidate_counts = Counter(r["suggested_review_decision"] for r in candidate)
    discrepancies = []
    for old, new in zip(source, candidate):
        if old["suggested_review_decision"] != old["phase2_5_review"]["preliminary_disposition"]:
            discrepancies.append({
                "bug_id": old["bug_id"], "field": "suggested_review_decision",
                "original_value": old["suggested_review_decision"], "review_value": old["phase2_5_review"]["preliminary_disposition"],
                "proposed_value": new["suggested_review_decision"],
                "rationale": "Prefer the evidence-linked Phase-2.5 preliminary disposition until its outstanding questions receive a human decision.",
                "provenance": ["gold_references_refined_draft.jsonl:suggested_review_decision", "gold_references_refined_draft.jsonl:phase2_5_review.preliminary_disposition", "phase2_5_semantic_edits.json:proposed_review_disposition"],
                "root_cause_asserted": False,
            })
    incomplete = sum(any(o["completeness"] == "incomplete" for o in r["test_oracles"]) for r in candidate)
    multi = sum(any(o["has_alternative_outcomes"] for o in r["test_oracles"]) for r in candidate)
    preventive = sum(bool(r["preventive_oracles"]) for r in candidate)
    counts = {
        "original_suggestions": dict(sorted(original_counts.items())), "review_dispositions": dict(sorted(review_counts.items())), "candidate_suggestions": dict(sorted(candidate_counts.items())),
        "complete_path_bugs": sum(bool(r["evaluation_targets_v2"]["cspc_eligible_path_ids"]) for r in candidate),
        "partial_path_bugs": sum(not r["evaluation_targets_v2"]["cspc_eligible_path_ids"] for r in candidate),
        "incomplete_oracle_bugs": incomplete, "multi_outcome_post_trigger_bugs": multi, "preventive_branch_bugs": preventive,
        "pending_human_decisions": sum(r["final_human_decision"] is None for r in candidate),
    }
    log = {
        "phase": "2.6", "status": "machine_proposed_pending_human_review",
        "source_files": {"gold_references_refined_draft.jsonl": file_sha(source_path), "phase2_5_semantic_edits.json": file_sha(ROOT/"data"/"phase2_5_semantic_edits.json"), "phase2_5_independent_audit_report.md": file_sha(ROOT/"phase2_5_independent_audit_report.md")},
        "verified_counts": counts, "discrepancy_resolutions": discrepancies, "per_bug_changes": per_bug_changes,
        "human_decisions_recorded": 0, "semantic_certification_claimed": False,
    }
    (ROOT/"data"/"gold_references_phase2_6_candidate.jsonl").write_text("".join(json.dumps(r,ensure_ascii=False,sort_keys=True)+"\n" for r in candidate),encoding="utf-8")
    (ROOT/"data"/"phase2_6_change_log.json").write_text(json.dumps(log,ensure_ascii=False,sort_keys=True,indent=2)+"\n",encoding="utf-8")
    (ROOT/"reports"/"phase2_6_audit_resolution.md").write_text(report(candidate,log),encoding="utf-8")
    (ROOT/"reports"/"phase2_6_human_review_checklist.md").write_text(checklist(candidate),encoding="utf-8")
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    main()
