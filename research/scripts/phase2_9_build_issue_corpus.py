#!/usr/bin/env python3
"""Build the Phase 2.9 retrospective Issue-only candidate corpus."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from phase2_9_abc_common import normalize, sha256, tokens


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"
DB_PATH = ROOT / "data/phase2_9_abc_issue_corpus.sqlite"
MANIFEST_PATH = ROOT / "data/phase2_9_abc_candidate_issue_corpus_manifest.jsonl"
EXCLUSION_PATH = ROOT / "data/phase2_9_abc_exclusion_log.jsonl"
TARGET_PATH = ROOT / "data/phase2_9_abc_target_issue_inputs.jsonl"


def jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    gold = jsonl(ROOT / "data/gold_references_phase2_6_candidate.jsonl")
    leakage = {x["bug_id"]: x for x in jsonl(ROOT / "data/leakage_manifest.jsonl")}
    target_ids = {x["bug_id"] for x in gold}
    sensitive_numbers: dict[str, set[int]] = defaultdict(set)
    for record in gold:
        project, number = record["bug_id"].rsplit("#", 1)
        sensitive_numbers[project].add(int(number))
        for pr_id in leakage[record["bug_id"]].get("restricted_fixing_prs", []):
            pr_project, pr_num = pr_id.rsplit("#", 1)
            sensitive_numbers[pr_project].add(int(pr_num))

    source = duckdb.connect()
    target_rows = source.execute(
        "SELECT project, number, title, body, created_at FROM read_parquet(?) WHERE project || '#' || CAST(number AS VARCHAR) IN (SELECT unnest(?)) ORDER BY project, number",
        [str(DATASET / "issue.parquet"), sorted(target_ids)],
    ).fetchall()
    target_inputs = []
    target_normalized_hashes = set()
    target_raw_hashes = set()
    for project, number, title, body, created_at in target_rows:
        title, body = title or "", body or ""
        text = f"{title}\n{body}"
        target_inputs.append({"bug_id": f"{project}#{number}", "project": project, "number": number,
                              "title": title, "body": body, "created_at": created_at,
                              "source_file": "issue.parquet", "content_sha256": sha256(text),
                              "normalized_content_sha256": sha256(normalize(text)),
                              "temporal_interpretation": "retrospective_current_dataset_snapshot_not_creation_time"})
        target_raw_hashes.add(sha256(text))
        target_normalized_hashes.add(sha256(normalize(text)))
    if len(target_inputs) != 18:
        raise SystemExit(f"expected 18 target Issue rows, got {len(target_inputs)}")
    TARGET_PATH.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in target_inputs), encoding="utf-8")

    tmp_db = DB_PATH.with_suffix(".sqlite.tmp")
    if tmp_db.exists():
        tmp_db.unlink()
    db = sqlite3.connect(tmp_db)
    db.execute("CREATE TABLE documents (doc_id TEXT PRIMARY KEY, project TEXT NOT NULL, issue_number INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT, normalized_text TEXT NOT NULL, content_sha256 TEXT NOT NULL, normalized_sha256 TEXT NOT NULL, token_count INTEGER NOT NULL, source_row_identity TEXT NOT NULL)")
    db.execute("CREATE INDEX documents_length ON documents(token_count)")
    counts = Counter()
    seen_ids: set[str] = set()
    first_raw_hash: dict[str, str] = {}
    first_normalized_hash: dict[str, str] = {}
    manifest_file = MANIFEST_PATH.open("w", encoding="utf-8")
    exclusion_file = EXCLUSION_PATH.open("w", encoding="utf-8")
    rows = source.execute("SELECT project, number, title, body, created_at FROM read_parquet(?) ORDER BY project, number", [str(DATASET / "issue.parquet")])
    batch_insert = []
    source_index = 0
    while batch := rows.fetchmany(5000):
        for project, number, title, body, created_at in batch:
            source_index += 1
            reasons: list[str] = []
            project = project or ""
            title, body = title or "", body or ""
            doc_id = f"{project}#{number}" if project and number is not None else f"MALFORMED_ROW_{source_index}"
            raw_text = f"{title}\n{body}"
            norm = normalize(raw_text)
            raw_hash, norm_hash = sha256(raw_text), sha256(norm)
            token_count = len(tokens(norm))
            if not project or number is None:
                reasons.append("malformed_missing_stable_identity")
            if doc_id in seen_ids:
                reasons.append("duplicate_stable_issue_id")
            seen_ids.add(doc_id)
            if not norm:
                reasons.append("empty_or_unusable_text")
            if doc_id in target_ids:
                reasons.append("selected_target_issue")
            elif raw_hash in target_raw_hashes:
                reasons.append("exact_target_content_duplicate")
            elif norm_hash in target_normalized_hashes:
                reasons.append("normalized_target_content_duplicate")
            if not reasons and project in sensitive_numbers:
                for sensitive in sorted(sensitive_numbers[project]):
                    short_ref = re.compile(rf"(?<!\d)#\s*{sensitive}(?!\d)", re.I)
                    url_ref = re.compile(rf"github\.com/{re.escape(project)}/(?:issues|pull)/{sensitive}(?!\d)", re.I)
                    if short_ref.search(raw_text) or url_ref.search(raw_text):
                        reasons.append(f"direct_target_or_repair_reference:{project}#{sensitive}")
                        break
            if not reasons and raw_hash in first_raw_hash:
                reasons.append(f"exact_duplicate_of:{first_raw_hash[raw_hash]}")
            if not reasons and norm_hash in first_normalized_hash:
                reasons.append(f"normalized_duplicate_of:{first_normalized_hash[norm_hash]}")
            first_raw_hash.setdefault(raw_hash, doc_id)
            first_normalized_hash.setdefault(norm_hash, doc_id)
            status = "ELIGIBLE_CANDIDATE" if not reasons else "EXCLUDED"
            manifest = {"doc_id": doc_id, "project": project, "issue_number": number,
                        "source_file": "issue.parquet", "source_row_identity": doc_id,
                        "created_at": created_at,
                        "content_sha256": raw_hash, "normalized_content_sha256": norm_hash,
                        "token_count": token_count, "status": status, "exclusion_reasons": reasons}
            manifest_file.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n")
            counts[status] += 1
            for reason in reasons:
                counts[f"reason:{reason.split(':', 1)[0]}"] += 1
            if reasons:
                exclusion_file.write(json.dumps({"doc_id": doc_id, "reasons": reasons,
                                                 "content_sha256": raw_hash}, sort_keys=True) + "\n")
            else:
                batch_insert.append((doc_id, project, int(number), title, body, created_at, norm, raw_hash, norm_hash, token_count, doc_id))
            if len(batch_insert) >= 5000:
                db.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch_insert)
                db.commit()
                batch_insert.clear()
    if batch_insert:
        db.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?,?)", batch_insert)
    db.commit()
    db.execute("ANALYZE")
    db.close()
    manifest_file.close()
    exclusion_file.close()
    os.replace(tmp_db, DB_PATH)
    summary = {"source_rows": source_index, "target_rows": len(target_inputs),
               "eligible_documents": counts["ELIGIBLE_CANDIDATE"],
               "excluded_documents": counts["EXCLUDED"],
               "exclusion_counts": {k.removeprefix("reason:"): v for k, v in sorted(counts.items()) if k.startswith("reason:")},
               "model_input_sources": ["issue.parquet:title", "issue.parquet:body"],
               "pr_commit_model_inputs": 0, "official_index": False}
    (ROOT / "data/phase2_9_abc_corpus_build_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
