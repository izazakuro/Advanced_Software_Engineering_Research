#!/usr/bin/env python3
"""Validate Phase-2.6 consistency/provenance; never imply semantic approval."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError as exc:
    raise SystemExit("Run with DuckDB, e.g. uv run --with duckdb==2.0.0 python scripts/validate_phase2_6_candidate.py") from exc

from validate_refined_gold_references import verify_excerpt


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"
INCOMPLETE_IDS = {"CorsixTH/CorsixTH#2881", "colobot/colobot#1787", "freeorion/freeorion#1897", "freeorion/freeorion#2365", "gemrb/gemrb#1878", "warzone2100/warzone2100#4125", "wolvenkit/wolvenkit#2279"}
MISMATCH_IDS = {"CorsixTH/CorsixTH#1799", "flame-engine/flame#1264", "vcmi/vcmi#5462"}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    errors: list[str] = []
    candidate = read_jsonl(ROOT/"data"/"gold_references_phase2_6_candidate.jsonl")
    source = read_jsonl(ROOT/"data"/"gold_references_refined_draft.jsonl")
    frozen = read_jsonl(ROOT/"data"/"selected_bugs.jsonl")
    leakage = {r["bug_id"]:r for r in read_jsonl(ROOT/"data"/"leakage_manifest.jsonl")}
    log = json.loads((ROOT/"data"/"phase2_6_change_log.json").read_text())
    by_source={r["bug_id"]:r for r in source}; frozen_ids={r["stable_bug_id"] for r in frozen}; ids=[r.get("bug_id") for r in candidate]
    if len(candidate)!=18 or len(set(ids))!=18 or set(ids)!=frozen_ids: errors.append("Candidate must preserve exactly the frozen 18 IDs")
    if set(log.get("per_bug_changes",{}))!=set(ids): errors.append("Change log does not cover all candidate IDs")
    if {x["bug_id"] for x in log.get("discrepancy_resolutions",[])}!=MISMATCH_IDS: errors.append("Change log does not contain exactly the three verified discrepancies")
    expected_hashes={"gold_references_refined_draft.jsonl":file_sha(ROOT/"data"/"gold_references_refined_draft.jsonl"),"phase2_5_semantic_edits.json":file_sha(ROOT/"data"/"phase2_5_semantic_edits.json"),"phase2_5_independent_audit_report.md":file_sha(ROOT/"phase2_5_independent_audit_report.md")}
    if log.get("source_files")!=expected_hashes: errors.append("Change-log source hashes do not match actual inputs")

    con=duckdb.connect(); con.execute("SET threads=4"); cache:dict[tuple[str,str],Any]={}
    suggestion_counts=Counter(); incomplete_ids=set(); alternative_post=set(); preventive=set(); complete=0; partial=0
    for r in candidate:
        bug=r["bug_id"]; src=by_source.get(bug)
        if not src or r.get("source_phase2_5_record_sha256")!=digest(src): errors.append(f"{bug}: source Phase-2.5 hash mismatch")
        if r.get("schema_version")!="2.6-audit-remediation-candidate": errors.append(f"{bug}: wrong schema version")
        if r.get("annotation_status")!="pending_review" or r.get("final_human_decision") is not None: errors.append(f"{bug}: decision gate is not blank/pending")
        proposed=r.get("phase2_5_review",{}).get("preliminary_disposition")
        if r.get("suggested_review_decision")!=proposed or r.get("phase2_6_machine_suggestion",{}).get("value")!=proposed: errors.append(f"{bug}: machine suggestion is inconsistent")
        if r.get("phase2_6_machine_suggestion",{}).get("is_human_decision") is not False: errors.append(f"{bug}: machine suggestion misrepresented as human")
        suggestion_counts[r["suggested_review_decision"]]+=1

        evidence=r.get("evidence",[]); ev_ids=[e.get("evidence_id") for e in evidence]; ev_set=set(ev_ids)
        if len(ev_ids)!=len(ev_set): errors.append(f"{bug}: duplicate evidence IDs")
        for e in evidence: verify_excerpt(con,DATASET,e,cache,errors)
        conditions={c["condition_id"]:c for c in r["trigger_conditions"]}; paths={p["path_id"]:p for p in r["trigger_paths"]}
        for c in conditions.values():
            if c.get("future_repair_only") and (c.get("role")!="root_cause_only" or c.get("necessity")!="not_applicable"): errors.append(f"{bug}/{c['condition_id']}: repair-only role violation")
        all_group_paths=[]
        groups=r.get("path_groups",[])
        if len(groups)!=1: errors.append(f"{bug}: expected one explicit path group")
        for g in groups:
            if g.get("operator") not in {"SINGLE","OR"} or not set(g.get("path_ids",[]))<=set(paths): errors.append(f"{bug}: invalid path group")
            all_group_paths+=g.get("path_ids",[])
        if sorted(all_group_paths)!=sorted(paths): errors.append(f"{bug}: paths are not covered exactly once by groups")
        if bug=="rptools/maptool#2416":
            if groups[0].get("operator")!="OR" or set(groups[0].get("path_ids",[]))!={"P1","P2"}: errors.append("MapTool #2416: OR grouping missing")
            if paths["P1"]["required_condition_ids"]!=["T2A"] or paths["P2"]["required_condition_ids"]!=["T2B"]: errors.append("MapTool #2416: alternative denominators are wrong")
        elif groups and groups[0].get("operator")!="SINGLE": errors.append(f"{bug}: unexpected OR path group")

        for p in paths.values():
            for field in ("required_condition_ids","setup_condition_ids","verification_action_ids","uncertain_condition_ids","root_cause_condition_ids"):
                if not set(p.get(field,[]))<=set(conditions): errors.append(f"{bug}/{p['path_id']}: invalid {field}")
            if any(conditions[cid]["future_repair_only"] for cid in p["required_condition_ids"]+p["setup_condition_ids"]+p["verification_action_ids"]+p["uncertain_condition_ids"]): errors.append(f"{bug}/{p['path_id']}: repair-only fact leaked into user-visible path")
            sa=p.get("scenario_adequacy",{})
            material=[cid for cid in p["setup_condition_ids"] if conditions[cid]["necessity"]=="supported"]
            observed=[cid for cid in p["setup_condition_ids"] if conditions[cid]["necessity"]=="not_established"]
            if sa.get("material_setup_condition_ids")!=material or sa.get("observed_but_not_universally_required_setup_ids")!=observed: errors.append(f"{bug}/{p['path_id']}: Scenario Adequacy setup mismatch")
            if p.get("tcc_denominator")!=len(p["required_condition_ids"]): errors.append(f"{bug}/{p['path_id']}: TCC denominator mismatch")
        cspc=[p["path_id"] for p in paths.values() if p["complete_path_metric_eligible"]]
        if r.get("evaluation_targets_v2",{}).get("cspc_eligible_path_ids")!=cspc: errors.append(f"{bug}: CSPC targets mismatch")
        complete+=bool(cspc); partial+=not bool(cspc)

        for o in r["test_oracles"]:
            if o.get("oracle_completeness_flag") != ("incomplete" if o["completeness"]=="incomplete" else "complete"): errors.append(f"{bug}/{o['oracle_id']}: completeness category mismatch")
            alt=len(o.get("acceptable_expected_outcomes",[]))>1
            if o.get("has_alternative_outcomes")!=alt: errors.append(f"{bug}/{o['oracle_id']}: alternative flag mismatch")
            if o["completeness"]=="incomplete": incomplete_ids.add(bug)
            if alt: alternative_post.add(bug)
            if not set(o["evidence_ids"])<=ev_set: errors.append(f"{bug}/{o['oracle_id']}: unresolved Oracle evidence")
        if r.get("preventive_oracles"): preventive.add(bug)
        summary=r.get("oracle_category_summary",{})
        if summary.get("incomplete_oracle_count")!=sum(o["completeness"]=="incomplete" for o in r["test_oracles"]): errors.append(f"{bug}: Oracle incomplete summary mismatch")
        if summary.get("multi_outcome_post_trigger_oracle_count")!=sum(o["has_alternative_outcomes"] for o in r["test_oracles"]): errors.append(f"{bug}: Oracle alternatives summary mismatch")

        if bug=="CorsixTH/CorsixTH#1799":
            o=r["test_oracles"][0]
            if "E8" in o["evidence_ids"] or any("does not occur" in x["description"] for x in o["acceptable_expected_outcomes"]): errors.append("CorsixTH #1799: prevention remains attached to post-trigger Oracle")
            if len(r["preventive_oracles"])!=1 or r["preventive_oracles"][0].get("applicable_before_condition_id")!="T1A": errors.append("CorsixTH #1799: separate preventive branch missing")
            branches={b["branch_type"] for b in r["outcome_branches"]}
            if branches!={"post_trigger","preventive_pre_trigger"}: errors.append("CorsixTH #1799: outcome branches invalid")

        if bug=="flame-engine/flame#1264" and "T3" not in paths["P1"]["verification_action_ids"]: errors.append("Flame #1264: Tiled comparison is not verification-only")
        if bug=="vcmi/vcmi#5462" and (conditions["T1"]["necessity"]!="not_established" or "T1" not in paths["P1"]["scenario_adequacy"]["observed_but_not_universally_required_setup_ids"]): errors.append("VCMI #5462: macOS setup necessity overstated")
        if bug=="gameservermanagers/linuxgsm#4059" and "T2" not in paths["P1"]["scenario_adequacy"]["material_setup_condition_ids"]: errors.append("LinuxGSM #4059: HLDS setup absent from SA")
        if bug=="flame-engine/flame#1003" and "T1" not in paths["P1"]["scenario_adequacy"]["material_setup_condition_ids"]: errors.append("Flame #1003: GameWidget setup absent from SA")
        if bug=="lichess-org/lila#12907" and "T1" not in paths["P1"]["scenario_adequacy"]["material_setup_condition_ids"]: errors.append("Lichess #12907: in-progress game setup absent from SA")
        if bug=="wolvenkit/wolvenkit#2279" and (not cspc or not any(o["completeness"]=="incomplete" for o in r["test_oracles"])): errors.append("WolvenKit #2279: independent path/Oracle flags lost")

        leak=leakage.get(bug,{}); repair=r["repair_evidence_summary"]
        if repair["fixing_pr_id"] not in leak.get("restricted_fixing_prs",[]) or set(repair["repair_commit_shas"])!=set(leak.get("restricted_repair_commits",[])): errors.append(f"{bug}: leakage restrictions incomplete")

    if suggestion_counts!=Counter({"Revise":11,"Approve":7}): errors.append(f"Candidate suggestion counts wrong: {dict(suggestion_counts)}")
    if incomplete_ids!=INCOMPLETE_IDS: errors.append(f"Incomplete Oracle bug set mismatch: {sorted(incomplete_ids)}")
    if alternative_post: errors.append(f"Post-trigger alternatives should be split, found: {sorted(alternative_post)}")
    if preventive!={"CorsixTH/CorsixTH#1799"}: errors.append(f"Preventive branch set mismatch: {sorted(preventive)}")
    if (complete,partial)!=(11,7): errors.append(f"Complete/partial counts wrong: {(complete,partial)}")
    if log.get("verified_counts",{}).get("incomplete_oracle_bugs")!=7 or log.get("verified_counts",{}).get("preventive_branch_bugs")!=1: errors.append("Change-log Oracle counts wrong")
    if log.get("human_decisions_recorded")!=0 or log.get("semantic_certification_claimed") is not False: errors.append("Change log falsely claims decisions/certification")

    rubric=(ROOT/"reports"/"test_scenario_evaluation_rubric_v2_draft.md").read_text()
    for phrase in ("Trigger Condition Coverage", "Scenario Adequacy", "Complete Supported Path Coverage", "not_scorable", "per-bug TCC", "MapTool #2416", "CorsixTH #1799", "Synthetic rubric examples", "Pre-registration decision log"):
        if phrase.casefold() not in rubric.casefold(): errors.append(f"Rubric missing: {phrase}")
    if rubric.count("### Example ")<6: errors.append("Rubric must contain at least six synthetic examples")
    checklist=(ROOT/"reports"/"phase2_6_human_review_checklist.md").read_text()
    if checklist.count("Final decision (Approve / Revise / Insufficient Evidence):")!=18: errors.append("Checklist does not contain 18 blank decisions")
    for forbidden in (ROOT/"data"/"gold_references_frozen.jsonl",ROOT/"data"/"frozen_sample_manifest.json",ROOT/"data"/"freeze_manifest.sha256",ROOT/"data"/"human_review_decisions.jsonl"):
        if forbidden.exists(): errors.append(f"Forbidden freeze/decision artifact exists: {forbidden.name}")

    if errors:
        print("Phase 2.6 validation FAILED:",file=sys.stderr)
        for e in errors: print("- "+e,file=sys.stderr)
        return 1
    print(json.dumps({"status":"PASS_STRUCTURAL_CONSISTENCY_AND_PROVENANCE_ONLY","records":18,"suggestions":{"Approve":7,"Revise":11},"discrepancies_resolved":3,"complete_path_bugs":11,"partial_path_bugs":7,"incomplete_oracle_bugs":7,"multi_outcome_post_trigger_bugs":0,"separate_preventive_branch_bugs":1,"pending_human_decisions":18,"semantic_correctness_claimed":False,"frozen_outputs_created":False},indent=2))
    return 0


if __name__=="__main__": sys.exit(main())
