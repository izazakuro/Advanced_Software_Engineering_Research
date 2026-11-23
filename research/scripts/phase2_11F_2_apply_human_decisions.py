#!/usr/bin/env python3
"""Apply authoritative Phase 2.11F-2 human decisions to new unfrozen Gold records."""

from __future__ import annotations

import copy
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "outputs/phase2_11F_2"
SOURCE_PACKAGE = ROOT / "data/phase2_11F_1_gold_evidence/phase2_11F_1_gold_evidence.jsonl"
DECISIONS = ROOT / "gold_human_decisions_working.json"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def find(items: list[dict], key: str, value: str) -> dict:
    return next(item for item in items if item[key] == value)


def change(log: list[dict], field: str, old: object, new: object, justification: str, evidence_ids: list[str] | None = None) -> None:
    if old == new:
        return
    log.append({"field": field, "old_value": copy.deepcopy(old), "new_value": copy.deepcopy(new),
                "human_decision_justification": justification, "supporting_evidence_ids": evidence_ids or [],
                "application_status": "APPLIED"})


def assign(log: list[dict], obj: dict, key: str, value: object, field: str, justification: str,
           evidence_ids: list[str] | None = None) -> None:
    old = copy.deepcopy(obj.get(key))
    change(log, field, old, value, justification, evidence_ids)
    obj[key] = copy.deepcopy(value)


def add_limitation(record: dict, log: list[dict], text: str, justification: str) -> None:
    if text in record["known_limitations"]:
        return
    old = copy.deepcopy(record["known_limitations"])
    record["known_limitations"].append(text)
    change(log, "known_limitations", old, record["known_limitations"], justification)


def evidence_references(value: object, inside_evidence: bool = False) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        now_inside = inside_evidence or ("evidence_id" in value and "excerpt" in value)
        if not now_inside and isinstance(value.get("evidence_ids"), list):
            refs.update(item for item in value["evidence_ids"] if isinstance(item, str))
        for child in value.values():
            refs.update(evidence_references(child, now_inside))
    elif isinstance(value, list):
        for child in value:
            refs.update(evidence_references(child, inside_evidence))
    return refs


