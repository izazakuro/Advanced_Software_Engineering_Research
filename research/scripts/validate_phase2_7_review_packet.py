#!/usr/bin/env python3
"""Read-only structural validator for the Phase 2.7 review materials."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> int:
    candidate = load_jsonl(ROOT / "data/gold_references_phase2_6_candidate.jsonl")
    decisions = load_jsonl(ROOT / "data/phase2_7_review_decisions_TEMPLATE.jsonl")
    packet = (ROOT / "reports/phase2_7_adjudication_packet.md").read_text(encoding="utf-8")
    cohort = (ROOT / "reports/phase2_7_cohort_plan.md").read_text(encoding="utf-8")
    errors: list[str] = []
    ids = [r["bug_id"] for r in candidate]
    decision_ids = [r.get("bug_id") for r in decisions]
    required = {
        "bug_id", "machine_suggestion", "human_decision", "reviewer", "reviewed_at",
        "rationale", "exploratory_include", "evidence_checked", "unresolved_questions",
    }
    if len(candidate) != 18 or len(decisions) != 18:
        errors.append(f"expected 18 candidate/template rows, got {len(candidate)}/{len(decisions)}")
    if decision_ids != ids:
        errors.append("template IDs/order differ from Phase 2.6 candidate")
    if len(set(decision_ids)) != len(decision_ids):
        errors.append("duplicate template bug IDs")
    complete = 0
    partial = 0
    for source, decision in zip(candidate, decisions):
        missing = required - set(decision)
        if missing:
            errors.append(f"{source['bug_id']}: missing fields {sorted(missing)}")
        for field in ("human_decision", "reviewer", "reviewed_at", "rationale", "evidence_checked"):
            if decision.get(field) is not None:
                errors.append(f"{source['bug_id']}: invented/populated {field}")
        if decision.get("machine_suggestion") != source["suggested_review_decision"]:
            errors.append(f"{source['bug_id']}: machine suggestion mismatch")
        is_complete = bool(source["evaluation_targets_v2"]["cspc_eligible_path_ids"])
        if is_complete:
            complete += 1
            if decision.get("exploratory_include") != "N/A":
                errors.append(f"{source['bug_id']}: complete candidate must use exploratory_include=N/A")
        else:
            partial += 1
            if decision.get("exploratory_include") is not None:
                errors.append(f"{source['bug_id']}: exploratory choice must remain blank")
        if decision.get("unresolved_questions") != source["phase2_6_review"]["unresolved_human_questions"]:
            errors.append(f"{source['bug_id']}: unresolved questions mismatch")
        heading = f"{source['bug_id']} — {source['issue_title']}"
        if packet.count(heading) != 1:
            errors.append(f"{source['bug_id']}: adjudication section count is not one")
    if (complete, partial) != (11, 7):
        errors.append(f"expected cohort counts 11/7, got {complete}/{partial}")
    forbidden = ("human_decision\": \"Approve", "Human decision: Approve", "Status: approved", "Gold References frozen")
    combined = packet + cohort + (ROOT / "data/phase2_7_review_decisions_TEMPLATE.jsonl").read_text(encoding="utf-8")
    for phrase in forbidden:
        if phrase in combined:
            errors.append(f"possible invented approval/freeze marker: {phrase!r}")
    for phrase in ("11 complete-path", "7 partial/exploratory", "CSPC remains NA", "pending human approval"):
        if phrase not in cohort:
            errors.append(f"cohort plan missing required statement: {phrase!r}")
    result = {"status": "PASS" if not errors else "FAIL", "records": len(decisions),
              "complete_path_candidates": complete, "partial_candidates": partial,
              "populated_human_decisions": sum(r.get("human_decision") is not None for r in decisions),
              "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
