#!/usr/bin/env python3
"""Validate Phase-2 Gold Reference drafts, provenance, and leakage records."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError as exc:
    raise SystemExit(
        "DuckDB is required. From research/: uv run --with duckdb==2.0.0 "
        "python scripts/validate_gold_references.py"
    ) from exc


FACT_FIELDS = ("bug_category", "secondary_category", "affected_component", "observed_behavior", "expected_behavior")
FORBIDDEN_KEYS = {"experimental_output", "model_response", "generated_test_scenario", "baseline_output", "rag_output", "human_approved"}
ALLOWED_SOURCES = {"issue", "pr", "commit", "comment"}
ALLOWED_STATUS = {"explicit", "inferred", "uncertain"}
ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=here.parents[1])
    parser.add_argument("--dataset-root", type=Path, default=here.parents[2] / "OSSGameBench")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read valid JSONL from {path}: {exc}") from exc


def walk_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(value)
        for child in value.values():
            keys.update(walk_keys(child))
    elif isinstance(value, list):
        for child in value:
            keys.update(walk_keys(child))
    return keys


def parse_record_id(record_id: str) -> tuple[str, int]:
    project, number = record_id.rsplit("#", 1)
    return project, int(number)


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def validate_source_excerpt(con: duckdb.DuckDBPyConnection, dataset: Path, evidence: dict[str, Any], errors: list[str], cache: dict[tuple[str, str], Any]) -> None:
    source_type = evidence["source_type"]
    record_id = evidence["source_record_id"]
    field = evidence["source_field"]
    quoted = evidence["excerpt"]
    prefix = f"{record_id}/{evidence['evidence_id']}"
    if source_type in {"issue", "pr"}:
        project, number = parse_record_id(record_id)
        table = "issue.parquet" if source_type == "issue" else "pr.parquet"
        simple_field = field
        if simple_field not in {"title", "body"}:
            errors.append(f"{prefix}: unsupported {source_type} source field {field}")
            return
        key = (source_type, record_id)
        if key not in cache:
            cache[key] = con.execute(
                f"SELECT title,body FROM read_parquet('{sql_path(dataset/table)}') WHERE project=? AND number=?",
                [project, number],
            ).fetchone()
        row = cache[key]
        if not row:
            errors.append(f"{prefix}: source record not found")
            return
        text = row[0] if simple_field == "title" else row[1]
        if quoted not in (text or ""):
            errors.append(f"{prefix}: excerpt is not an exact substring of {table}.{simple_field}")
    elif source_type == "commit":
        relative = evidence["source_file"]
        if not relative.startswith("commits/"):
            errors.append(f"{prefix}: invalid commit source path")
            return
        path = dataset / relative
        if not path.is_file():
            errors.append(f"{prefix}: commit Parquet does not exist: {relative}")
            return
        key = ("commit", record_id)
        if key not in cache:
            cache[key] = con.execute(
                f"SELECT message,diff FROM read_parquet('{sql_path(path)}') WHERE split_part(sha,' ',1)=?",
                [record_id],
            ).fetchone()
        row = cache[key]
        if not row:
            errors.append(f"{prefix}: commit SHA not found in stated file")
            return
        if field == "message":
            valid = quoted in (row[0] or "")
        elif field.startswith("diff.patch"):
            valid = any(quoted in (change.get("patch") or "") for change in (row[1] or []))
        else:
            valid = False
        if not valid:
            errors.append(f"{prefix}: excerpt is not an exact substring of the stated commit field")
    elif source_type == "comment":
        if evidence.get("temporally_verified") is not False:
            errors.append(f"{prefix}: comment evidence must be temporally unverified")


def main() -> int:
    args = parse_args(); research = args.research_root.resolve(); dataset = args.dataset_root.resolve()
    gold = load_jsonl(research / "data" / "gold_references_draft.jsonl")
    leakage = load_jsonl(research / "data" / "leakage_manifest.jsonl")
    frozen = load_jsonl(research / "data" / "selected_bugs.jsonl")
    errors: list[str] = []
    frozen_ids = {r["stable_bug_id"] for r in frozen}
    gold_ids = [r.get("bug_id") for r in gold]
    leak_ids = [r.get("bug_id") for r in leakage]
    if len(gold) != 18 or len(set(gold_ids)) != 18:
        errors.append(f"Gold JSONL must contain exactly 18 unique records; got {len(gold)}/{len(set(gold_ids))}")
    if set(gold_ids) != frozen_ids:
        errors.append("Gold IDs do not exactly match frozen Phase-1 IDs")
    if len(leakage) != 18 or set(leak_ids) != frozen_ids:
        errors.append("Leakage manifest must contain exactly one record for every frozen bug")
    leak_by_id = {r["bug_id"]: r for r in leakage}
    con = duckdb.connect(); con.execute("SET threads=4")
    source_cache: dict[tuple[str, str], Any] = {}

    for record in gold:
        bug_id = record.get("bug_id", "<missing>")
        if record.get("issue_id") != bug_id:
            errors.append(f"{bug_id}: issue_id does not preserve the frozen stable ID")
        if record.get("annotation_status") != "pending_review":
            errors.append(f"{bug_id}: annotation_status must be pending_review")
        if record.get("final_human_decision") is not None:
            errors.append(f"{bug_id}: final_human_decision must remain null")
        forbidden = walk_keys(record) & FORBIDDEN_KEYS
        if forbidden:
            errors.append(f"{bug_id}: contains forbidden experimental/approval keys: {sorted(forbidden)}")

        evidence = record.get("evidence", [])
        evidence_ids = [e.get("evidence_id") for e in evidence]
        evidence_set = set(evidence_ids)
        if len(evidence_ids) != len(evidence_set) or None in evidence_set:
            errors.append(f"{bug_id}: evidence IDs must be unique and non-null")
        for item in evidence:
            required = ("evidence_id", "source_type", "source_record_id", "source_file", "source_field", "excerpt", "evidence_type", "evidence_availability", "temporally_verified", "temporal_role")
            missing = [key for key in required if key not in item or item[key] in {None, ""}]
            if missing:
                errors.append(f"{bug_id}/{item.get('evidence_id')}: missing provenance fields {missing}")
                continue
            if item["source_type"] not in ALLOWED_SOURCES:
                errors.append(f"{bug_id}/{item['evidence_id']}: invalid source_type")
            if item["source_type"] in {"pr", "commit"} and item["temporal_role"] != "evaluation_only_future":
                errors.append(f"{bug_id}/{item['evidence_id']}: target repair evidence must be evaluation-only")
            validate_source_excerpt(con, dataset, item, errors, source_cache)

        for field in FACT_FIELDS:
            value = record.get(field)
            if value is None:
                continue
            if not value.get("value") or value.get("status") not in ALLOWED_STATUS or value.get("confidence") not in ALLOWED_CONFIDENCE:
                errors.append(f"{bug_id}/{field}: malformed factual annotation")
            refs = value.get("evidence_ids", [])
            if not refs or not set(refs) <= evidence_set:
                errors.append(f"{bug_id}/{field}: evidence references are missing or unresolved")

        conditions = record.get("trigger_conditions", [])
        condition_ids = [c.get("condition_id") for c in conditions]
        condition_set = set(condition_ids)
        if not conditions or len(condition_ids) != len(condition_set) or None in condition_set:
            errors.append(f"{bug_id}: condition IDs must be non-empty and unique")
        for condition in conditions:
            if condition.get("status") not in ALLOWED_STATUS or condition.get("confidence") not in ALLOWED_CONFIDENCE:
                errors.append(f"{bug_id}/{condition.get('condition_id')}: invalid status/confidence")
            if not condition.get("description") or not condition.get("type"):
                errors.append(f"{bug_id}/{condition.get('condition_id')}: condition lacks description/type")
            if not condition.get("evidence_ids") or not set(condition["evidence_ids"]) <= evidence_set:
                errors.append(f"{bug_id}/{condition.get('condition_id')}: unresolved evidence")

        paths = record.get("trigger_paths", [])
        path_ids = [p.get("path_id") for p in paths]
        path_set = set(path_ids)
        if not paths or len(path_ids) != len(path_set) or None in path_set:
            errors.append(f"{bug_id}: path IDs must be non-empty and unique")
        for path in paths:
            refs = set(path.get("required_condition_ids", [])) | set(path.get("ordered_condition_ids", []))
            if not refs or not refs <= condition_set:
                errors.append(f"{bug_id}/{path.get('path_id')}: invalid condition reference")
            if path.get("relationship_status") not in {"supported", "uncertain"} or path.get("completeness") not in {"complete", "partial"}:
                errors.append(f"{bug_id}/{path.get('path_id')}: invalid relationship/completeness")
            if not path.get("evidence_ids") or not set(path["evidence_ids"]) <= evidence_set:
                errors.append(f"{bug_id}/{path.get('path_id')}: unresolved evidence")

        oracles = record.get("test_oracles", [])
        oracle_ids = [o.get("oracle_id") for o in oracles]
        if not oracles or len(oracle_ids) != len(set(oracle_ids)):
            errors.append(f"{bug_id}: Oracle IDs must be non-empty and unique")
        for oracle in oracles:
            if oracle.get("trigger_path_id") not in path_set:
                errors.append(f"{bug_id}/{oracle.get('oracle_id')}: invalid trigger path")
            for field in ("expected_result", "incorrect_result", "verification_method"):
                if not oracle.get(field):
                    errors.append(f"{bug_id}/{oracle.get('oracle_id')}: missing {field}")
            if not oracle.get("evidence_ids") or not set(oracle["evidence_ids"]) <= evidence_set:
                errors.append(f"{bug_id}/{oracle.get('oracle_id')}: unresolved evidence")
            if oracle.get("confidence") not in ALLOWED_CONFIDENCE or oracle.get("completeness") not in {"complete", "incomplete"}:
                errors.append(f"{bug_id}/{oracle.get('oracle_id')}: invalid confidence/completeness")

        repair = record.get("repair_evidence_summary", {})
        if repair.get("explicit_fix_link_evidence_id") not in evidence_set:
            errors.append(f"{bug_id}: repair link evidence is unresolved")
        if repair.get("commit_records_available", 0) + repair.get("commit_records_unavailable", 0) != len(repair.get("repair_commit_shas", [])):
            errors.append(f"{bug_id}: repair commit availability counts are inconsistent")

        leak = leak_by_id.get(bug_id, {})
        if leak.get("retrieval_cutoff_timestamp") != record.get("issue_created_at"):
            errors.append(f"{bug_id}: leakage cutoff does not match Issue creation")
        if repair.get("fixing_pr_id") not in leak.get("restricted_fixing_prs", []):
            errors.append(f"{bug_id}: fixing PR missing from leakage restrictions")
        if set(repair.get("repair_commit_shas", [])) != set(leak.get("restricted_repair_commits", [])):
            errors.append(f"{bug_id}: repair Commit leakage restrictions are incomplete")
        if leak.get("post_report_discussions_restricted") is not True:
            errors.append(f"{bug_id}: post-report discussions must be restricted")

    complete_paths = sum(all(p["completeness"] == "complete" for p in r["trigger_paths"]) for r in gold)
    usable_oracles = sum(all(o["confidence"] in {"high", "medium"} and o["expected_result"] and o["incorrect_result"] for o in r["test_oracles"]) for r in gold)
    substantial = sum(r.get("review_priority") == "substantial" for r in gold)
    if errors:
        print("Gold Reference validation FAILED:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": "PASS", "records": len(gold), "frozen_id_match": True,
        "complete_trigger_paths": complete_paths, "usable_test_oracles": usable_oracles,
        "substantial_human_review": substantial, "evidence_entries": sum(len(r["evidence"]) for r in gold),
        "all_pending_review": True, "future_experimental_outputs_present": False,
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