def main() -> None:
    if OUTPUT.exists():
        raise SystemExit("outputs/phase2_11F_2 already exists; refusing to overwrite")
    OUTPUT.mkdir(parents=True)
    decision_doc = json.loads(DECISIONS.read_text(encoding="utf-8"))
    decisions = {item["bug_id"]: item for item in decision_doc["decisions"]}
    wrappers = jsonl(SOURCE_PACKAGE)
    source_gold = {row["bug_id"]: row["gold_candidate"] for row in wrappers}
    wrapper_by_id = {row["bug_id"]: row for row in wrappers}
    ordered_ids = [row["bug_id"] for row in wrappers]
    if set(decisions) != set(ordered_ids):
        raise SystemExit("human decision IDs do not match F-1 Gold IDs")

    source_paths = [
        DECISIONS, SOURCE_PACKAGE,
        ROOT / "phase2_11F_1_gold_reference_evidence_package.zip",
        ROOT / "data/gold_references_phase2_6_candidate.jsonl",
        ROOT / "data/phase2_11F_1_gold_evidence/phase2_11F_1_validation.json",
        ROOT / "phase2_11E_9_codex_handoff/phase2_11C_gold_18_structural_evidence_audit.csv",
        ROOT / "phase2_11E_9_codex_handoff/phase2_11D_ABC_manifest_NOT_FROZEN.jsonl",
        ROOT / "phase2_11E_9_codex_handoff/phase2_11E_9_final_human_eligibility_108.csv",
        ROOT / "phase2_11E_9_codex_handoff/test_scenario_evaluation_rubric_v3_PROPOSED.md",
    ]
    source_hashes = {str(path.relative_to(ROOT)): sha(path) for path in source_paths}

    partial_bugs = {
        "CorsixTH/CorsixTH#2881", "colobot/colobot#1787", "freeorion/freeorion#1897",
        "freeorion/freeorion#2365", "gemrb/gemrb#1878", "longturn/freeciv21#1189",
        "warzone2100/warzone2100#4125",
    }
    strict_status = {
        "CorsixTH/CorsixTH#1799": {"O1": "eligible_pending_final_gold_verification", "OP1": "preventive_branch_only_not_P1_strict_oracle"},
        "CorsixTH/CorsixTH#2881": {"O1": "ineligible_incomplete_oracle"},
        "colobot/colobot#1787": {"O1": "ineligible_incomplete_oracle"},
        "flame-engine/flame#1003": {"O1": "eligible_human_approved"},
        "flame-engine/flame#1264": {"O1": "eligible_human_approved"},
        "freeorion/freeorion#1897": {"O1": "ineligible_incomplete_oracle"},
        "freeorion/freeorion#2365": {"O1": "ineligible_incomplete_oracle"},
        "gameservermanagers/linuxgsm#4059": {"O1": "eligible_human_approved"},
        "gameservermanagers/linuxgsm#4251": {"O1": "eligible_human_approved"},
        "gemrb/gemrb#1878": {"O1": "ineligible_incomplete_oracle"},
        "lichess-org/lila#12907": {"O1": "eligible_human_approved"},
        "longturn/freeciv21#1189": {"O1": "eligible_candidate_pending_final_gold_verification"},
        "rptools/maptool#2393": {"O1": "eligible_human_approved"},
        "rptools/maptool#2416": {"O1": "eligible_human_approved", "O2": "eligible_human_approved"},
        "tmodloader/tmodloader#2644": {"O1": "pending_rubric_decision"},
        "vcmi/vcmi#5462": {"O1": "pending_rubric_decision"},
        "warzone2100/warzone2100#4125": {"O1": "ineligible_incomplete_oracle"},
        "wolvenkit/wolvenkit#2279": {"O1": "ineligible_incomplete_oracle"},
    }

    revised_records: list[dict] = []
    application_logs: list[dict] = []
    diff_sections = ["# Phase 2.11F-2 Gold Revision Diff", "",
                     "All changes below derive from `gold_human_decisions_working.json`. REVISE does not approve the resulting record.", ""]
    final_queue = ["# Phase 2.11F-2 Final Human Gold Review Queue", "",
                   "No record is frozen. The ten REVISE results require final human verification.", ""]

    for bug_id in ordered_ids:
        original = source_gold[bug_id]
        record = copy.deepcopy(original)
        decision = decisions[bug_id]
        human_decision = decision["human_decision"]
        note = decision["notes"]
        modifications: list[dict] = []
        justification = f"Authoritative Phase 2.11F-2 human decision ({human_decision}): {note}"

        # Explicit per-target content clarifications.
        if bug_id == "CorsixTH/CorsixTH#1799":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "ambiguity_status", "clear", "test_oracles.O1.ambiguity_status", justification, ["E3", "E4"])
            assign(modifications, oracle, "ambiguity_reasons", [], "test_oracles.O1.ambiguity_reasons", justification, ["E3", "E4"])
            assign(modifications, oracle, "applicable_branch_ids", ["B_POST"], "test_oracles.O1.applicable_branch_ids", justification, ["E3", "E4"])
            preventive = find(record["preventive_oracles"], "oracle_id", "OP1")
            assign(modifications, preventive, "applicable_branch_ids", ["B_PREVENT"], "preventive_oracles.OP1.applicable_branch_ids", justification, ["E8"])
            add_limitation(record, modifications, "The attached save fixture is referenced but was not independently verified or archived in this phase.", justification)
        elif bug_id == "flame-engine/flame#1264":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result",
                   "Visible tiles respect the documented layer and occlusion order; pixel-identical rendering is not required.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E4", "E6"])
            assign(modifications, oracle, "verification_method", "Visible-tile and layer/occlusion-order comparison",
                   "test_oracles.O1.verification_method", justification, ["E3", "E4", "E6"])
        elif bug_id == "freeorion/freeorion#1897":
            add_limitation(record, modifications, "Starting a second or later game is an observed boundary, not a guaranteed reproduction recipe.", justification)
        elif bug_id == "freeorion/freeorion#2365":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result",
                   "Attempting the XML save does not end in the documented AddressSanitizer abnormal-allocation termination; successful XML loading is not required.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E5"])
        elif bug_id == "gemrb/gemrb#1878":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result", "The travel attempt does not terminate the process with SIGSEGV.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E5"])
            assign(modifications, oracle, "incorrect_result", "The process terminates with SIGSEGV during the travel attempt; a specific Actor::Die stack is not required.",
                   "test_oracles.O1.incorrect_result", justification, ["E3"])
        elif bug_id == "lichess-org/lila#12907":
            condition = find(record["trigger_conditions"], "condition_id", "T1")
            assign(modifications, condition, "description",
                   "Use a controlled in-progress game with enough already-played moves to verify FEN fullmove progression; the historical live game ID is not required.",
                   "trigger_conditions.T1.description", justification, condition.get("evidence_ids", []))
        elif bug_id == "tmodloader/tmodloader#2644":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result", "Startup does not stop with the documented System.ComponentModel.Win32Exception: Permission denied.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E5"])
            assign(modifications, oracle, "incorrect_result", "Startup stops with System.ComponentModel.Win32Exception: Permission denied.",
                   "test_oracles.O1.incorrect_result", justification, ["E3"])
        elif bug_id == "vcmi/vcmi#5462":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result", "The client remains running while attempting to create a multiplayer room; successful room creation is not required by this Oracle.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E5"])
        elif bug_id == "warzone2100/warzone2100#4125":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result", "The host does not enter a persistent repeated-kick loop and remains responsive; exactly one kick per player is not required.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E4", "E6"])
        elif bug_id == "wolvenkit/wolvenkit#2279":
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            assign(modifications, oracle, "expected_result",
                   "The CLI does not enter infinite MergeMaterialChain recursion or terminate with Stack Overflow; successful export is not required when the game path is invalid.",
                   "test_oracles.O1.expected_result", justification, ["E3", "E5"])

        # Keep the machine-readable acceptable outcome aligned with each
        # human-authorized Oracle boundary clarification.
        if bug_id in {
            "CorsixTH/CorsixTH#1799", "flame-engine/flame#1264",
            "freeorion/freeorion#2365", "gemrb/gemrb#1878",
            "tmodloader/tmodloader#2644", "vcmi/vcmi#5462",
            "warzone2100/warzone2100#4125", "wolvenkit/wolvenkit#2279",
        }:
            oracle = find(record["test_oracles"], "oracle_id", "O1")
            outcomes = oracle.get("acceptable_expected_outcomes", [])
            if len(outcomes) == 1:
                assign(modifications, outcomes[0], "description", oracle["expected_result"],
                       "test_oracles.O1.acceptable_expected_outcomes.A1.description",
                       justification, outcomes[0].get("evidence_ids", []))

        # Recompute path-level metadata without adding trigger conditions.
        for path in record["trigger_paths"]:
            is_partial = bug_id in partial_bugs
            assign(modifications, path, "tcc_denominator", len(path["required_condition_ids"]),
                   f"trigger_paths.{path['path_id']}.tcc_denominator", justification, path.get("evidence_ids", []))
            assign(modifications, path, "complete_path_metric_eligible", not is_partial,
                   f"trigger_paths.{path['path_id']}.complete_path_metric_eligible", justification, path.get("evidence_ids", []))
            assign(modifications, path, "completeness", "partial" if is_partial else "complete",
                   f"trigger_paths.{path['path_id']}.completeness", justification, path.get("evidence_ids", []))
            assign(modifications, path, "fully_supported", not is_partial,
                   f"trigger_paths.{path['path_id']}.fully_supported", justification, path.get("evidence_ids", []))
            assign(modifications, path, "cspc_eligibility", "not_applicable_partial_or_unsupported" if is_partial else "eligible_under_current_human_decision_unfrozen",
                   f"trigger_paths.{path['path_id']}.cspc_eligibility", justification, path.get("evidence_ids", []))
            assign(modifications, path, "runtime_reproduction_verified", False,
                   f"trigger_paths.{path['path_id']}.runtime_reproduction_verified", justification)
            assign(modifications, path["scenario_adequacy"], "practical_specificity_status",
                   "limited_or_partial" if is_partial else "documented_complete",
                   f"trigger_paths.{path['path_id']}.scenario_adequacy.practical_specificity_status",
                   justification, path.get("evidence_ids", []))

        # Independent Oracle origin/completeness plus backward-compatible strict-scoring extension.
        for oracle in record["test_oracles"]:
            strict = strict_status[bug_id][oracle["oracle_id"]]
            assign(modifications, oracle, "strict_oracle_scoring_eligibility", strict,
                   f"test_oracles.{oracle['oracle_id']}.strict_oracle_scoring_eligibility", justification, oracle.get("evidence_ids", []))
            if strict == "eligible_human_approved":
                scoring = "scorable_under_human_approved_gold_unfrozen"
            elif strict in {"pending_rubric_decision", "eligible_candidate_pending_final_gold_verification"}:
                scoring = strict
            elif strict == "eligible_pending_final_gold_verification":
                scoring = "pending_final_gold_verification"
            else:
                scoring = "strict_oracle_ineligible"
            assign(modifications, oracle, "scoring_status", scoring,
                   f"test_oracles.{oracle['oracle_id']}.scoring_status", justification, oracle.get("evidence_ids", []))
        for oracle in record.get("preventive_oracles", []):
            strict = strict_status.get(bug_id, {}).get(oracle["oracle_id"], "branch_specific_pending_human_review")
            assign(modifications, oracle, "strict_oracle_scoring_eligibility", strict,
                   f"preventive_oracles.{oracle['oracle_id']}.strict_oracle_scoring_eligibility", justification, oracle.get("evidence_ids", []))

        eligible_paths = [path["path_id"] for path in record["trigger_paths"] if path["complete_path_metric_eligible"]]
        required_union = []
        for path in record["trigger_paths"]:
            for condition_id in path["required_condition_ids"]:
                if condition_id not in required_union:
                    required_union.append(condition_id)
        evaluation = record["evaluation_targets"]
        assign(modifications, evaluation, "complete_path_coverage_applicable", bool(eligible_paths),
               "evaluation_targets.complete_path_coverage_applicable", justification)
        assign(modifications, evaluation, "complete_supported_path_ids", eligible_paths,
               "evaluation_targets.complete_supported_path_ids", justification)
        assign(modifications, evaluation, "eligible_required_condition_ids", required_union,
               "evaluation_targets.eligible_required_condition_ids", justification)
        assign(modifications, evaluation, "condition_coverage_denominator", len(required_union),
               "evaluation_targets.condition_coverage_denominator", justification)
        v2 = record["evaluation_targets_v2"]
        assign(modifications, v2, "cspc_eligible_path_ids", eligible_paths,
               "evaluation_targets_v2.cspc_eligible_path_ids", justification)
        assign(modifications, v2, "partial_exploratory_only", not bool(eligible_paths),
               "evaluation_targets_v2.partial_exploratory_only", justification)
        assign(modifications, v2["tcc"], "path_denominators",
               {path["path_id"]: path["tcc_denominator"] for path in record["trigger_paths"]},
               "evaluation_targets_v2.tcc.path_denominators", justification)
        for branch in record.get("outcome_branches", []):
            branch_eligible = any(path_id in eligible_paths for path_id in branch.get("applicable_path_ids", []))
            if branch.get("branch_type") == "preventive_pre_trigger":
                branch_eligible = False
            assign(modifications, branch, "cspc_eligible", branch_eligible,
                   f"outcome_branches.{branch['branch_id']}.cspc_eligible", justification)

        oracle_category_summary = {
            "incomplete_oracle_count": sum(oracle["completeness"] == "incomplete" for oracle in record["test_oracles"]),
            "multi_outcome_post_trigger_oracle_count": sum(len(oracle.get("acceptable_expected_outcomes", [])) > 1 for oracle in record["test_oracles"]),
            "separate_preventive_oracle_count": len(record.get("preventive_oracles", [])),
            "categories_are_independent": True,
        }
        record["oracle_category_summary"] = oracle_category_summary
        record["source_schema_version"] = original.get("schema_version")
        record["schema_version"] = "2.11F-2-human-decision-applied-unfrozen"
        record["final_human_decision"] = human_decision
        record["annotation_status"] = "human_approved_unfrozen" if human_decision == "APPROVE" else "human_revised_pending_final_verification"
        record["gold_frozen"] = False
        record["runtime_reproduction_claimed"] = False
        record["human_review"] = {
            "decision": human_decision, "decision_notes_verbatim": note,
            "revision_requirements": decision.get("revision_requirements", []),
            "final_verification_required": human_decision == "REVISE",
            "approved_limitations": [note] if human_decision == "APPROVE" else [],
            "source": "gold_human_decisions_working.json",
        }
        record["evidence_use_policy"] = {
            "target_issue_evidence": "original_reproduction_and_oracle_evidence",
            "later_comments_and_repair_evidence": "evaluation_only_never_generation_input",
            "future_repair_may_define_required_trigger": False,
        }

        # Log governance/schema changes too, ensuring every REVISE has a traceable application log.
        for field, old, new in (
            ("schema_version", original.get("schema_version"), record["schema_version"]),
            ("final_human_decision", original.get("final_human_decision"), human_decision),
            ("annotation_status", original.get("annotation_status"), record["annotation_status"]),
            ("gold_frozen", original.get("gold_frozen"), False),
            ("human_review", original.get("human_review"), record["human_review"]),
            ("evidence_use_policy", original.get("evidence_use_policy"), record["evidence_use_policy"]),
            ("source_schema_version", original.get("source_schema_version"), record["source_schema_version"]),
            ("runtime_reproduction_claimed", original.get("runtime_reproduction_claimed"), False),
            ("oracle_category_summary", original.get("oracle_category_summary"), oracle_category_summary),
        ):
            change(modifications, field, old, new, justification)

        original_condition_ids = [item["condition_id"] for item in original["trigger_conditions"]]
        new_condition_ids = [item["condition_id"] for item in record["trigger_conditions"]]
        application_status = "APPLIED_AWAITING_FINAL_HUMAN_VERIFICATION" if human_decision == "REVISE" else "APPROVED_DEFINITION_PRESERVED_UNFROZEN"
        log_entry = {
            "bug_id": bug_id, "human_decision": human_decision, "human_notes_verbatim": note,
            "application_status": application_status, "field_level_modifications": modifications,
            "modification_count": len(modifications), "original_condition_ids": original_condition_ids,
            "result_condition_ids": new_condition_ids, "unsupported_new_trigger_introduced": original_condition_ids != new_condition_ids,
            "evidence_justification": sorted({eid for item in modifications for eid in item["supporting_evidence_ids"]}),
            "remaining_limitations": record["known_limitations"], "gold_frozen": False,
        }
        revised_records.append(record)
        application_logs.append(log_entry)

        diff_sections.extend([f"## `{bug_id}` — {human_decision}", "", f"Human note: {note}", ""])
        if modifications:
            for item in modifications:
                diff_sections.extend([f"### `{item['field']}`", "", "**Before**", "",
                    "```json", json.dumps(item["old_value"], ensure_ascii=False, indent=2, sort_keys=True), "```", "",
                    "**After**", "", "```json", json.dumps(item["new_value"], ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
        else:
            diff_sections.extend(["No field change was needed; the approved candidate was preserved exactly.", ""])

        wrapper = wrapper_by_id[bug_id]
        if human_decision == "REVISE":
            final_queue.extend([f"## `{bug_id}` — FINAL VERIFICATION REQUIRED", "", f"Human revision note: {note}", "",
                f"Applied modifications: {len(modifications)}", "",
                f"Evidence IDs: {', '.join(wrapper['traceability_audit']['referenced_evidence_ids'])}", "",
                "Recommendation: verify the revised boundary against the cited evidence; keep this record REVISE and unfrozen until a human explicitly accepts the resulting artifact.", "",
                "- [ ] Confirm every authorized revision is represented.",
                "- [ ] Confirm no reproduction step was invented.",
                "- [ ] Confirm TCC/CSPC/Oracle metadata matches the intended boundary.",
                "- Final human decision on revised artifact: ______", "- Reviewer reason: ______", ""])
        else:
            questions = wrapper["gap_audit"]["unresolved_human_questions"]
            final_queue.extend([f"## `{bug_id}` — APPROVED / UNFROZEN", "", f"Approved note: {note}", "",
                f"Unresolved preservation questions only: {'; '.join(questions) if questions else 'none'}", ""])

    output_records = OUTPUT / "gold_references_human_revised.jsonl"
    output_log = OUTPUT / "human_decision_application_log.jsonl"
    output_diff = OUTPUT / "gold_revision_diff.md"
    output_validation = OUTPUT / "gold_validation_report.json"
    output_queue = OUTPUT / "gold_final_human_review_queue.md"
    output_summary = OUTPUT / "phase2_11F_2_summary.md"
    write_jsonl(output_records, revised_records)
    write_jsonl(output_log, application_logs)
    output_diff.write_text("\n".join(diff_sections), encoding="utf-8")
    output_queue.write_text("\n".join(final_queue), encoding="utf-8")

    source_hashes_after = {name: sha(ROOT / name) for name in source_hashes}
    errors = []
    warnings = []
    counts = Counter(record["final_human_decision"] for record in revised_records)
    if len(revised_records) != 18 or len({record["bug_id"] for record in revised_records}) != 18:
        errors.append("Expected 18 unique Gold records")
    if counts != Counter({"REVISE": 10, "APPROVE": 8}):
        errors.append(f"Human decision counts mismatch: {dict(counts)}")
    for record, log in zip(revised_records, application_logs):
        bug_id = record["bug_id"]
        condition_ids = {item["condition_id"] for item in record["trigger_conditions"]}
        source_condition_ids = {item["condition_id"] for item in source_gold[bug_id]["trigger_conditions"]}
        if condition_ids != source_condition_ids:
            errors.append(f"{bug_id}: trigger condition IDs changed")
        for path in record["trigger_paths"]:
            if path["tcc_denominator"] != len(path["required_condition_ids"]):
                errors.append(f"{bug_id}/{path['path_id']}: TCC denominator mismatch")
            if not set(path["required_condition_ids"]) <= condition_ids:
                errors.append(f"{bug_id}/{path['path_id']}: unknown required condition")
            if path["completeness"] == "partial" and path["complete_path_metric_eligible"]:
                errors.append(f"{bug_id}/{path['path_id']}: partial path CSPC eligible")
        if record["final_human_decision"] == "REVISE" and (not log["field_level_modifications"] or not record["human_review"]["final_verification_required"]):
            errors.append(f"{bug_id}: REVISE lacks change log/final verification gate")
        if record["gold_frozen"]:
            errors.append(f"{bug_id}: Gold frozen")
        if record["evidence"] != source_gold[bug_id]["evidence"]:
            errors.append(f"{bug_id}: original evidence changed")
        evidence_ids = {item["evidence_id"] for item in record["evidence"]}
        referenced = evidence_references(record)
        if not referenced <= evidence_ids:
            errors.append(f"{bug_id}: unresolved evidence IDs")
        if any(item.get("future_repair_only") and item.get("role") != "root_cause_only" for item in record["trigger_conditions"]):
            errors.append(f"{bug_id}: repair-only condition promoted to trigger")
        evidence_by_id = {item["evidence_id"]: item for item in record["evidence"]}
        for condition in record["trigger_conditions"]:
            if condition.get("role") == "required_trigger" and any(
                evidence_by_id[eid].get("temporal_role") == "evaluation_only_future"
                for eid in condition.get("evidence_ids", []) if eid in evidence_by_id
            ):
                errors.append(f"{bug_id}: required trigger depends on future repair evidence")
        for path in record["trigger_paths"]:
            expression_ids = set(re.findall(r"T(?:_RC)?\w+", path["expression"]))
            if expression_ids != set(path["required_condition_ids"]):
                errors.append(f"{bug_id}/{path['path_id']}: path expression mismatch")
        oracle_ids = {item["oracle_id"] for item in record["test_oracles"] + record.get("preventive_oracles", [])}
        for oracle in record["test_oracles"]:
            if oracle.get("status") not in {"explicit", "inferred"} or oracle.get("completeness") not in {"complete", "incomplete"}:
                errors.append(f"{bug_id}/{oracle['oracle_id']}: invalid independent Oracle dimensions")
            if "strict_oracle_scoring_eligibility" not in oracle:
                errors.append(f"{bug_id}/{oracle['oracle_id']}: strict Oracle eligibility missing")
        for branch in record.get("outcome_branches", []):
            if not set(branch.get("oracle_ids", [])) <= oracle_ids:
                errors.append(f"{bug_id}/{branch['branch_id']}: unresolved branch Oracle")
            if branch.get("branch_type") == "preventive_pre_trigger" and branch.get("cspc_eligible"):
                errors.append(f"{bug_id}/{branch['branch_id']}: preventive branch counted in CSPC")
        if record["human_review"]["decision_notes_verbatim"] != decisions[bug_id]["notes"]:
            errors.append(f"{bug_id}: exact human decision note changed")
    if not all(source_hashes[name] == source_hashes_after[name] for name in source_hashes):
        errors.append("Original source artifact changed")
    warnings.extend(["Ten REVISE records require final human verification before any Gold freeze.",
                     "Strict Oracle eligibility for tmodloader#2644 and vcmi#5462 remains pending Rubric decision.",
                     "No runtime reproduction was performed in this phase."])
    validation = {
        "status": "PASS_HUMAN_DECISIONS_APPLIED_UNFROZEN" if not errors else "FAIL",
        "record_count": len(revised_records), "unique_bug_ids": len({record["bug_id"] for record in revised_records}),
        "human_decision_counts": dict(counts), "revise_logs_with_modifications": sum(log["human_decision"] == "REVISE" and bool(log["field_level_modifications"]) for log in application_logs),
        "approved_records_unfrozen": sum(record["final_human_decision"] == "APPROVE" and not record["gold_frozen"] for record in revised_records),
        "tcc_denominator_checks_passed": not any("TCC denominator" in error for error in errors),
        "partial_paths_excluded_from_CSPC": not any("partial path CSPC" in error for error in errors),
        "evidence_resolution_passed": not any("evidence IDs" in error for error in errors),
        "repair_evidence_isolated": not any("repair-only" in error or "future repair evidence" in error for error in errors),
        "path_expression_dependency_consistency": not any("path expression" in error for error in errors),
        "oracle_dimension_consistency": not any("Oracle dimensions" in error or "strict Oracle eligibility" in error for error in errors),
        "oracle_branch_scope_consistency": not any("branch Oracle" in error or "preventive branch" in error for error in errors),
        "human_notes_preserved_verbatim": not any("decision note" in error for error in errors),
        "original_evidence_and_excerpts_unchanged": not any("original evidence" in error for error in errors),
        "original_artifacts_unchanged": not any("source artifact" in error for error in errors),
        "validation_scope": "structural_and_evidence_alignment_only",
        "semantic_or_runtime_correctness_claimed": False,
        "gold_frozen": False, "rubric_frozen": False, "ABC_manifest_frozen": False,
        "official_model_calls": 0, "errors": errors, "warnings": warnings,
        "source_hashes": {name: {"before": source_hashes[name], "after": source_hashes_after[name], "unchanged": source_hashes[name] == source_hashes_after[name]} for name in source_hashes},
    }
    output_validation.write_text(json.dumps(validation, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    summary = f"""# Phase 2.11F-2 Summary

Status: **{validation['status']}**.

- Gold records: 18 unique.
- Human decisions preserved: 8 APPROVE, 10 REVISE.
- REVISE records with traceable field-level logs: {validation['revise_logs_with_modifications']}/10.
- All Gold records remain unfrozen.
- Official model calls: 0.

The ten revised artifacts await final human verification. APPROVE records preserve their exact decision notes and authorized limitations. Rubric and ABC Manifest remain unfrozen. Do not proceed to official generation.

## Remaining blockers

- Final verification of all ten REVISE outputs.
- Rubric decision for pending strict Oracle eligibility, including tModLoader #2644 and VCMI #5462.
- Gold freeze must occur only in a later explicitly authorized phase.
- Model/configuration/schedule freeze and explicit generation authorization remain outstanding.
"""
    output_summary.write_text(summary, encoding="utf-8")
    print(json.dumps({"status": validation["status"], "output": str(OUTPUT),
                      "decisions": dict(counts), "revise_logs": validation["revise_logs_with_modifications"],
                      "errors": errors, "official_model_calls": 0}, indent=2, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
