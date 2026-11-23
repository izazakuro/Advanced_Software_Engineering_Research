#!/usr/bin/env python3
"""Read-only schema audit for time-safe BM25 readiness; builds no corpus."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"
TABLES = ("issue.parquet", "issue_comment.parquet", "issue_pr.parquet", "pr.parquet",
          "pr_comment.parquet", "pr_commit.parquet", "commit_metadata.parquet")


def main() -> None:
    con = duckdb.connect()
    schemas = {}
    for name in TABLES:
        rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{DATASET / name}')").fetchall()
        schemas[name] = [{"name": row[0], "type": row[1]} for row in rows]
    columns = {name: {x["name"] for x in fields} for name, fields in schemas.items()}
    gates = {
        "issue_creation_time_present": "created_at" in columns["issue.parquet"],
        "pr_creation_time_present": "created_at" in columns["pr.parquet"],
        "issue_body_version_or_update_time_present": bool({"updated_at", "body_versions", "body_version_timestamp"} & columns["issue.parquet"]),
        "pr_body_version_or_update_time_present": bool({"updated_at", "body_versions", "body_version_timestamp"} & columns["pr.parquet"]),
        "issue_comment_time_present": bool({"created_at", "updated_at"} & columns["issue_comment.parquet"]),
        "pr_comment_time_present": bool({"created_at", "updated_at"} & columns["pr_comment.parquet"]),
    }
    result = {"status": "FAIL_CLOSED_NO_OFFICIAL_INDEX", "dataset": str(DATASET),
              "scope": "schema/metadata only; no study-bug retrieval and no generation",
              "schemas": schemas, "temporal_gates": gates,
              "conclusion": "Creation times do not prove historical body contents; comments lack timestamps. Real text is ineligible until historical snapshots/content timestamps are independently verified."}
    output = ROOT / "data/phase2_7_bm25_source_schema_audit.json"
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
