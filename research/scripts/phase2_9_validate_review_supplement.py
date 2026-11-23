#!/usr/bin/env python3
"""Independently validate the Phase 2.9 human-review supplement ZIP."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path

from phase2_9_abc_common import normalize, sha256


ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "phase2_9_review_supplement.zip"
PREFIX = "phase2_9_review_supplement/"


def lines(data: bytes) -> list[dict]:
    return [json.loads(x) for x in data.decode("utf-8").splitlines() if x.strip()]


def main() -> int:
    errors: list[str] = []
    with zipfile.ZipFile(ZIP_PATH) as archive:
        names = set(archive.namelist())
        def read(name: str) -> bytes:
            return archive.read(PREFIX + name)
        manifest = json.loads(read("manifest.json"))
        validation = json.loads(read("validation_report.json"))
        targets = lines(read("exports/target_issues_full.jsonl"))
        top10 = lines(read("exports/bm25_top10_candidates_full.jsonl"))
        group_b = lines(read("exports/group_b_proposed_context_full.jsonl"))
        group_c = lines(read("exports/group_c_proposed_context_full.jsonl"))
        c_outside = lines(read("exports/group_c_outside_target_top10_full.jsonl"))
        evidence = lines(read("exports/gold_evidence_full_original_fields.jsonl"))
        repair = lines(read("exports/gold_repair_commit_availability.jsonl"))
        expected_counts = (len(targets), len(top10), len(group_b), len(group_c), len(c_outside), len(evidence))
        if expected_counts != (18, 180, 54, 54, 54, 135):
            errors.append(f"record counts mismatch: {expected_counts}")
        for label, rows in (("targets", targets), ("top10", top10), ("group_b", group_b), ("group_c", group_c)):
            for row in rows:
                if "title" not in row or "body" not in row:
                    errors.append(f"{label}: missing original text fields for {row.get('doc_id')}")
                    continue
                content_hash = sha256(f"{row['title']}\n{row['body']}")
                if content_hash != row["content_sha256"]:
                    errors.append(f"{label}: content hash mismatch for {row['doc_id']}")
                if sha256(normalize(f"{row['title']}\n{row['body']}")) != row["normalized_content_sha256"]:
                    errors.append(f"{label}: normalized hash mismatch for {row['doc_id']}")
        if any(not x["content_hash_matches_manifest"] for x in targets + top10 + group_c):
            errors.append("source text differs from selection manifest hash")
        if any(x.get("human_approval") is not None for x in group_b + group_c):
            errors.append("human approval field was modified")
        if any(x["in_target_bm25_top10"] for x in group_c):
            errors.append("Group C outside-Top10 classification mismatch")
        if {(x["target_bug_id"], x["doc_id"]) for x in group_c} != {(x["target_bug_id"], x["doc_id"]) for x in c_outside}:
            errors.append("separate Group C outside-Top10 export is incomplete")
        if any(not x["resolved_exact_excerpt"] for x in evidence):
            errors.append("unresolved Gold evidence item")
        if sum(not x["found_in_local_dataset"] for x in repair) != 38:
            errors.append("missing repair commit count mismatch")
        if validation.get("status") != "PASS":
            errors.append("embedded validation did not pass")
        for entry in manifest.get("files", []):
            member = PREFIX + entry["path"]
            if member not in names:
                errors.append(f"manifest member missing: {entry['path']}")
                continue
            data = archive.read(member)
            if len(data) != entry["size_bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                errors.append(f"manifest hash/size mismatch: {entry['path']}")
        required = {
            PREFIX + "reference/test_scenario_evaluation_rubric_abc_PROPOSED.md",
            PREFIX + "reference/phase2_9_abc_experiment_protocol_PROPOSED.md",
            PREFIX + "reference/gold_references_phase2_6_candidate.jsonl",
        }
        if not required <= names:
            errors.append("required rubric/protocol/Gold reference missing")
        if hashlib.sha256(read("reference/gold_references_phase2_6_candidate.jsonl")).hexdigest() != hashlib.sha256((ROOT / "data/gold_references_phase2_6_candidate.jsonl").read_bytes()).hexdigest():
            errors.append("packaged Gold file differs from original")
        forbidden_fragments = ("issue_corpus.sqlite", "candidate_issue_corpus_manifest", "official_output", "official_score")
        if any(any(fragment in name for fragment in forbidden_fragments) for name in names):
            errors.append("package contains full corpus or official experiment artifact")
    result = {"status": "PASS" if not errors else "FAIL", "zip": str(ZIP_PATH),
              "zip_sha256": hashlib.sha256(ZIP_PATH.read_bytes()).hexdigest(),
              "zip_size_bytes": ZIP_PATH.stat().st_size, "target_records": len(targets),
              "top10_records": len(top10), "group_b_records": len(group_b), "group_c_records": len(group_c),
              "group_c_outside_top10_records": len(c_outside), "gold_evidence_items": len(evidence),
              "unresolved_gold_evidence": sum(not x["resolved_exact_excerpt"] for x in evidence),
              "repair_commits_missing_locally": sum(not x["found_in_local_dataset"] for x in repair),
              "errors": errors}
    print(json.dumps(result, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
