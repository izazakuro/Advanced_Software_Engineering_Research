#!/usr/bin/env python3
"""Export Phase 2.11D selections and local dataset evidence for human review."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sqlite3
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

try:
    import duckdb
except ImportError as exc:  # pragma: no cover - environment diagnostic
    raise SystemExit("duckdb is required; set PYTHONPATH to the approved temporary dependency directory") from exc


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"
FINAL_DIR = ROOT / "data/phase2_11E_evidence"
STAGE_DIR = ROOT / "data/.phase2_11E_evidence.tmp"
FINAL_ZIP = ROOT / "phase2_11E_human_adjudication_evidence_package.zip"
STAGE_ZIP = ROOT / ".phase2_11E_human_adjudication_evidence_package.zip.tmp"
PROMPT_HASH = "d42621f35d0ae6d2b0dcece8291cc686c83fa318e51f2548c73cd7aa5f43206e"
FLAME_PAIRS = {
    ("flame-engine/flame#1003", "flame-engine/tiled.dart#58"),
    ("flame-engine/flame#1003", "flame-engine/tiled.dart#81"),
    ("flame-engine/flame#1003", "flame-engine/tiled.dart#56"),
    ("flame-engine/flame#1264", "flame-engine/tiled.dart#81"),
    ("flame-engine/flame#1264", "flame-engine/tiled.dart#58"),
}
GITHUB_REF_RE = re.compile(
    r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s?#]+)/(?:(issues)|(pull)|(commit))/([A-Za-z0-9._-]+)", re.I
)
SHORT_REF_RE = re.compile(r"(?<![A-Za-z0-9_])#(\d+)\b")
REPO_URL_RE = re.compile(r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s?#)]+)", re.I)


def json_default(value: object) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def dump_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=json_default)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(dump_json(row) + "\n" for row in rows), encoding="utf-8")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def content_hash(title: str | None, body: str | None) -> str:
    return hashlib.sha256(f"{title or ''}\n{body or ''}".encode("utf-8")).hexdigest()


def split_doc_id(doc_id: str) -> tuple[str, int]:
    project, number = doc_id.rsplit("#", 1)
    return project, int(number)


def owner(project: str) -> str:
    return project.split("/", 1)[0].casefold()


def rows_as_dicts(cursor) -> list[dict]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def evidence_id(index: int) -> str:
    return f"E{index:06d}"


def add_evidence(records: list[dict], record: dict) -> str:
    identifier = evidence_id(len(records) + 1)
    records.append({"evidence_id": identifier, **record})
    return identifier


def extract_text_references(text: str, default_project: str, source_record_id: str) -> list[dict]:
    refs: list[dict] = []
    seen = set()
    for match in GITHUB_REF_RE.finditer(text or ""):
        project = f"{match.group(1)}/{match.group(2).removesuffix('.git')}"
        if match.group(3):
            kind, value = "ISSUE_URL", f"{project}#{match.group(6)}"
        elif match.group(4):
            kind, value = "PULL_REQUEST_URL", f"{project}#{match.group(6)}"
        else:
            kind, value = "COMMIT_URL", f"{project}@{match.group(6)}"
        key = (kind, value, match.group(0))
        if key not in seen:
            refs.append({"reference_type": kind, "referenced_id": value, "raw_reference": match.group(0),
                         "provenance": "TEXTUAL_REFERENCE", "source_record_id": source_record_id})
            seen.add(key)
    url_spans = [match.span() for match in GITHUB_REF_RE.finditer(text or "")]
    for match in SHORT_REF_RE.finditer(text or ""):
        if any(start <= match.start() < end for start, end in url_spans):
            continue
        value = f"{default_project}#{match.group(1)}"
        key = ("UNTYPED_SAME_REPOSITORY_NUMBER", value, match.group(0))
        if key not in seen:
            refs.append({"reference_type": key[0], "referenced_id": value, "raw_reference": match.group(0),
                         "provenance": "TEXTUAL_REFERENCE_TYPE_NOT_RESOLVED", "source_record_id": source_record_id})
            seen.add(key)
    return refs


def main() -> None:
    if FINAL_DIR.exists() or FINAL_ZIP.exists() or STAGE_DIR.exists() or STAGE_ZIP.exists():
        raise SystemExit("Phase 2.11E output or staging path already exists; refusing to overwrite")
    stage = STAGE_DIR
    stage.mkdir(parents=True)

    manifest_path = ROOT / "data/phase2_11D_ABC_manifest_NOT_FROZEN.jsonl"
    b_path = ROOT / "data/phase2_11D_B_cross_project_top3_PROPOSED.jsonl"
    c_path = ROOT / "data/phase2_11D_C_cross_project_random3_PROPOSED.jsonl"
    audit_path = ROOT / "data/phase2_11D_cross_project_eligibility_audit.csv"
    decisions_path = ROOT / "reports/phase2_11D_remaining_human_decisions.md"
    phase2_11d_paths = sorted(
        list((ROOT / "data").glob("phase2_11D*"))
        + list((ROOT / "reports").glob("phase2_11D*"))
        + list((ROOT / "scripts").glob("phase2_11D*"))
    )
    phase2_11d_hashes = {str(path.relative_to(ROOT)): sha_file(path) for path in phase2_11d_paths}

    manifest = load_jsonl(manifest_path)
    b_records = {record["bug_id"]: record for record in load_jsonl(b_path)}
    c_records = {record["bug_id"]: record for record in load_jsonl(c_path)}
    target_ids = [record["target_bug_id"] for record in manifest]
    expected_hash: dict[str, str] = {}
    pair_rows: list[dict] = []
    for record in manifest:
        bug_id = record["target_bug_id"]
        expected_hash[bug_id] = record["target_issue"]["content_sha256"]
        b_by_id = {item["doc_id"]: item for item in b_records[bug_id]["documents"]}
        c_by_id = {item["doc_id"]: item for item in c_records[bug_id]["documents"]}
        for group_key, group in (("group_B", "B"), ("group_C", "C")):
            for slot, item in enumerate(record[group_key]["documents"], 1):
                expected_hash[item["doc_id"]] = item["content_sha256"]
                details = b_by_id[item["doc_id"]] if group == "B" else c_by_id[item["doc_id"]]
                pair_rows.append({
                    "target_bug_id": bug_id, "group": group, "slot": slot,
                    "candidate_doc_id": item["doc_id"], "candidate_repository": item["project"],
                    "candidate_issue_number": split_doc_id(item["doc_id"])[1],
                    "original_bm25_rank": details.get("original_bm25_rank"),
                    "original_bm25_score": details.get("bm25_score"),
                    "selection_seed": details.get("seed"),
                    "sampling_metadata": ({key: details.get(key) for key in (
                        "matched_to_B_doc_id", "matched_to_B_token_count", "length_tolerance_used",
                        "eligible_pool_size", "selection_method")}
                        if group == "C" else None),
                    "selection_method": (b_records[bug_id]["method"] if group == "B" else c_records[bug_id]["method"]),
                    "content_sha256": item["content_sha256"], "human_decision": None,
                    "review_status": "REVIEW_REQUIRED",
                })
    unique_ids = sorted(set(target_ids) | {row["candidate_doc_id"] for row in pair_rows})

    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE selected_issues(project VARCHAR, number BIGINT, doc_id VARCHAR)")
    con.executemany("INSERT INTO selected_issues VALUES (?, ?, ?)", [(*split_doc_id(doc_id), doc_id) for doc_id in unique_ids])
    issue_rows = rows_as_dicts(con.execute(
        "SELECT s.doc_id, i.project, i.number, i.contributor, i.title, i.body, i.labels, i.state, i.created_at, i.closed_at "
        "FROM read_parquet(?) i JOIN selected_issues s ON i.project=s.project AND i.number=s.number ORDER BY s.doc_id",
        [str(DATASET / "issue.parquet")],
    ))
    issue_by_id = {row["doc_id"]: row for row in issue_rows}

    comment_rows = rows_as_dicts(con.execute(
        "WITH source AS (SELECT row_number() OVER () AS source_order, * FROM read_parquet(?)) "
        "SELECT s.doc_id, c.source_order, c.contributor, c.body FROM source c "
        "JOIN selected_issues s ON c.project=s.project AND c.issue_number=s.number "
        "ORDER BY s.doc_id, c.source_order",
        [str(DATASET / "issue_comment.parquet")],
    ))
    comments_by_id: dict[str, list[dict]] = defaultdict(list)
    for row in comment_rows:
        ordinal = len(comments_by_id[row["doc_id"]]) + 1
        comments_by_id[row["doc_id"]].append({
            "source_order": ordinal, "author": row["contributor"], "created_at": None,
            "body": row["body"] if row["body"] is not None else "",
            "source_dataset": "OSSGameBench", "source_table": "issue_comment.parquet",
            "source_record_id": f"{row['doc_id']}:comment:{ordinal}",
            "missing_field_reasons": {"created_at": "FIELD_NOT_PRESENT_IN_ISSUE_COMMENT_PARQUET",
                                      "stable_comment_id": "FIELD_NOT_PRESENT_IN_ISSUE_COMMENT_PARQUET"},
        })

    repositories = sorted({split_doc_id(doc_id)[0] for doc_id in unique_ids})
    con.execute("CREATE TEMP TABLE selected_projects(project VARCHAR)")
    con.executemany("INSERT INTO selected_projects VALUES (?)", [(project,) for project in repositories])
    project_rows = rows_as_dicts(con.execute(
        "SELECT p.project, p.is_fork, p.languages, p.topics, p.created_at, p.size, p.readme_content "
        "FROM read_parquet(?) p JOIN selected_projects s ON p.project=s.project ORDER BY p.project",
        [str(DATASET / "project.parquet")],
    ))
    project_by_id = {row["project"]: row for row in project_rows}

    issue_pr_rows = rows_as_dicts(con.execute(
        "SELECT s.doc_id, r.project, r.issue_number, r.pr_number FROM read_parquet(?) r "
        "JOIN selected_issues s ON r.project=s.project AND r.issue_number=s.number ORDER BY s.doc_id, r.pr_number",
        [str(DATASET / "issue_pr.parquet")],
    ))
    pr_keys = sorted({(row["project"], int(row["pr_number"])) for row in issue_pr_rows})
    con.execute("CREATE TEMP TABLE selected_prs(project VARCHAR, number BIGINT)")
    if pr_keys:
        con.executemany("INSERT INTO selected_prs VALUES (?, ?)", pr_keys)
    pr_rows = rows_as_dicts(con.execute(
        "SELECT p.* FROM read_parquet(?) p JOIN selected_prs s ON p.project=s.project AND p.number=s.number "
        "ORDER BY p.project, p.number", [str(DATASET / "pr.parquet")]
    )) if pr_keys else []
    pr_by_key = {(row["project"], int(row["number"])): row for row in pr_rows}
    pr_comment_rows = rows_as_dicts(con.execute(
        "WITH source AS (SELECT row_number() OVER () AS source_order, * FROM read_parquet(?)) "
        "SELECT c.* FROM source c JOIN selected_prs s ON c.project=s.project AND c.pr_number=s.number "
        "ORDER BY c.project, c.pr_number, c.source_order", [str(DATASET / "pr_comment.parquet")]
    )) if pr_keys else []
    pr_comments: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in pr_comment_rows:
        key = (row["project"], int(row["pr_number"]))
        pr_comments[key].append({"source_order": len(pr_comments[key]) + 1, "author": row["contributor"],
                                 "created_at": None, "body": row["body"] or "",
                                 "missing_field_reason": "TIMESTAMP_NOT_PRESENT_IN_PR_COMMENT_PARQUET"})
    pr_commit_rows = rows_as_dicts(con.execute(
        "SELECT c.project, c.pr_number, c.commit_sha FROM read_parquet(?) c "
        "JOIN selected_prs s ON c.project=s.project AND c.pr_number=s.number "
        "ORDER BY c.project, c.pr_number, c.commit_sha", [str(DATASET / "pr_commit.parquet")]
    )) if pr_keys else []
    pr_commits: dict[tuple[str, int], list[str]] = defaultdict(list)
    for row in pr_commit_rows:
        pr_commits[(row["project"], int(row["pr_number"]))].append(row["commit_sha"])
    metadata_rows = rows_as_dicts(con.execute("SELECT project, file_location FROM read_parquet(?)", [str(DATASET / "commit_metadata.parquet")]))
    commit_file = {row["project"]: ROOT.parent / row["file_location"] for row in metadata_rows}

    commit_cache: dict[tuple[str, str], dict | None] = {}
    missing_commit_details: list[dict] = []
    for project, pr_number in pr_keys:
        path = commit_file.get(project)
        for commit_sha in pr_commits.get((project, pr_number), []):
            key = (project, commit_sha)
            detail = None
            if path and path.exists():
                found = rows_as_dicts(con.execute(
                    "SELECT repo, sha, contributor, contributor_email, created_at, message, files, diff "
                    "FROM read_parquet(?) WHERE split_part(sha, ' ', 1)=? LIMIT 1", [str(path), commit_sha]
                ))
                detail = found[0] if found else None
            if detail is None:
                missing_commit_details.append({"project": project, "pr_number": pr_number, "commit_sha": commit_sha,
                                               "reason": "COMMIT_DETAIL_NOT_AVAILABLE_IN_DATASET_FILE"})
            commit_cache[key] = detail

    evidence: list[dict] = []
    evidence_by_doc: dict[str, list[str]] = defaultdict(list)
    project_evidence: dict[str, str] = {}
    project_all_evidence: dict[str, list[str]] = defaultdict(list)
    for project in repositories:
        metadata = project_by_id.get(project)
        identifier = add_evidence(evidence, {
            "evidence_type": "PROJECT_METADATA", "subject_id": project,
            "provenance": {"kind": "STRUCTURED_DATASET_RECORD", "source_dataset": "OSSGameBench",
                           "source_table": "project.parquet", "source_record_id": project},
            "data": ({"repository": project, "owner": owner(project), "is_fork": metadata["is_fork"],
                      "fork_parent": None, "languages_raw": metadata["languages"], "topics_raw": metadata["topics"],
                      "created_at": metadata["created_at"], "size": metadata["size"],
                      "readme_content": metadata["readme_content"],
                      "missing_field_reasons": {"fork_parent": "FIELD_NOT_PRESENT_IN_PROJECT_PARQUET",
                                                "mirror_status": "FIELD_NOT_PRESENT_IN_PROJECT_PARQUET",
                                                "rename_or_transfer_history": "NOT_AVAILABLE_IN_DATASET"}}
                     if metadata else {"repository": project, "status": "NOT_AVAILABLE_IN_DATASET"}),
        })
        project_evidence[project] = identifier
        project_all_evidence[project].append(identifier)
        if metadata and metadata.get("readme_content"):
            for match in REPO_URL_RE.finditer(metadata["readme_content"]):
                referenced = f"{match.group(1)}/{match.group(2).removesuffix('.git')}"
                if referenced.casefold() == project.casefold():
                    continue
                readme_evidence_id = add_evidence(evidence, {
                    "evidence_type": "PROJECT_README_REPOSITORY_REFERENCE", "subject_id": project,
                    "object_id": referenced, "provenance": {"kind": "TEXTUAL_REFERENCE",
                        "source_dataset": "OSSGameBench", "source_table": "project.parquet",
                        "source_record_id": project, "raw_reference": match.group(0)},
                    "interpretation": "REFERENCE_ONLY_NOT_DEPENDENCY_PROOF",
                })
                project_all_evidence[project].append(readme_evidence_id)

    structured_pr_by_doc: dict[str, list[dict]] = defaultdict(list)
    structured_commits_by_doc: dict[str, list[dict]] = defaultdict(list)
    for relation in issue_pr_rows:
        key = (relation["project"], int(relation["pr_number"]))
        pr = pr_by_key.get(key)
        commits = []
        for commit_sha in pr_commits.get(key, []):
            commits.append({"commit_sha": commit_sha, "detail": commit_cache.get((key[0], commit_sha)),
                            "detail_status": "AVAILABLE" if commit_cache.get((key[0], commit_sha)) else "NOT_AVAILABLE_IN_DATASET"})
        identifier = add_evidence(evidence, {
            "evidence_type": "STRUCTURED_ISSUE_PULL_REQUEST_RELATION", "subject_id": relation["doc_id"],
            "object_id": f"{key[0]}#{key[1]}",
            "provenance": {"kind": "STRUCTURED_DATASET_RELATION", "source_dataset": "OSSGameBench",
                           "source_table": "issue_pr.parquet",
                           "source_record_id": f"{relation['doc_id']}->PR#{key[1]}"},
            "pull_request": ({**pr, "comments": pr_comments.get(key, []), "commits": commits} if pr else None),
            "missing_reason": None if pr else "PR_RECORD_NOT_AVAILABLE_IN_PR_PARQUET",
        })
        evidence_by_doc[relation["doc_id"]].append(identifier)
        structured_pr_by_doc[relation["doc_id"]].append({"pr_id": f"{key[0]}#{key[1]}", "evidence_id": identifier})
        for commit in commits:
            structured_commits_by_doc[relation["doc_id"]].append({
                "commit_id": f"{key[0]}@{commit['commit_sha']}", "commit_sha": commit["commit_sha"],
                "evidence_id": identifier, "detail_status": commit["detail_status"],
                "provenance": "STRUCTURED_ISSUE_PR_AND_PR_COMMIT_RELATIONS",
            })

    snapshots: list[dict] = []
    snapshot_by_id: dict[str, dict] = {}
    hash_results: list[dict] = []
    for doc_id in unique_ids:
        row = issue_by_id.get(doc_id)
        if row is None:
            continue
        project = row["project"]
        comments = comments_by_id.get(doc_id, [])
        textual_refs = []
        textual_refs.extend(extract_text_references(row["body"] or "", project, doc_id))
        for comment in comments:
            textual_refs.extend(extract_text_references(comment["body"], project, comment["source_record_id"]))
        dedup = set()
        linked_issues = []
        linked_prs = list(structured_pr_by_doc.get(doc_id, []))
        linked_commits = list(structured_commits_by_doc.get(doc_id, []))
        for ref in textual_refs:
            key = (ref["reference_type"], ref["referenced_id"], ref["source_record_id"], ref["raw_reference"])
            if key in dedup:
                continue
            dedup.add(key)
            identifier = add_evidence(evidence, {"evidence_type": "ISSUE_TEXT_REFERENCE", "subject_id": doc_id, **ref})
            evidence_by_doc[doc_id].append(identifier)
            item = {"referenced_id": ref["referenced_id"], "evidence_id": identifier,
                    "provenance": ref["provenance"]}
            if ref["reference_type"] == "ISSUE_URL":
                linked_issues.append(item)
            elif ref["reference_type"] == "PULL_REQUEST_URL":
                linked_prs.append({"pr_id": ref["referenced_id"], "evidence_id": identifier})
            elif ref["reference_type"] == "COMMIT_URL":
                linked_commits.append(item)
            else:
                linked_issues.append({**item, "object_type": "UNTYPED_ISSUE_OR_PR"})
        computed_hash = content_hash(row["title"], row["body"])
        expected = expected_hash[doc_id]
        verification = "PASS" if computed_hash == expected else "HASH_VERIFICATION_UNRESOLVED"
        hash_results.append({"doc_id": doc_id, "expected_sha256": expected, "computed_sha256": computed_hash,
                             "status": verification, "hash_input": "UTF-8 bytes of title + newline + body; null=>empty"})
        snapshot = {
            "doc_id": doc_id, "repository": project, "issue_number": int(row["number"]),
            "title": row["title"] if row["title"] is not None else "",
            "body": row["body"] if row["body"] is not None else "",
            "state": row["state"], "created_at": row["created_at"], "updated_at": None,
            "closed_at": row["closed_at"], "labels": row["labels"] or [], "author": row["contributor"],
            "source_dataset": "OSSGameBench", "source_table": "issue.parquet", "source_record_id": doc_id,
            "original_content_sha256": computed_hash,
            "hash_verification": {"manifest_sha256": expected, "status": verification,
                                  "algorithm": "sha256((title_or_empty + '\\n' + body_or_empty).encode('utf-8'))"},
            "available_comments": comments,
            "linked_issue_references": linked_issues, "linked_pull_requests": linked_prs,
            "linked_commits": linked_commits,
            "repository_metadata_evidence_id": project_evidence[project],
            "all_evidence_ids": sorted(set(evidence_by_doc[doc_id] + project_all_evidence[project])),
            "retrieval_corpus_text": {"included_in_bm25": True, "fields": ["title", "body"],
                                      "assembly": "title + newline + body", "text_sha256": computed_hash,
                                      "processed_text_exported": False,
                                      "processed_text_note": "BM25 tokenization is reproducible from Phase 2.9 code; no separate processed field exists in issue.parquet"},
            "additional_review_only_evidence": {"comments_in_model_prompt": False, "pr_commit_evidence_in_model_prompt": False,
                                                "project_metadata_in_model_prompt": False},
            "missing_field_reasons": {"updated_at": "FIELD_NOT_PRESENT_IN_ISSUE_PARQUET",
                                      "comment_timestamps": "FIELD_NOT_PRESENT_IN_ISSUE_COMMENT_PARQUET" if comments else "NO_COMMENTS_PRESENT",
                                      "comment_stable_ids": "FIELD_NOT_PRESENT_IN_ISSUE_COMMENT_PARQUET" if comments else "NO_COMMENTS_PRESENT"},
        }
        snapshots.append(snapshot)
        snapshot_by_id[doc_id] = snapshot

    for pair in pair_rows:
        pair_key = (pair["target_bug_id"], pair["candidate_doc_id"])
        pair["target_snapshot_doc_id"] = pair["target_bug_id"]
        pair["candidate_snapshot_doc_id"] = pair["candidate_doc_id"]
        pair["hash_verification_status"] = snapshot_by_id.get(pair["candidate_doc_id"], {}).get("hash_verification", {}).get("status")
        pair["same_repository"] = split_doc_id(pair["target_bug_id"])[0].casefold() == pair["candidate_repository"].casefold()
        pair["same_owner"] = owner(split_doc_id(pair["target_bug_id"])[0]) == owner(pair["candidate_repository"])
        pair["evidence_ids"] = sorted(set(
            snapshot_by_id.get(pair["target_bug_id"], {}).get("all_evidence_ids", [])
            + snapshot_by_id.get(pair["candidate_doc_id"], {}).get("all_evidence_ids", [])
        ))
        if pair_key in FLAME_PAIRS:
            identifier = add_evidence(evidence, {
                "evidence_type": "SHARED_REPOSITORY_OWNER", "subject_id": pair["target_bug_id"],
                "object_id": pair["candidate_doc_id"], "owner": "flame-engine",
                "provenance": {"kind": "STABLE_REPOSITORY_IDENTIFIER_COMPONENT",
                               "source_records": [pair["target_bug_id"], pair["candidate_doc_id"]]},
                "interpretation": "REVIEW_REQUIRED_NOT_LEAKAGE_CLASSIFICATION",
            })
            pair["evidence_ids"].append(identifier)

    worksheet_rows = []
    for pair in pair_rows:
        worksheet_rows.append({
            "target_bug_id": pair["target_bug_id"], "candidate_doc_id": pair["candidate_doc_id"],
            "group": pair["group"], "slot": pair["slot"],
            "same_repository": str(pair["same_repository"]).lower(), "same_owner": str(pair["same_owner"]).lower(),
            "known_fork_or_mirror": "", "shared_code_evidence": "", "direct_answer_leakage_evidence": "",
            "linked_fix_evidence": "", "review_priority": "FLAME_PRIORITY_REVIEW_REQUIRED" if (pair["target_bug_id"], pair["candidate_doc_id"]) in FLAME_PAIRS else "STANDARD_REVIEW",
            "ai_recommendation": "", "human_decision": "", "human_reason": "",
            "evidence_ids": ";".join(pair["evidence_ids"]),
        })

    target_snapshots = [snapshot_by_id[doc_id] for doc_id in target_ids if doc_id in snapshot_by_id]
    write_jsonl(stage / "phase2_11E_unique_issue_snapshots.jsonl", snapshots)
    write_jsonl(stage / "phase2_11E_target_candidate_pairs.jsonl", pair_rows)
    write_jsonl(stage / "phase2_11E_target_issues.jsonl", target_snapshots)
    write_jsonl(stage / "phase2_11E_linked_evidence.jsonl", evidence)
    queue_path = stage / "phase2_11E_human_adjudication_queue.csv"
    with queue_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(worksheet_rows[0])); writer.writeheader(); writer.writerows(worksheet_rows)

    flame_doc_ids = sorted({doc_id for pair in FLAME_PAIRS for doc_id in pair})
    flame_lines = ["# Phase 2.11E Flame Cross-Repository Priority Review", "",
                   "Status: **REVIEW_REQUIRED**. This packet supplies evidence and makes no leakage or eligibility decision.", "",
                   "All text below is copied verbatim from the local OSSGameBench snapshot. Comments lack timestamps because the source table does not contain them.", "",
                   "## Priority pairs", ""]
    pair_lookup = {(row["target_bug_id"], row["candidate_doc_id"]): row for row in pair_rows}
    for target_id, candidate_id in sorted(FLAME_PAIRS):
        pair = pair_lookup[(target_id, candidate_id)]
        flame_lines.extend([f"- `{target_id}` → `{candidate_id}`", f"  - Group {pair['group']} slot {pair['slot']}"
                            f"; BM25 rank {pair['original_bm25_rank']}",
                            f"  - Evidence IDs: {', '.join(pair['evidence_ids'])}",
                            "  - Relationship status: same owner, different repository; human review required."])
    flame_lines.extend(["", "## Repository relationship evidence", "",
        "Both repositories use the `flame-engine` owner. The local `project.parquet` records both as `is_fork=false`, but it contains no fork-parent, mirror, rename, or transfer fields. The `tiled.dart` README explicitly links examples in `flame-engine/flame/tree/main/packages/flame_tiled`; this is a source-grounded repository reference, not by itself proof of answer leakage. Structured Issue→PR evidence and available commit diffs are recorded in `phase2_11E_linked_evidence.jsonl`.", ""])
    for doc_id in flame_doc_ids:
        snapshot = snapshot_by_id[doc_id]
        flame_lines.extend([f"## `{doc_id}`", "", f"**Title:** {snapshot['title']}", "", "### Full Issue body", "", snapshot["body"], "", "### Available comments", ""])
        if snapshot["available_comments"]:
            for comment in snapshot["available_comments"]:
                flame_lines.extend([f"#### Comment {comment['source_order']} — {comment['author']}", "", comment["body"], ""])
        else:
            flame_lines.extend(["No comments are present in `issue_comment.parquet` for this Issue.", ""])
        flame_lines.extend(["### Evidence references", "", ", ".join(snapshot["all_evidence_ids"]) or "None", ""])
    (stage / "phase2_11E_flame_priority_review.md").write_text("\n".join(flame_lines), encoding="utf-8")

    missing_issue_records = sorted(set(unique_ids) - set(issue_by_id))
    missing_bodies = [row["doc_id"] for row in snapshots if row["body"] == ""]
    duplicate_pair_keys = [key for key, count in Counter((x["target_bug_id"], x["group"], x["slot"], x["candidate_doc_id"]) for x in pair_rows).items() if count > 1]
    reused_candidates = {key: value for key, value in Counter(x["candidate_doc_id"] for x in pair_rows).items() if value > 1}
    flame_covered = sorted([list(pair) for pair in FLAME_PAIRS if pair in pair_lookup])
    phase2_11d_after = {name: sha_file(ROOT / name) for name in phase2_11d_hashes}
    source_unchanged = all(phase2_11d_hashes[name] == phase2_11d_after[name] for name in phase2_11d_hashes)
    mandatory_pass = (
        len(target_snapshots) == 18 and sum(x["group"] == "B" for x in pair_rows) == 54
        and sum(x["group"] == "C" for x in pair_rows) == 54 and len(pair_rows) == 108
        and not missing_issue_records and not missing_bodies and all(x["status"] == "PASS" for x in hash_results)
        and len(flame_covered) == 5 and not duplicate_pair_keys and source_unchanged
    )
    validation = {
        "status": "PASS_EVIDENCE_EXPORT" if mandatory_pass else "BLOCKED_EVIDENCE_EXPORT",
        "counts": {"target_references": len(target_snapshots), "B_candidate_references": sum(x["group"] == "B" for x in pair_rows),
                   "C_candidate_references": sum(x["group"] == "C" for x in pair_rows), "target_candidate_pairs": len(pair_rows),
                   "unique_issue_documents": len(snapshots), "fully_resolved_issue_snapshots": len(snapshots) - len(missing_bodies),
                   "issue_comments": len(comment_rows), "linked_evidence_records": len(evidence)},
        "mandatory_content": {"missing_issue_records": missing_issue_records, "missing_bodies": missing_bodies},
        "comments": {"source_available": True, "documents_with_zero_comments": sum(not x["available_comments"] for x in snapshots),
                     "comments_missing_timestamp": len(comment_rows),
                     "unavailable_metadata_reason": "issue_comment.parquet has no timestamp or stable comment ID columns"},
        "hash_verification": {"passed": sum(x["status"] == "PASS" for x in hash_results),
                              "unresolved": [x for x in hash_results if x["status"] != "PASS"], "records": hash_results},
        "linked_evidence": {"missing_commit_details": missing_commit_details,
                            "missing_linked_evidence_sources": [],
                            "limitations": ["No live GitHub data fetched", "Fork-parent/mirror/rename/transfer fields are absent",
                                            "Short #number textual references are untyped", "Comments have no timestamps",
                                            "Absence of a structured relation is not proof that no relation exists"]},
        "duplicates": {"duplicate_target_candidate_reference_keys": duplicate_pair_keys,
                       "reused_candidate_documents_preserved_as_relationships": reused_candidates},
        "provenance": {"all_snapshots_have_source_provenance": all(x["source_dataset"] and x["source_table"] and x["source_record_id"] for x in snapshots),
                       "bm25_input_fields": ["issue.parquet.title", "issue.parquet.body"],
                       "additional_evidence_not_model_input": True},
        "flame_priority": {"required_pairs": 5, "covered_pairs": len(flame_covered), "pairs": flame_covered},
        "worksheet": {"rows": len(worksheet_rows),
                      "all_ai_recommendations_blank": all(not x["ai_recommendation"] for x in worksheet_rows),
                      "all_human_decisions_blank": all(not x["human_decision"] and not x["human_reason"] for x in worksheet_rows)},
        "phase2_11D_source_hashes": {name: {"before": phase2_11d_hashes[name], "after": phase2_11d_after[name],
                                                   "unchanged": phase2_11d_hashes[name] == phase2_11d_after[name]}
                                      for name in phase2_11d_hashes},
        "phase2_11D_sources_byte_identical": source_unchanged,
        "official_model_calls": 0, "human_review_completed": False, "experiment_frozen": False,
    }
    (stage / "phase2_11E_evidence_export_validation.json").write_text(
        json.dumps(validation, indent=2, ensure_ascii=False, sort_keys=True, default=json_default) + "\n", encoding="utf-8"
    )
    readme = f"""# Phase 2.11E Human Adjudication Evidence Export

