#!/usr/bin/env python3
"""Build the external Phase 2.9 ABC human-review supplement ZIP."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from phase2_9_abc_common import normalize, sha256


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"
OUTPUT = ROOT / "phase2_9_review_supplement.zip"
FIXED_ZIP_TIME = (2026, 11, 16, 0, 0, 0)


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in rows), encoding="utf-8")


def parse_id(stable_id: str) -> tuple[str, int]:
    project, number = stable_id.rsplit("#", 1)
    return project, int(number)


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_issue_rows(con: duckdb.DuckDBPyConnection, ids: set[str]) -> dict[str, dict[str, Any]]:
    con.execute("CREATE OR REPLACE TEMP TABLE wanted_issues(project VARCHAR, number BIGINT)")
    con.executemany("INSERT INTO wanted_issues VALUES (?,?)", [parse_id(x) for x in sorted(ids)])
    rows = con.execute("""
        SELECT i.project,i.number,i.contributor,i.title,i.body,i.labels,i.state,i.created_at,i.closed_at
        FROM read_parquet(?) i JOIN wanted_issues w USING(project,number)
    """, [str(DATASET / "issue.parquet")]).fetchall()
    names = [x[0] for x in con.description]
    return {f"{r[0]}#{r[1]}": dict(zip(names, r)) for r in rows}


def full_issue(record: dict[str, Any], *, doc_id: str) -> dict[str, Any]:
    title, body = record.get("title") or "", record.get("body") or ""
    text = f"{title}\n{body}"
    return {"doc_id": doc_id, "project": record["project"], "issue_number": record["number"],
            "contributor": record.get("contributor"), "created_at": record.get("created_at"),
            "closed_at": record.get("closed_at"), "state": record.get("state"), "labels": record.get("labels"),
            "title": title, "body": body, "content_sha256": sha256(text),
            "normalized_content_sha256": sha256(normalize(text)), "source_file": "issue.parquet",
            "snapshot_semantics": "current_OSSGameBench_dataset_snapshot_not_verified_creation_time_text"}


def fetch_source_record(con: duckdb.DuckDBPyConnection, evidence: dict[str, Any]) -> dict[str, Any] | None:
    stype, rid = evidence["source_type"], evidence["source_record_id"]
    if stype in {"issue", "pr"}:
        project, number = parse_id(rid)
        table = DATASET / ("issue.parquet" if stype == "issue" else "pr.parquet")
        row = con.execute("SELECT * FROM read_parquet(?) WHERE project=? AND number=?", [str(table), project, number]).fetchone()
        if not row:
            return None
        names = [x[0] for x in con.description]
        return {"source_type": stype, "source_file": evidence["source_file"], "source_record_id": rid,
                "record": dict(zip(names, row))}
    if stype == "commit":
        path = DATASET / evidence["source_file"]
        if not path.is_file():
            return None
        row = con.execute("SELECT * FROM read_parquet(?) WHERE split_part(sha,' ',1)=?", [str(path), rid]).fetchone()
        if not row:
            return None
        names = [x[0] for x in con.description]
        return {"source_type": stype, "source_file": evidence["source_file"], "source_record_id": rid,
                "record": dict(zip(names, row))}
    return None


def field_value(source: dict[str, Any], field: str) -> str | None:
    record = source["record"]
    if field in {"title", "body", "message"}:
        return record.get(field)
    if field.startswith("diff.patch"):
        wanted = field.removeprefix("diff.patch").strip()
        if wanted.startswith("(") and wanted.endswith(")"):
            wanted = wanted[1:-1]
        for item in record.get("diff") or []:
            if not wanted or item.get("file") == wanted:
                return item.get("patch")
    return None


def main() -> None:
    con = duckdb.connect()
    top10 = jsonl(ROOT / "data/phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl")
    top3 = jsonl(ROOT / "data/phase2_9_abc_top3_similar_PROPOSED.jsonl")
    random_context = jsonl(ROOT / "data/phase2_9_abc_random_context_PROPOSED.jsonl")
    targets = jsonl(ROOT / "data/phase2_9_abc_target_issue_inputs.jsonl")
    gold = jsonl(ROOT / "data/gold_references_phase2_6_candidate.jsonl")
    top3_by = {x["bug_id"]: x for x in top3}
    random_by = {x["bug_id"]: x for x in random_context}
    target_ids = {x["bug_id"] for x in targets}
    top10_ids = {c["doc_id"] for x in top10 for c in x["candidates"]}
    b_ids = {d for x in top3 for d in x["document_ids"]}
    c_ids = {d["doc_id"] for x in random_context for d in x["documents"]}
    all_issue_ids = target_ids | top10_ids | b_ids | c_ids
    issues = fetch_issue_rows(con, all_issue_ids)

    with tempfile.TemporaryDirectory(prefix="phase2_9_review_supplement.") as tmp:
        stage = Path(tmp) / "phase2_9_review_supplement"
        exports, reference = stage / "exports", stage / "reference"
        exports.mkdir(parents=True); reference.mkdir()

        target_export = []
        for target in targets:
            row = issues.get(target["bug_id"])
            if row:
                item = full_issue(row, doc_id=target["bug_id"])
                item.update({"target_bug_id": target["bug_id"], "selection_role": "TARGET_ISSUE",
                             "manifest_content_sha256": target["content_sha256"],
                             "content_hash_matches_manifest": item["content_sha256"] == target["content_sha256"]})
                target_export.append(item)
        write_jsonl(exports / "target_issues_full.jsonl", target_export)

        top10_export = []
        b_export = []
        c_export = []
        c_outside = []
        for result in top10:
            bug = result["bug_id"]
            b_set = set(top3_by[bug]["document_ids"])
            c_set = {x["doc_id"] for x in random_by[bug]["documents"]}
            for candidate in result["candidates"]:
                row = issues.get(candidate["doc_id"])
                if not row:
                    continue
                item = full_issue(row, doc_id=candidate["doc_id"])
                item.update({"target_bug_id": bug, "bm25_rank": candidate["rank_before_human_review"],
                             "bm25_score": candidate["bm25_score"], "automated_risk_status": candidate["automated_risk_status"],
                             "human_relevance_decision": candidate["human_relevance_decision"],
                             "human_leakage_decision": candidate["human_leakage_decision"],
                             "proposed_group_b_for_target": candidate["doc_id"] in b_set,
                             "proposed_group_c_for_target": candidate["doc_id"] in c_set,
                             "manifest_content_sha256": candidate["content_sha256"],
                             "content_hash_matches_manifest": item["content_sha256"] == candidate["content_sha256"]})
                top10_export.append(item)
        top10_lookup = {(x["target_bug_id"], x["doc_id"]): x for x in top10_export}
        for selection in top3:
            for slot, selected in enumerate(selection["documents"], 1):
                base = dict(top10_lookup[(selection["bug_id"], selected["doc_id"])])
                base.update({"group": "B", "selection_slot": slot, "selection_status": selection["status"],
                             "human_approval": selection["human_approval"]})
                b_export.append(base)
        for selection in random_context:
            top_ids_for_target = {x["doc_id"] for x in top10_export if x["target_bug_id"] == selection["bug_id"]}
            for selected in selection["documents"]:
                row = issues.get(selected["doc_id"])
                if not row:
                    continue
                item = full_issue(row, doc_id=selected["doc_id"])
                item.update({"target_bug_id": selection["bug_id"], "group": "C", "selection_slot": selected["slot"],
                             "selection_status": selection["status"], "global_seed": selection["global_seed"],
                             "per_slot_seed": selected["seed"], "matched_to_group_b_doc_id": selected["matched_to_similar_doc_id"],
                             "matched_to_group_b_token_count": selected["matched_to_similar_token_count"],
                             "token_count": selected["token_count"], "length_tolerance_used": selected["length_tolerance_used"],
                             "automated_risk_status": selected["automated_risk_status"],
                             "human_approval": selected["human_approval"],
                             "in_target_bm25_top10": selected["doc_id"] in top_ids_for_target,
                             "manifest_content_sha256": selected["content_sha256"],
                             "content_hash_matches_manifest": item["content_sha256"] == selected["content_sha256"]})
                c_export.append(item)
                if not item["in_target_bm25_top10"]:
                    c_outside.append(item)
        write_jsonl(exports / "bm25_top10_candidates_full.jsonl", top10_export)
        write_jsonl(exports / "group_b_proposed_context_full.jsonl", b_export)
        write_jsonl(exports / "group_c_proposed_context_full.jsonl", c_export)
        write_jsonl(exports / "group_c_outside_target_top10_full.jsonl", c_outside)

        source_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
        source_records: list[dict[str, Any]] = []
        evidence_export: list[dict[str, Any]] = []
        unresolved_evidence: list[dict[str, Any]] = []
        for record in gold:
            for evidence in record["evidence"]:
                key = (evidence["source_file"], evidence["source_record_id"])
                if key not in source_cache:
                    source_cache[key] = fetch_source_record(con, evidence)
                    if source_cache[key] is not None:
                        source_records.append(source_cache[key])
                source = source_cache[key]
                value = field_value(source, evidence["source_field"]) if source else None
                resolved = value is not None and evidence["excerpt"] in value
                item = dict(evidence)
                item.update({"bug_id": record["bug_id"], "full_original_field_value": value,
                             "source_record_key": f"{key[0]}::{key[1]}", "resolved_exact_excerpt": resolved})
                evidence_export.append(item)
                if not resolved:
                    unresolved_evidence.append({"bug_id": record["bug_id"], "evidence_id": evidence["evidence_id"],
                                                "source_file": evidence["source_file"], "source_record_id": evidence["source_record_id"],
                                                "source_field": evidence["source_field"], "reason": "source/field missing or excerpt not found"})
        write_jsonl(exports / "gold_evidence_full_original_fields.jsonl", evidence_export)
        write_jsonl(exports / "gold_source_records_full.jsonl", source_records)

        repair_availability = []
        for record in gold:
            project = record["bug_id"].rsplit("#", 1)[0]
            owner, repo = project.split("/", 1)
            commit_path = DATASET / "commits" / f"commits|{owner}|{repo}.parquet"
            for commit_sha in record["repair_evidence_summary"]["repair_commit_shas"]:
                found = bool(commit_path.is_file() and con.execute("SELECT 1 FROM read_parquet(?) WHERE split_part(sha,' ',1)=? LIMIT 1", [str(commit_path), commit_sha]).fetchone())
                repair_availability.append({"bug_id": record["bug_id"], "commit_sha": commit_sha,
                                            "source_file": str(commit_path.relative_to(DATASET)), "found_in_local_dataset": found,
                                            "note": None if found else "Referenced repair commit is unavailable locally; no evidence was invented."})
        write_jsonl(exports / "gold_repair_commit_availability.jsonl", repair_availability)
        write_jsonl(exports / "gold_unresolved_evidence.jsonl", unresolved_evidence)

        original_files = {
            "gold_references_phase2_6_candidate.jsonl": ROOT / "data/gold_references_phase2_6_candidate.jsonl",
            "gold_references_refined_draft.jsonl": ROOT / "data/gold_references_refined_draft.jsonl",
            "gold_references_draft.jsonl": ROOT / "data/gold_references_draft.jsonl",
            "phase2_9_abc_target_issue_inputs.jsonl": ROOT / "data/phase2_9_abc_target_issue_inputs.jsonl",
            "phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl": ROOT / "data/phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl",
            "phase2_9_abc_top3_similar_PROPOSED.jsonl": ROOT / "data/phase2_9_abc_top3_similar_PROPOSED.jsonl",
            "phase2_9_abc_random_context_PROPOSED.jsonl": ROOT / "data/phase2_9_abc_random_context_PROPOSED.jsonl",
            "phase2_9_abc_gold_adjudication_workbook.md": ROOT / "reports/phase2_9_abc_gold_adjudication_workbook.md",
            "test_scenario_evaluation_rubric_abc_PROPOSED.md": ROOT / "reports/test_scenario_evaluation_rubric_abc_PROPOSED.md",
            "phase2_9_abc_experiment_protocol_PROPOSED.md": ROOT / "reports/phase2_9_abc_experiment_protocol_PROPOSED.md",
            "phase2_9_abc_leakage_policy_PROPOSED.json": ROOT / "data/phase2_9_abc_leakage_policy_PROPOSED.json",
        }
        for name, source in original_files.items():
            shutil.copy2(source, reference / name)

        def duplicate_stats(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> dict[str, Any]:
            keys = ["::".join(str(x.get(f)) for f in key_fields) for x in rows]
            counts = Counter(keys)
            duplicate_keys = sorted(k for k, v in counts.items() if v > 1)
            return {"records": len(rows), "unique_keys": len(counts), "duplicate_key_count": len(duplicate_keys),
                    "duplicate_keys": duplicate_keys}

        missing_issue_ids = sorted(all_issue_ids - set(issues))
        missing_texts = {
            "missing_source_issue_rows": missing_issue_ids,
            "target_empty_title": [x["doc_id"] for x in target_export if not x["title"]],
            "target_empty_body": [x["doc_id"] for x in target_export if not x["body"]],
            "top10_empty_title_records": [f"{x['target_bug_id']}::{x['doc_id']}" for x in top10_export if not x["title"]],
            "top10_empty_body_records": [f"{x['target_bug_id']}::{x['doc_id']}" for x in top10_export if not x["body"]],
            "group_c_empty_title_records": [f"{x['target_bug_id']}::{x['doc_id']}" for x in c_export if not x["title"]],
            "group_c_empty_body_records": [f"{x['target_bug_id']}::{x['doc_id']}" for x in c_export if not x["body"]],
        }
        declared_unavailable = sum(x["repair_evidence_summary"]["commit_records_unavailable"] for x in gold)
        counts = {
            "target_issue_records": len(target_export), "bm25_top10_records": len(top10_export),
            "bm25_top10_unique_issue_ids": len({x["doc_id"] for x in top10_export}),
            "group_b_records": len(b_export), "group_b_unique_issue_ids": len({x["doc_id"] for x in b_export}),
            "group_c_records": len(c_export), "group_c_unique_issue_ids": len({x["doc_id"] for x in c_export}),
            "group_c_outside_target_top10_records": len(c_outside), "gold_records": len(gold),
            "gold_evidence_items": len(evidence_export), "gold_evidence_resolved": sum(x["resolved_exact_excerpt"] for x in evidence_export),
            "gold_evidence_unresolved": len(unresolved_evidence), "gold_unique_source_records": len(source_records),
            "repair_commit_references": len(repair_availability),
            "repair_commits_missing_locally": sum(not x["found_in_local_dataset"] for x in repair_availability),
            "declared_unavailable_repair_commit_records": declared_unavailable,
        }
        duplicates = {
            "targets": duplicate_stats(target_export, ("doc_id",)),
            "top10_target_doc_pairs": duplicate_stats(top10_export, ("target_bug_id", "doc_id")),
            "group_b_target_doc_pairs": duplicate_stats(b_export, ("target_bug_id", "doc_id")),
            "group_c_target_doc_pairs": duplicate_stats(c_export, ("target_bug_id", "doc_id")),
            "gold_bug_ids": duplicate_stats(gold, ("bug_id",)),
        }
        readme = """# Phase 2.9 ABC Human Review Supplement\n\nThis package contains only external-review evidence for the retrospective ABC preparation. `exports/` contains full current OSSGameBench Issue text for targets, BM25 candidates, and B/C selections plus full locally resolvable Gold evidence fields. `reference/` contains byte-preserved project artifacts.\n\nIssue text is the current dataset snapshot, not verified creation-time text. PR/commit evidence is evaluation-only and must not enter model prompts. Human approval fields remain blank. No generation, scoring, or freeze is included.\n\nGroup C records outside the target's Top-10 are duplicated in `group_c_outside_target_top10_full.jsonl` for convenient review. See `manifest.json` and `validation_report.json` for counts, missing evidence, duplicates, and hashes.\n"""
        (stage / "README.md").write_text(readme, encoding="utf-8")

        validation = {
            "status": "PASS" if (len(target_export) == 18 and len(top10_export) == 180 and len(b_export) == 54 and len(c_export) == 54 and not missing_issue_ids and not unresolved_evidence and all(x["content_hash_matches_manifest"] for x in target_export + top10_export + c_export)) else "FAIL",
            "checks": {"targets_full_18": len(target_export) == 18, "top10_full_180": len(top10_export) == 180,
                       "group_b_full_54": len(b_export) == 54, "group_c_full_54": len(c_export) == 54,
                       "all_requested_issue_rows_found": not missing_issue_ids,
                       "all_manifest_content_hashes_match": all(x["content_hash_matches_manifest"] for x in target_export + top10_export + c_export),
                       "all_gold_evidence_items_resolve": not unresolved_evidence,
                       "human_approval_fields_preserved": all(x.get("human_approval") is None for x in b_export + c_export),
                       "abc_rubric_included": (reference / "test_scenario_evaluation_rubric_abc_PROPOSED.md").exists(),
                       "abc_protocol_included": (reference / "phase2_9_abc_experiment_protocol_PROPOSED.md").exists()},
            "limitations": {"repair_commits_missing_locally": counts["repair_commits_missing_locally"],
                            "current_snapshot_not_historical_text": True, "semantic_safety_not_validated": True},
        }
        (stage / "validation_report.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")

        manifest = {"package": "phase2_9_review_supplement", "created_at": datetime.now(timezone.utc).isoformat(),
                    "status": "HUMAN_REVIEW_ONLY_NOT_FROZEN", "counts": counts, "missing_texts": missing_texts,
                    "missing_evidence": unresolved_evidence, "duplicates": duplicates,
                    "scope": {"entire_candidate_corpus_included": False, "official_model_outputs_included": False,
                              "human_approvals_modified": False}}
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # A manifest cannot contain a stable hash of itself. Hash every other
        # package member; the ZIP hash is reported externally after creation.
        package_files = [p for p in stage.rglob("*") if p.is_file() and p.name != "manifest.json"]
        manifest["files"] = [{"path": str(p.relative_to(stage)), "size_bytes": p.stat().st_size, "sha256": file_sha(p)} for p in sorted(package_files)]
        (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        tmp_zip = OUTPUT.with_suffix(".zip.tmp")
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for path in sorted(p for p in stage.rglob("*") if p.is_file()):
                info = zipfile.ZipInfo(str(Path(stage.name) / path.relative_to(stage)), FIXED_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                archive.writestr(info, path.read_bytes(), compresslevel=9)
        os.replace(tmp_zip, OUTPUT)
        if validation["status"] != "PASS":
            raise SystemExit(json.dumps(validation, indent=2))
        print(json.dumps({"status": "PASS", "zip": str(OUTPUT), "zip_sha256": file_sha(OUTPUT),
                          "zip_size_bytes": OUTPUT.stat().st_size, "counts": counts,
                          "missing_text_source_rows": len(missing_issue_ids), "unresolved_gold_evidence": len(unresolved_evidence)}, indent=2))


if __name__ == "__main__":
    main()
