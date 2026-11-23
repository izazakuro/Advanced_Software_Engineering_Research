#!/usr/bin/env python3
"""Build Phase-2.7 human-adjudication packet, template, and cohort plan."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def citation(e: dict[str, Any]) -> str:
    text = e["excerpt"].replace("\r", " ").replace("\n", " ")
    if len(text) > 280: text = text[:277] + "…"
    return f"{e['evidence_id']} — `{e['source_file']} / {e['source_record_id']} / {e['source_field']}` ({e['temporal_role']}): “{text}”"


def source_status(condition: dict[str, Any]) -> str:
    if condition.get("future_repair_only"): return "repair-only evaluation evidence"
    if condition.get("status") == "explicit": return "source-verified statement"
    if condition.get("status") == "inferred": return "evidence-supported interpretation"
    return "uncertain"


def packet(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Phase 2.7 Human Adjudication Packet", "",
        "This packet contains machine-prepared evidence and questions only. Every human decision, reviewer identity, date, rationale, and exploratory-inclusion choice remains blank in the companion JSONL template.", "",
        "Evidence marked `target_snapshot` is exact in the local Parquet snapshot but its historical text-at-creation state is unverified because edit history is absent. PR/Commit evidence is evaluation-only future repair information.", "",
    ]
    for i, r in enumerate(records, 1):
        conditions={c["condition_id"]:c for c in r["trigger_conditions"]}; ev={e["evidence_id"]:e for e in r["evidence"]}
        required=sorted({cid for p in r["trigger_paths"] for cid in p["required_condition_ids"]})
        setup=sorted({cid for p in r["trigger_paths"] for cid in p["scenario_adequacy"]["material_setup_condition_ids"]})
        observed_setup=sorted({cid for p in r["trigger_paths"] for cid in p["scenario_adequacy"]["observed_but_not_universally_required_setup_ids"]})
        uncertain=sorted({cid for p in r["trigger_paths"] for cid in p["uncertain_condition_ids"]})
        root_only=sorted({cid for p in r["trigger_paths"] for cid in p["root_cause_condition_ids"]})
        relevant_eids=sorted({eid for cid in required+setup+observed_setup+uncertain+root_only for eid in conditions[cid]["evidence_ids"]} | {eid for o in r["test_oracles"] for eid in o["evidence_ids"]} | {eid for o in r["preventive_oracles"] for eid in o["evidence_ids"]})
        lines += [f"## {i}. {r['bug_id']} — {r['issue_title']}", "", f"- Machine suggestion: **{r['suggested_review_decision']}** (advisory)", f"- Candidate cohort: **{'complete-path' if r['evaluation_targets_v2']['cspc_eligible_path_ids'] else 'partial/exploratory only'}**", f"- Eligible paths: `{r['evaluation_targets_v2']['cspc_eligible_path_ids'] or []}`", f"- Path grouping: `{r['path_groups']}`", ""]
        lines += ["### Conditions", "", "| ID | Role / necessity | Verification status | Description | Evidence |", "| --- | --- | --- | --- | --- |"]
        for cid in required+setup+observed_setup+uncertain+root_only:
            c=conditions[cid]
            lines.append(f"| {cid} | {c['role']} / {c['necessity']} | {source_status(c)} | {c['description']} | {', '.join(c['evidence_ids'])} |")
        lines += ["", "### Scenario adequacy and paths", ""]
        for p in r["trigger_paths"]:
            lines.append(f"- **{p['path_id']}**: required={p['required_condition_ids']}; material setup={p['scenario_adequacy']['material_setup_condition_ids']}; observed setup={p['scenario_adequacy']['observed_but_not_universally_required_setup_ids']}; verification={p['verification_action_ids']}; uncertain={p['uncertain_condition_ids']}; root-cause-only={p['root_cause_condition_ids']}; CSPC={p['cspc_eligibility']}.")
        lines += ["", "### Applicable Oracle branches", ""]
        for branch in r["outcome_branches"]:
            lines.append(f"- `{branch}`")
        for o in r["test_oracles"]:
            lines.append(f"- **{o['oracle_id']}**: {o['expected_result']} Incorrect: {o['incorrect_result']} Status={o['scoring_status']}; evidence={o['evidence_ids']}.")
        for o in r["preventive_oracles"]:
            lines.append(f"- **{o['oracle_id']} preventive-only**: {o['expected_result']} Applicable before {o['applicable_before_condition_id']}; evidence={o['evidence_ids']}.")
        lines += ["", "### Evidence citations", ""]
        lines += [f"- {citation(ev[eid])}" for eid in relevant_eids]
        lines += ["", "### Unavailable / inferred / unresolved", ""]
        for limitation in r["known_limitations"]: lines.append(f"- {limitation}")
        for question in r["phase2_6_review"]["unresolved_human_questions"]: lines.append(f"- **Human question:** {question}")
        lines += ["", "### Decision fields", "", "- Human decision: ____________________", "- Reviewer: ____________________", "- Reviewed at: ____________________", "- Rationale: ____________________", f"- Exploratory include: {'N/A (complete-path candidate)' if r['evaluation_targets_v2']['cspc_eligible_path_ids'] else 'Yes / No / undecided'}", "", "---", ""]
    return "\n".join(lines)


def cohort_plan(records: list[dict[str, Any]]) -> str:
    complete=[r for r in records if r["evaluation_targets_v2"]["cspc_eligible_path_ids"]]; partial=[r for r in records if not r["evaluation_targets_v2"]["cspc_eligible_path_ids"]]
    incomplete=[r for r in records if any(o["completeness"]=="incomplete" for o in r["test_oracles"])]
    lines=["# Phase 2.7 Cohort Plan", "", "Status: proposed and pending human approval. Final inclusion depends on itemized decisions; no machine suggestion includes a bug automatically.", "", f"Summary: {len(complete)} complete-path candidates and {len(partial)} partial/exploratory candidates.", "", f"## Complete-path candidate cohort ({len(complete)})", ""]
    lines += [f"- `{r['bug_id']}` — machine suggestion {r['suggested_review_decision']}; eligible paths {r['evaluation_targets_v2']['cspc_eligible_path_ids']}" for r in complete]
    lines += ["", f"## Partial/exploratory candidate cohort ({len(partial)})", ""]
    lines += [f"- `{r['bug_id']}` — CSPC is NA; human must separately choose exploratory_include Yes/No." for r in partial]
    lines += ["", f"## Incomplete-Oracle records ({len(incomplete)})", ""]
    lines += [f"- `{r['bug_id']}` — incomplete Oracle does not automatically exclude TCC/SA; Oracle may be partial or `not_scorable` after human review." for r in incomplete]
    lines += ["", "## Inclusion and reporting rules", "", "1. Confirmatory complete-path cohort includes only human-approved records with at least one approved CSPC-eligible path.", "2. Partial records can enter only a separately labeled exploratory TCC/SA cohort after explicit `exploratory_include=true`; CSPC remains NA.", "3. `Insufficient Evidence` records remain identified and excluded with reasons; no substitutions are permitted.", "4. Incomplete Oracle status affects Oracle scoring, not automatically TCC eligibility. Report scoreable counts by method arm.", "5. Report per-bug macro TCC, cohort denominators, paired effects, and project clustering/sensitivity where feasible.", "6. Human decisions and rubric choices must be complete before freeze or official output generation.", ""]
    return "\n".join(lines)


def main() -> None:
    records=read_jsonl(ROOT/"data"/"gold_references_phase2_6_candidate.jsonl")
    template=[]
    for r in records:
        complete=bool(r["evaluation_targets_v2"]["cspc_eligible_path_ids"])
        template.append({
            "bug_id":r["bug_id"], "machine_suggestion":r["suggested_review_decision"],
            "human_decision":None, "reviewer":None, "reviewed_at":None, "rationale":None,
            "exploratory_include":"N/A" if complete else None, "evidence_checked":None,
            "unresolved_questions":r["phase2_6_review"]["unresolved_human_questions"],
        })
    (ROOT/"reports"/"phase2_7_adjudication_packet.md").write_text(packet(records),encoding="utf-8")
    (ROOT/"data"/"phase2_7_review_decisions_TEMPLATE.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False,sort_keys=True)+"\n" for x in template),encoding="utf-8")
    (ROOT/"reports"/"phase2_7_cohort_plan.md").write_text(cohort_plan(records),encoding="utf-8")
    print(json.dumps({"records":len(records),"complete":sum(bool(r['evaluation_targets_v2']['cspc_eligible_path_ids']) for r in records),"partial":sum(not r['evaluation_targets_v2']['cspc_eligible_path_ids'] for r in records),"human_decisions_populated":sum(x['human_decision'] is not None for x in template)},indent=2))


if __name__=="__main__": main()
