#!/usr/bin/env python3
"""Apply auditable Phase-2.5 semantic edits to the Phase-2 draft.

The semantic judgments live in data/phase2_5_semantic_edits.json. This script
only applies those declared edits deterministically, records before/after values,
and writes review artifacts. It never approves a record.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROLES = {"required_trigger", "reproduction_setup", "verification_action", "uncertain_condition", "root_cause_only"}
NECESSITIES = {"supported", "not_established", "not_applicable"}


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=here.parents[1])
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def concise(value: Any, limit: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def change_entry(kind: str, target_id: str, original: Any, proposed: Any, evidence_ids: list[str], rationale: str) -> dict[str, Any]:
    return {
        "change_type": kind, "target_id": target_id, "original_value": original,
        "proposed_value": proposed, "evidence_ids": evidence_ids,
        "rationale": rationale, "review_status": "machine_proposed_pending_human_decision",
    }


def apply_one(original: dict[str, Any], edit: dict[str, Any], frozen: dict[str, Any]) -> dict[str, Any]:
    refined = copy.deepcopy(original)
    changes: list[dict[str, Any]] = []
    evidence_ids = {e["evidence_id"] for e in refined["evidence"]}

    for item in edit.get("add_evidence", []):
        if item["evidence_id"] in evidence_ids:
            raise ValueError(f"{original['bug_id']}: duplicate added evidence {item['evidence_id']}")
        if item["source_type"] == "issue" and item["excerpt"] not in frozen["original_issue_body"]:
            raise ValueError(f"{original['bug_id']}: added Issue excerpt is not in frozen source")
        refined["evidence"].append(copy.deepcopy(item)); evidence_ids.add(item["evidence_id"])
        changes.append(change_entry("evidence_added", item["evidence_id"], None, item, [item["evidence_id"]], edit["rationale"]))

    by_condition = {c["condition_id"]: c for c in refined["trigger_conditions"]}
    for cid, update in edit["condition_updates"].items():
        if cid not in by_condition:
            raise ValueError(f"{original['bug_id']}: unknown condition {cid}")
        before = copy.deepcopy(by_condition[cid])
        by_condition[cid].update(copy.deepcopy(update))
        after = copy.deepcopy(by_condition[cid])
        changes.append(change_entry("condition_refined", cid, before, after, after.get("evidence_ids", []), edit["rationale"]))
    for condition in edit.get("add_conditions", []):
        cid = condition["condition_id"]
        if cid in by_condition:
            raise ValueError(f"{original['bug_id']}: duplicate condition {cid}")
        refined["trigger_conditions"].append(copy.deepcopy(condition)); by_condition[cid] = refined["trigger_conditions"][-1]
        changes.append(change_entry("condition_added", cid, None, condition, condition.get("evidence_ids", []), edit["rationale"]))
    for condition in refined["trigger_conditions"]:
        if condition.get("role") not in ROLES or condition.get("necessity") not in NECESSITIES or not isinstance(condition.get("future_repair_only"), bool):
            raise ValueError(f"{original['bug_id']}/{condition['condition_id']}: incomplete refined role metadata")
        if not set(condition["evidence_ids"]) <= evidence_ids:
            raise ValueError(f"{original['bug_id']}/{condition['condition_id']}: unresolved condition evidence")

    by_path = {p["path_id"]: p for p in refined["trigger_paths"]}
    for pid, update in edit["path_updates"].items():
        if pid not in by_path:
            raise ValueError(f"{original['bug_id']}: unknown path {pid}")
        before = copy.deepcopy(by_path[pid])
        by_path[pid].update(copy.deepcopy(update))
        by_path[pid]["expression"] = " AND ".join(by_path[pid]["required_condition_ids"])
        by_path[pid]["complete_path_metric_eligible"] = bool(by_path[pid]["fully_supported"] and by_path[pid]["completeness"] == "complete")
        after = copy.deepcopy(by_path[pid])
        path_evidence = sorted({eid for cid in after["required_condition_ids"] + after["setup_condition_ids"] for eid in by_condition[cid]["evidence_ids"]})
        after["evidence_ids"] = path_evidence
        by_path[pid]["evidence_ids"] = path_evidence
        changes.append(change_entry("trigger_path_rebuilt", pid, before, after, path_evidence, edit["rationale"]))

    if "expected_behavior_update" in edit:
        before = copy.deepcopy(refined["expected_behavior"])
        refined["expected_behavior"].update(copy.deepcopy(edit["expected_behavior_update"]))
        changes.append(change_entry("expected_behavior_refined", "expected_behavior", before, refined["expected_behavior"], refined["expected_behavior"]["evidence_ids"], edit["rationale"]))

    by_oracle = {o["oracle_id"]: o for o in refined["test_oracles"]}
    for oid, update in edit.get("oracle_updates", {}).items():
        if oid not in by_oracle:
            raise ValueError(f"{original['bug_id']}: unknown Oracle {oid}")
        before = copy.deepcopy(by_oracle[oid]); by_oracle[oid].update(copy.deepcopy(update)); after = copy.deepcopy(by_oracle[oid])
        changes.append(change_entry("oracle_refined", oid, before, after, after.get("evidence_ids", []), edit["rationale"]))
    for oracle in refined["test_oracles"]:
        if "acceptable_expected_outcomes" not in oracle:
            oracle["acceptable_expected_outcomes"] = [{"outcome_id": "A1", "description": oracle["expected_result"], "evidence_ids": oracle["evidence_ids"]}]
        oracle["ambiguity_status"] = "ambiguous" if oracle["completeness"] == "incomplete" or len(oracle["acceptable_expected_outcomes"]) > 1 else "clear"

    eligible_required = sorted({cid for path in refined["trigger_paths"] for cid in path["required_condition_ids"] if by_condition[cid]["role"] == "required_trigger" and by_condition[cid]["necessity"] == "supported"})
    complete_paths = sorted(p["path_id"] for p in refined["trigger_paths"] if p["complete_path_metric_eligible"])
    refined["evaluation_targets"] = {
        "eligible_required_condition_ids": eligible_required,
        "condition_coverage_denominator": len(eligible_required),
        "complete_supported_path_ids": complete_paths,
        "complete_path_coverage_applicable": bool(complete_paths),
        "oracle_ids": [o["oracle_id"] for o in refined["test_oracles"]],
    }
    refined["schema_version"] = "2.5-refined-draft"
    refined["source_phase2_record_sha256"] = digest(original)
    refined["phase2_5_review"] = {
        "preliminary_disposition": edit["proposed_review_disposition"],
        "review_summary": edit["review_summary"],
        "independent_finding_assessment": "accepted_with_evidence_based_refinement",
        "condition_id_mapping": edit.get("condition_id_mapping", []),
        "change_log": changes,
        "outstanding_questions": edit["outstanding_questions"],
        "semantic_edits_source": "data/phase2_5_semantic_edits.json",
    }
    refined["annotation_status"] = "pending_review"
    refined["final_human_decision"] = None
    refined["review_notes"] = list(refined.get("review_notes", [])) + ["Phase 2.5 machine-proposed refinement; semantic correctness awaits human review."]
    return refined


def evidence_label(record: dict[str, Any], ids: list[str]) -> str:
    by_id = {e["evidence_id"]: e for e in record["evidence"]}
    return ", ".join(f"{eid} ({by_id[eid]['source_file']}:{by_id[eid]['source_record_id']}:{by_id[eid]['source_field']})" for eid in ids)


def review_report(records: list[dict[str, Any]]) -> str:
    change_count = sum(len(r["phase2_5_review"]["change_log"]) for r in records)
    changed_conditions = sum(sum(c["change_type"] in {"condition_refined", "condition_added"} for c in r["phase2_5_review"]["change_log"]) for r in records)
    partial = sum(not r["evaluation_targets"]["complete_path_coverage_applicable"] for r in records)
    ambiguous = sum(any(o["ambiguity_status"] == "ambiguous" for o in r["test_oracles"]) for r in records)
    dispositions = Counter(r["phase2_5_review"]["preliminary_disposition"] for r in records)
    lines = [
        "# Phase 2.5 Gold Reference Review Report", "",
        "This report records machine-proposed, evidence-linked refinements. It is not human approval and does not establish semantic correctness merely because validation passes.", "",
        "## Summary", "",
        f"- Frozen bugs reviewed: **{len(records)}**", f"- Audited changes: **{change_count}**", f"- Conditions changed or added: **{changed_conditions}**",
        f"- Bugs without a complete supported path: **{partial}**", f"- Bugs with at least one ambiguous/incomplete or alternative Oracle: **{ambiguous}**",
        f"- Proposed dispositions: **{dict(sorted(dispositions.items()))}**", f"- Pending human decisions: **{len(records)}**", "",
        "## Cross-cutting decisions", "",
        "- Conditions now distinguish `required_trigger`, `reproduction_setup`, `verification_action`, `uncertain_condition`, and `root_cause_only`.",
        "- Only `required_trigger` conditions with `necessity=supported` enter the Condition Coverage denominator.",
        "- Complete Path Coverage is not applicable when no path is both complete and fully supported.",
        "- Repair-only mechanisms remain evaluation evidence and are never promoted to model-input steps.",
        "- The original Phase-2 JSONL remains unchanged; every before/after semantic edit is embedded in each refined record.", "",
    ]
    for idx, record in enumerate(records, 1):
        review = record["phase2_5_review"]
        lines += [f"## {idx}. {record['bug_id']}", "", f"- Proposed disposition: **{review['preliminary_disposition']}** (not a final decision)", f"- Finding assessment: {review['independent_finding_assessment']}", f"- Summary: {review['review_summary']}", "", "### Source-linked change log", ""]
        for change in review["change_log"]:
            lines += [f"- **{change['change_type']} — {change['target_id']}**", f"  - Original: `{concise(change['original_value'])}`", f"  - Proposed: `{concise(change['proposed_value'])}`", f"  - Evidence: {evidence_label(record, change['evidence_ids']) if change['evidence_ids'] else 'schema/role clarification; no new factual claim'}", f"  - Rationale: {change['rationale']}"]
        lines += ["", "### Outstanding questions", ""]
        lines += [f"- {q}" for q in review["outstanding_questions"]]
        lines += ["", f"Condition Coverage denominator if approved: **{record['evaluation_targets']['condition_coverage_denominator']}** ({', '.join(record['evaluation_targets']['eligible_required_condition_ids'])})", f"Complete Path Coverage applicable: **{record['evaluation_targets']['complete_path_coverage_applicable']}**", "", "---", ""]
    return "\n".join(lines)


def checklist(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Gold Reference Human Review Checklist", "",
        "Every decision below is intentionally blank. Reviewers must inspect the cited evidence and choose **Approve**, **Revise**, or **Insufficient Evidence**. Machine-proposed dispositions are advisory only.", "",
    ]
    for idx, record in enumerate(records, 1):
        review = record["phase2_5_review"]
        lines += [f"## {idx}. {record['bug_id']} — {record['issue_title']}", "", f"Proposed disposition: **{review['preliminary_disposition']}**", ""]
        lines += ["### Conditions", "", "| ID | Role | Necessity | Status/confidence | Description | Evidence |", "| --- | --- | --- | --- | --- | --- |"]
        for c in record["trigger_conditions"]:
            lines.append(f"| {c['condition_id']} | {c['role']} | {c['necessity']} | {c['status']}/{c['confidence']} | {c['description']} | {evidence_label(record, c['evidence_ids'])} |")
        lines += ["", "### Paths and Oracles", ""]
        for p in record["trigger_paths"]:
            lines.append(f"- {p['path_id']}: required={p['required_condition_ids']}; setup={p['setup_condition_ids']}; verification={p['verification_action_ids']}; uncertain={p['uncertain_condition_ids']}; root-cause-only={p['root_cause_condition_ids']}; complete/supported metric eligible={p['complete_path_metric_eligible']}.")
        for o in record["test_oracles"]:
            outcomes = " OR ".join(x["description"] for x in o["acceptable_expected_outcomes"])
            lines.append(f"- {o['oracle_id']} ({o['ambiguity_status']}): expected `{outcomes}`; incorrect `{o['incorrect_result']}`; verify by `{o['verification_method']}`; evidence {evidence_label(record, o['evidence_ids'])}.")
        lines += ["", "### Reviewer checks", "", "- [ ] Evidence excerpts support every required trigger.", "- [ ] Setup details are not incorrectly treated as necessary.", "- [ ] Repair-only information is excluded from trigger steps and future model inputs.", "- [ ] Path ordering/completeness is justified.", "- [ ] Oracle is observable and documented alternatives are preserved.", "- [ ] Missing fixture/timing/environment limitations are acceptable.", ""]
        lines += ["Outstanding questions:"] + [f"- {q}" for q in review["outstanding_questions"]]
        lines += ["", "**Final human decision (Approve / Revise / Insufficient Evidence): ____________________**", "", "Reviewer: ____________________", "", "Review date: ____________________", "", "Decision notes / exclusion reason: ________________________________________________", "", "---", ""]
    return "\n".join(lines)


def main() -> int:
    args = parse_args(); root = args.research_root.resolve()
    draft_path = root / "data" / "gold_references_draft.jsonl"
    edits_path = root / "data" / "phase2_5_semantic_edits.json"
    frozen_path = root / "data" / "selected_bugs.jsonl"
    original = read_jsonl(draft_path); edits = json.loads(edits_path.read_text(encoding="utf-8")); frozen_rows = read_jsonl(frozen_path)
    frozen = {r["stable_bug_id"]: r for r in frozen_rows}
    ids = {r["bug_id"] for r in original}
    if len(original) != 18 or ids != set(edits) or ids != set(frozen):
        raise SystemExit("Phase-2 draft, semantic edits, and frozen sample must contain the same 18 IDs")
    refined = [apply_one(record, edits[record["bug_id"]], frozen[record["bug_id"]]) for record in original]
    output = root / "data" / "gold_references_refined_draft.jsonl"
    output.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in refined), encoding="utf-8")
    (root / "reports" / "phase2_5_review_report.md").write_text(review_report(refined), encoding="utf-8")
    (root / "reports" / "gold_reference_human_review_checklist.md").write_text(checklist(refined), encoding="utf-8")
    print(json.dumps({
        "records": len(refined),
        "changed_or_added_conditions": sum(sum(c["change_type"] in {"condition_refined", "condition_added"} for c in r["phase2_5_review"]["change_log"]) for r in refined),
        "incomplete_supported_paths": sum(not r["evaluation_targets"]["complete_path_coverage_applicable"] for r in refined),
        "ambiguous_oracles": sum(any(o["ambiguity_status"] == "ambiguous" for o in r["test_oracles"]) for r in refined),
        "pending_human_decisions": sum(r["final_human_decision"] is None for r in refined),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