Status: **{validation['status']}**. Human review has not been performed. Official generation remains NO-GO.

## Files

- `phase2_11E_unique_issue_snapshots.jsonl`: one full snapshot per unique target/candidate Issue.
- `phase2_11E_target_candidate_pairs.jsonl`: exactly 108 selection relationships; repeated candidate use is intentionally preserved.
- `phase2_11E_target_issues.jsonl`: the 18 full target snapshots.
- `phase2_11E_linked_evidence.jsonl`: dataset-grounded project, textual-reference, Issue→PR, PR comment, commit, and diff evidence.
- `phase2_11E_flame_priority_review.md`: focused evidence for the five Flame pairs.
- `phase2_11E_human_adjudication_queue.csv`: blank human worksheet.
- `phase2_11E_evidence_export_validation.json`: counts, hashes, missing evidence, and integrity checks.

## Lookup

Use `doc_id` in the unique snapshot file. Pair records point to `target_snapshot_doc_id` and `candidate_snapshot_doc_id`. Evidence IDs resolve in the linked-evidence file.

## Retrieval text versus review-only evidence

BM25 used the exact `issue.parquet` title and body assembled as `title + "\\n" + body`. The exported comments, project metadata, PRs, PR comments, commits, and diffs are additional review-only evidence and were not model or retrieval input.

