#!/usr/bin/env python3
"""Read-only Phase 2.8 corpus feasibility audit; never builds an index."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pr_number(stable_id: str) -> int:
    return int(stable_id.rsplit("#", 1)[1])


def canonical_sha(raw: str) -> str | None:
    match = re.match(r"^([0-9a-fA-F]{40})(?:\s|$)", raw or "")
    return match.group(1).lower() if match else None


def main() -> None:
    con = duckdb.connect()
    candidates = read_jsonl(ROOT / "data/gold_references_phase2_6_candidate.jsonl")
    leakage = {x["bug_id"]: x for x in read_jsonl(ROOT / "data/leakage_manifest.jsonl")}
    coverage: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []

    for record in candidates:
        bug_id = record["bug_id"]
        project, issue_number_text = bug_id.rsplit("#", 1)
        issue_number = int(issue_number_text)
        cutoff = record["issue_created_at"]
        complete = bool(record["evaluation_targets_v2"]["cspc_eligible_path_ids"])
        leak = leakage[bug_id]
        restricted_prs = {pr_number(x) for x in leak.get("restricted_fixing_prs", [])}
        linked_prs = {row[0] for row in con.execute(
            "SELECT pr_number FROM read_parquet(?) WHERE project=? AND issue_number=?",
            [str(DATASET / "issue_pr.parquet"), project, issue_number],
        ).fetchall()}
        blocked_prs = restricted_prs | linked_prs
        restricted_shas = set(leak.get("restricted_repair_commits", []))
        if blocked_prs:
            placeholders = ",".join("?" for _ in blocked_prs)
            linked_shas = {row[0] for row in con.execute(
                f"SELECT commit_sha FROM read_parquet(?) WHERE project=? AND pr_number IN ({placeholders})",
                [str(DATASET / "pr_commit.parquet"), project, *sorted(blocked_prs)],
            ).fetchall()}
        else:
            linked_shas = set()
        blocked_shas = restricted_shas | linked_shas

        issues_pre = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE project=? AND TRY_CAST(created_at AS TIMESTAMPTZ) < CAST(? AS TIMESTAMPTZ) AND number <> ?",
            [str(DATASET / "issue.parquet"), project, cutoff, issue_number],
        ).fetchone()[0]
        prs_pre = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE project=? AND TRY_CAST(created_at AS TIMESTAMPTZ) < CAST(? AS TIMESTAMPTZ)",
            [str(DATASET / "pr.parquet"), project, cutoff],
        ).fetchone()[0]
        blocked_pre_prs = 0
        if blocked_prs:
            placeholders = ",".join("?" for _ in blocked_prs)
            blocked_pre_prs = con.execute(
                f"SELECT count(*) FROM read_parquet(?) WHERE project=? AND TRY_CAST(created_at AS TIMESTAMPTZ) < CAST(? AS TIMESTAMPTZ) AND number IN ({placeholders})",
                [str(DATASET / "pr.parquet"), project, cutoff, *sorted(blocked_prs)],
            ).fetchone()[0]
        issue_comments = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE project=?",
            [str(DATASET / "issue_comment.parquet"), project],
        ).fetchone()[0]
        pr_comments = con.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE project=?",
            [str(DATASET / "pr_comment.parquet"), project],
        ).fetchone()[0]

        owner, repo = project.split("/", 1)
        commit_file = DATASET / "commits" / f"commits|{owner}|{repo}.parquet"
        commit_rows = con.execute(
            "SELECT sha, created_at, message FROM read_parquet(?) WHERE TRY_CAST(created_at AS TIMESTAMPTZ) < CAST(? AS TIMESTAMPTZ) ORDER BY TRY_CAST(created_at AS TIMESTAMPTZ) DESC, sha",
            [str(commit_file), cutoff],
        ).fetchall()
        reference_re = re.compile(rf"(?<!\d)#\s*{issue_number}(?!\d)", re.IGNORECASE)
        eligible_by_date = []
        excluded_linked = 0
        excluded_text_reference = 0
        malformed_sha = 0
        for raw_sha, created_at, message in commit_rows:
            commit_sha = canonical_sha(raw_sha)
            if commit_sha is None:
                malformed_sha += 1
                continue
            if commit_sha in blocked_shas:
                excluded_linked += 1
                continue
            if reference_re.search(message or ""):
                excluded_text_reference += 1
                continue
            eligible_by_date.append((commit_sha, created_at, message or ""))
        for commit_sha, created_at, message in eligible_by_date[:2]:
            samples.append({
                "bug_id": bug_id,
                "track": "B_IMMUTABLE_COMMIT_SENSITIVITY",
                "document_id": f"{project}@{commit_sha}",
                "source_file": str(commit_file.relative_to(DATASET)),
                "commit_created_at": created_at,
                "cutoff": cutoff,
                "message_sha256": sha(message),
                "message_preview": " ".join(message.split())[:160],
                "content_immutability": "SHA-addressed commit content",
                "public_visibility_at_timestamp": "UNVERIFIED",
                "eligibility": "EXCLUDED_UNVERIFIED_REACHABILITY",
            })

        coverage.append({
            "bug_id": bug_id,
            "cohort": "complete_path_candidate" if complete else "partial_exploratory_candidate",
            "cutoff": cutoff,
            "track_a_historical_issue_pr": {
                "strictly_eligible_documents": 0,
                "pre_cutoff_issue_records_after_target_exclusion": issues_pre,
                "pre_cutoff_pr_records_before_linkage_exclusion": prs_pre,
                "pre_cutoff_blocked_linked_or_repair_prs": blocked_pre_prs,
                "pre_cutoff_pr_records_after_linkage_exclusion": prs_pre - blocked_pre_prs,
                "untimestamped_issue_comments_excluded": issue_comments,
                "untimestamped_pr_comments_excluded": pr_comments,
                "verdict": "NOT_VERIFIABLE",
                "dominant_exclusion_reason": "mutable_text_historical_snapshot_unverified",
            },
            "track_b_immutable_commit_sensitivity": {
                "strictly_eligible_documents": 0,
                "pre_cutoff_commits_before_exclusions": len(commit_rows),
                "linked_or_repair_commits_excluded": excluded_linked,
                "target_issue_text_reference_commits_excluded": excluded_text_reference,
                "malformed_or_noncanonical_sha_rows_excluded": malformed_sha,
                "date_filtered_candidates_pending_visibility_proof": len(eligible_by_date),
                "verdict": "NOT_VERIFIABLE",
                "dominant_exclusion_reason": "commit_public_visibility_or_reachability_time_unverified",
            },
            "human_decision": None,
        })

    coverage_path = ROOT / "data/phase2_8_per_bug_corpus_coverage.jsonl"
    coverage_path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in coverage), encoding="utf-8")
    manifest = {
        "manifest_version": "phase2.8-candidate-metadata-only-1",
        "status": "NOT_APPROVED_NOT_INDEXABLE",
        "contains_model_input": False,
        "official_index_created": False,
        "sample_count": len(samples),
        "samples": samples,
    }
    (ROOT / "data/phase2_8_candidate_corpus_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    complete_rows = [x for x in coverage if x["cohort"] == "complete_path_candidate"]
    summary = {
        "records": len(coverage),
        "complete_path_records": len(complete_rows),
        "partial_records": len(coverage) - len(complete_rows),
        "track_a_strictly_eligible_total": sum(x["track_a_historical_issue_pr"]["strictly_eligible_documents"] for x in coverage),
        "track_b_strictly_eligible_total": sum(x["track_b_immutable_commit_sensitivity"]["strictly_eligible_documents"] for x in coverage),
        "complete_targets_with_at_least_5_track_a_eligible": sum(x["track_a_historical_issue_pr"]["strictly_eligible_documents"] >= 5 for x in complete_rows),
        "complete_targets_with_at_least_5_track_b_eligible": sum(x["track_b_immutable_commit_sensitivity"]["strictly_eligible_documents"] >= 5 for x in complete_rows),
        "track_a_verdict": "NOT_VERIFIABLE",
        "track_b_verdict": "NOT_VERIFIABLE",
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
