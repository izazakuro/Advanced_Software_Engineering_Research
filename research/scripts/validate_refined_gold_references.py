#!/usr/bin/env python3
"""Validate Phase-2.5 refined drafts without claiming semantic correctness."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError as exc:
    raise SystemExit(
        "DuckDB is required. From research/: uv run --with duckdb==2.0.0 "
        "python scripts/validate_refined_gold_references.py"
    ) from exc


ROLES = {"required_trigger", "reproduction_setup", "verification_action", "uncertain_condition", "root_cause_only"}
NECESSITIES = {"supported", "not_established", "not_applicable"}
LIST_ROLE_MAP = {
    "required_condition_ids": "required_trigger", "setup_condition_ids": "reproduction_setup",
    "verification_action_ids": "verification_action", "uncertain_condition_ids": "uncertain_condition",
    "root_cause_condition_ids": "root_cause_only",
}
FORBIDDEN = {"experimental_output", "model_response", "generated_test_scenario", "baseline_output", "rag_output", "human_approved"}


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve(); parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=here.parents[1])
    parser.add_argument("--dataset-root", type=Path, default=here.parents[2] / "OSSGameBench")
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def walk_keys(value: Any) -> set[str]:
    out: set[str] = set()
    if isinstance(value, dict):
        out.update(value)
        for child in value.values(): out.update(walk_keys(child))
    elif isinstance(value, list):
        for child in value: out.update(walk_keys(child))
    return out


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def parse_id(record_id: str) -> tuple[str, int]:
    project, number = record_id.rsplit("#", 1); return project, int(number)


def verify_excerpt(con: duckdb.DuckDBPyConnection, dataset: Path, evidence: dict[str, Any], cache: dict[tuple[str, str], Any], errors: list[str]) -> None:
    stype = evidence["source_type"]; rid = evidence["source_record_id"]; field = evidence["source_field"]; quote = evidence["excerpt"]
    label = f"{rid}/{evidence['evidence_id']}"
    if stype in {"issue", "pr"}:
        project, number = parse_id(rid); table = "issue.parquet" if stype == "issue" else "pr.parquet"
        if field not in {"title", "body"}:
            errors.append(f"{label}: invalid {stype} field {field}"); return
        key = (stype, rid)
        if key not in cache:
            cache[key] = con.execute(f"SELECT title,body FROM read_parquet('{sql_path(dataset/table)}') WHERE project=? AND number=?", [project, number]).fetchone()
        row = cache[key]; text = row[0 if field == "title" else 1] if row else None
        if not row or quote not in (text or ""):
            errors.append(f"{label}: excerpt does not resolve exactly to {table}.{field}")
    elif stype == "commit":
        path = dataset / evidence["source_file"]
        if not path.is_file(): errors.append(f"{label}: commit source file missing"); return
        key = ("commit", rid)
        if key not in cache:
            cache[key] = con.execute(f"SELECT message,diff FROM read_parquet('{sql_path(path)}') WHERE split_part(sha,' ',1)=?", [rid]).fetchone()
        row = cache[key]
        if not row: errors.append(f"{label}: commit SHA not found"); return
        valid = quote in (row[0] or "") if field == "message" else any(quote in (d.get("patch") or "") for d in (row[1] or [])) if field.startswith("diff.patch") else False
        if not valid: errors.append(f"{label}: commit excerpt does not resolve")
    elif stype == "comment" and evidence.get("temporally_verified") is not False:
        errors.append(f"{label}: comment evidence must be temporally unverified")


def main() -> int:
    args = parse_args(); root = args.research_root.resolve(); dataset = args.dataset_root.resolve(); errors: list[str] = []
    refined = read_jsonl(root / "data" / "gold_references_refined_draft.jsonl")
    original = read_jsonl(root / "data" / "gold_references_draft.jsonl")
    frozen = read_jsonl(root / "data" / "selected_bugs.jsonl")
    leakage = read_jsonl(root / "data" / "leakage_manifest.jsonl")
    edits = json.loads((root / "data" / "phase2_5_semantic_edits.json").read_text(encoding="utf-8"))
    original_by = {r["bug_id"]: r for r in original}; frozen_ids = {r["stable_bug_id"] for r in frozen}; leak_by = {r["bug_id"]: r for r in leakage}
    ids = [r.get("bug_id") for r in refined]
    if len(refined) != 18 or len(set(ids)) != 18 or set(ids) != frozen_ids or set(ids) != set(edits):
        errors.append("Refined records, semantic edits, and frozen sample must contain the same 18 unique IDs")
    con = duckdb.connect(); con.execute("SET threads=4"); cache: dict[tuple[str, str], Any] = {}

    changed_conditions = 0; partial_records = 0; ambiguous_records = 0
    for record in refined:
        bug = record.get("bug_id", "<missing>")
        if record.get("schema_version") != "2.5-refined-draft": errors.append(f"{bug}: wrong schema_version")
        if record.get("annotation_status") != "pending_review" or record.get("final_human_decision") is not None:
            errors.append(f"{bug}: all decisions must remain pending/null")
        if walk_keys(record) & FORBIDDEN: errors.append(f"{bug}: contains experimental/approval fields")
        if bug in original_by and record.get("source_phase2_record_sha256") != digest(original_by[bug]):
            errors.append(f"{bug}: Phase-2 source hash mismatch")

        evidence = record.get("evidence", []); evidence_ids = [e.get("evidence_id") for e in evidence]; evidence_set = set(evidence_ids)
        if len(evidence_ids) != len(evidence_set) or None in evidence_set: errors.append(f"{bug}: evidence IDs invalid")
        for e in evidence:
            required = {"evidence_id", "source_type", "source_record_id", "source_file", "source_field", "excerpt", "evidence_type", "temporally_verified", "temporal_role"}
            if not required <= set(e) or not e.get("excerpt"): errors.append(f"{bug}/{e.get('evidence_id')}: incomplete provenance"); continue
            if e["source_type"] in {"pr", "commit"} and e["temporal_role"] != "evaluation_only_future": errors.append(f"{bug}/{e['evidence_id']}: repair evidence is not evaluation-only")
            verify_excerpt(con, dataset, e, cache, errors)

        conditions = record.get("trigger_conditions", []); by_condition = {c.get("condition_id"): c for c in conditions}
        if len(by_condition) != len(conditions) or None in by_condition: errors.append(f"{bug}: condition IDs invalid")
        for c in conditions:
            if c.get("role") not in ROLES or c.get("necessity") not in NECESSITIES or not isinstance(c.get("future_repair_only"), bool): errors.append(f"{bug}/{c.get('condition_id')}: invalid role metadata")
            if not c.get("evidence_ids") or not set(c["evidence_ids"]) <= evidence_set: errors.append(f"{bug}/{c.get('condition_id')}: unresolved evidence")
            if c["future_repair_only"] and c["role"] != "root_cause_only": errors.append(f"{bug}/{c['condition_id']}: future repair-only condition must be root_cause_only")
            if c["role"] == "root_cause_only" and c["necessity"] != "not_applicable": errors.append(f"{bug}/{c['condition_id']}: root cause cannot be a trigger necessity")

        paths = record.get("trigger_paths", []); by_path = {p.get("path_id"): p for p in paths}
        if len(by_path) != len(paths) or None in by_path: errors.append(f"{bug}: path IDs invalid")
        all_required: set[str] = set(); complete_ids: set[str] = set()
        for p in paths:
            listed: list[str] = []
            for field, expected_role in LIST_ROLE_MAP.items():
                refs = p.get(field)
                if not isinstance(refs, list) or not set(refs) <= set(by_condition): errors.append(f"{bug}/{p.get('path_id')}: invalid {field}"); continue
                listed += refs
                for cid in refs:
                    if by_condition[cid]["role"] != expected_role: errors.append(f"{bug}/{p['path_id']}/{cid}: role does not match {field}")
            if len(listed) != len(set(listed)): errors.append(f"{bug}/{p['path_id']}: a condition occupies multiple path roles")
            if not set(p.get("ordered_condition_ids", [])) <= set(by_condition): errors.append(f"{bug}/{p['path_id']}: invalid ordering references")
            if p.get("fully_supported") and (p.get("relationship_status") != "supported" or p.get("completeness") != "complete"): errors.append(f"{bug}/{p['path_id']}: fully_supported contradicts status")
            expected_metric = bool(p.get("fully_supported") and p.get("completeness") == "complete")
            if p.get("complete_path_metric_eligible") != expected_metric: errors.append(f"{bug}/{p['path_id']}: metric eligibility mismatch")
            for cid in p.get("required_condition_ids", []):
                c = by_condition[cid]
                if c["necessity"] != "supported": errors.append(f"{bug}/{p['path_id']}/{cid}: required trigger necessity is not supported")
                all_required.add(cid)
            if expected_metric: complete_ids.add(p["path_id"])

        target = record.get("evaluation_targets", {})
        if target.get("eligible_required_condition_ids") != sorted(all_required) or target.get("condition_coverage_denominator") != len(all_required): errors.append(f"{bug}: Condition Coverage denominator mismatch")
        if target.get("complete_supported_path_ids") != sorted(complete_ids) or target.get("complete_path_coverage_applicable") != bool(complete_ids): errors.append(f"{bug}: Complete Path Coverage target mismatch")
        if not complete_ids: partial_records += 1

        oracles = record.get("test_oracles", []); oracle_ids = {o.get("oracle_id") for o in oracles}
        if len(oracle_ids) != len(oracles) or None in oracle_ids: errors.append(f"{bug}: Oracle IDs invalid")
        if any(o.get("ambiguity_status") == "ambiguous" for o in oracles): ambiguous_records += 1
        for o in oracles:
            if o.get("trigger_path_id") not in by_path: errors.append(f"{bug}/{o.get('oracle_id')}: invalid path")
            if not o.get("evidence_ids") or not set(o["evidence_ids"]) <= evidence_set: errors.append(f"{bug}/{o.get('oracle_id')}: unresolved Oracle evidence")
            outcomes = o.get("acceptable_expected_outcomes", [])
            if not outcomes: errors.append(f"{bug}/{o.get('oracle_id')}: no acceptable outcome")
            for outcome in outcomes:
                if not outcome.get("description") or not outcome.get("evidence_ids") or not set(outcome["evidence_ids"]) <= evidence_set: errors.append(f"{bug}/{o.get('oracle_id')}: invalid acceptable outcome")

        review = record.get("phase2_5_review", {}); log = review.get("change_log", [])
        if review.get("preliminary_disposition") not in {"Approve", "Revise", "Insufficient Evidence"}: errors.append(f"{bug}: invalid preliminary disposition")
        if not review.get("outstanding_questions"): errors.append(f"{bug}: no outstanding human question")
        for change in log:
            if change.get("review_status") != "machine_proposed_pending_human_decision": errors.append(f"{bug}: change log falsely finalized")
            if not set(change.get("evidence_ids", [])) <= evidence_set: errors.append(f"{bug}: change log evidence unresolved")
            if change.get("change_type") in {"condition_refined", "condition_added"}: changed_conditions += 1

        leak = leak_by.get(bug, {}); repair = record.get("repair_evidence_summary", {})
        if leak.get("retrieval_cutoff_timestamp") != record.get("issue_created_at"): errors.append(f"{bug}: cutoff mismatch")
        if repair.get("fixing_pr_id") not in leak.get("restricted_fixing_prs", []): errors.append(f"{bug}: fixing PR leakage restriction missing")
        if set(repair.get("repair_commit_shas", [])) != set(leak.get("restricted_repair_commits", [])): errors.append(f"{bug}: repair Commit restrictions incomplete")

    checklist = (root / "reports" / "gold_reference_human_review_checklist.md").read_text(encoding="utf-8")
    if checklist.count("Final human decision (Approve / Revise / Insufficient Evidence):") != 18: errors.append("Human checklist does not contain 18 blank decision gates")
    rubric = (root / "reports" / "test_scenario_evaluation_rubric.md").read_text(encoding="utf-8")
    for required in ("Condition Coverage", "Complete Path Coverage", "Oracle Correctness", "Unsupported Assumptions", "Blind paired scoring", "adjudication"):
        if required.casefold() not in rubric.casefold(): errors.append(f"Evaluation rubric missing {required}")

    phase3_markers = ("bm25", "rag_index", "retrieval_index", "experiment_output", "llm_output", "phase3")
    phase3_artifacts = [
        path for path in root.rglob("*")
        if path.is_file() and any(marker in path.name.casefold() for marker in phase3_markers)
    ]
    if phase3_artifacts:
        errors.append("Unexpected Phase-3/RAG/experiment artifacts: " + ", ".join(str(p.relative_to(root)) for p in phase3_artifacts))

    if errors:
        print("Refined Gold Reference validation FAILED:", file=sys.stderr)
        for error in errors: print(f"- {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "PASS_STRUCTURAL_AND_PROVENANCE_ONLY", "records": len(refined),
        "changed_or_added_conditions": changed_conditions, "bugs_without_complete_supported_path": partial_records,
        "bugs_with_ambiguous_oracles": ambiguous_records, "pending_human_decisions": sum(r["final_human_decision"] is None for r in refined),
        "semantic_correctness_claimed": False, "phase3_artifacts_present": bool(phase3_artifacts),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