## Limitations

Issue `updated_at` is absent. Issue/PR comment timestamps and stable comment IDs are absent. Project fork-parent, mirror, rename, and transfer metadata are absent. Short `#number` references cannot always be typed as Issue versus PR. No online data was fetched, and missing relations were not inferred from similarity.

The package is ready for human adjudication if validation reports `PASS_EVIDENCE_EXPORT`; that status is structural only and does not approve any candidate.
"""
    (stage / "phase2_11E_review_package_README.md").write_text(readme, encoding="utf-8")
    if not mandatory_pass:
        raise SystemExit(dump_json(validation))

    with zipfile.ZipFile(STAGE_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(stage.iterdir()):
            info = zipfile.ZipInfo(f"phase2_11E_evidence/{path.name}", (2026, 11, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(STAGE_ZIP) as archive:
        bad = archive.testzip()
        if bad or len(archive.namelist()) != 8:
            raise SystemExit(f"ZIP validation failed: bad={bad}, entries={len(archive.namelist())}")
    os.replace(stage, FINAL_DIR)
    os.replace(STAGE_ZIP, FINAL_ZIP)
    print(json.dumps({"status": validation["status"], "output_directory": str(FINAL_DIR),
                      "zip": str(FINAL_ZIP), "zip_sha256": sha_file(FINAL_ZIP),
                      "counts": validation["counts"], "hashes_passed": validation["hash_verification"]["passed"],
                      "flame_pairs_covered": validation["flame_priority"]["covered_pairs"],
                      "missing_commit_details": len(missing_commit_details)}, indent=2))


if __name__ == "__main__":
    main()
