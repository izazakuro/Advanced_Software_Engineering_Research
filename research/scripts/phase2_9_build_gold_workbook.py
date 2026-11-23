#!/usr/bin/env python3
"""Build the Phase 2.9 ABC Gold adjudication workbook without decisions."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    records = [json.loads(x) for x in (ROOT / "data/gold_references_phase2_6_candidate.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    priority = {"CorsixTH/CorsixTH#1799", "flame-engine/flame#1264", "vcmi/vcmi#5462", "wolvenkit/wolvenkit#2279"}
    lines = ["# Phase 2.9 ABC Gold Adjudication Workbook", "",
             "Status: **REVIEW_REQUIRED**. Machine recommendations are advisory. Human decision, reviewer, date, rationale, evidence sign-off, and exploratory inclusion remain blank. Repair-only evidence must not become a model-visible trigger.", ""]
    for index, r in enumerate(records, 1):
        conditions = {x["condition_id"]: x for x in r["trigger_conditions"]}
        complete = bool(r["evaluation_targets_v2"]["cspc_eligible_path_ids"])
        lines += [f"## {index}. {r['bug_id']} — {r['issue_title']}", "",
                  f"- Priority review: **{'Yes' if r['bug_id'] in priority else 'No'}**",
                  f"- Machine recommendation: **{r['suggested_review_decision']}** (not approval)",
                  f"- Cohort candidate: **{'complete-path' if complete else 'partial/exploratory'}**",
                  f"- CSPC eligibility: `{r['evaluation_targets_v2']['cspc_eligible_path_ids'] or 'NA pending separately approved revision'}`", "",
                  "### Supported triggers, setup, and paths", ""]
        for p in r["trigger_paths"]:
            required = [f"{cid}: {conditions[cid]['description']} [{', '.join(conditions[cid]['evidence_ids'])}]" for cid in p["required_condition_ids"]]
            material = [f"{cid}: {conditions[cid]['description']} [{', '.join(conditions[cid]['evidence_ids'])}]" for cid in p["scenario_adequacy"]["material_setup_condition_ids"]]
            observed = [f"{cid}: {conditions[cid]['description']} [{', '.join(conditions[cid]['evidence_ids'])}]" for cid in p["scenario_adequacy"]["observed_but_not_universally_required_setup_ids"]]
            lines += [f"- `{p['path_id']}` required: {'; '.join(required) or 'none'}",
                      f"- `{p['path_id']}` material setup: {'; '.join(material) or 'none'}",
                      f"- `{p['path_id']}` observed/not-universally-required setup: {'; '.join(observed) or 'none'}",
                      f"- `{p['path_id']}` TCC denominator={p['tcc_denominator']}; CSPC={p['cspc_eligibility']}"]
        lines += [f"- Path group: `{r['path_groups']}`", "", "### Oracle evidence", ""]
        for o in r["test_oracles"]:
            lines.append(f"- `{o['oracle_id']}` ({o['completeness']}; {o['scoring_status']}): expected={o['expected_result']} Incorrect={o['incorrect_result']} Evidence={o['evidence_ids']}.")
        for o in r["preventive_oracles"]:
            lines.append(f"- `{o['oracle_id']}` preventive before `{o['applicable_before_condition_id']}`: expected={o['expected_result']} Evidence={o['evidence_ids']}; never credit as post-trigger completion.")
        lines += ["", "### Ambiguities and review questions", ""]
        lines += [f"- {x}" for x in r["known_limitations"]]
        lines += [f"- **Decision question:** {x}" for x in r["phase2_6_review"]["unresolved_human_questions"]]
        lines += ["- Target Issue text is the current dataset snapshot; creation-time equivalence is unknown.", "",
                  "### Human fields", "", "- Human decision (Approve / Revise / Insufficient Evidence): ______",
                  "- Reviewer: ______", "- Reviewed at: ______", "- Evidence checked: ______", "- Rationale/sign-off: ______",
                  f"- Exploratory include: {'N/A' if complete else '______ (Yes / No)'}", "", "---", ""]
    (ROOT / "reports/phase2_9_abc_gold_adjudication_workbook.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"records": len(records), "complete": sum(bool(x["evaluation_targets_v2"]["cspc_eligible_path_ids"]) for x in records),
                      "partial": sum(not bool(x["evaluation_targets_v2"]["cspc_eligible_path_ids"]) for x in records),
                      "human_decisions_populated": 0}, indent=2))


if __name__ == "__main__":
    main()
