#!/usr/bin/env python3
"""Build and validate the Phase 2.11 human-adjudication ZIP package."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "phase2_11_adjudication_package.zip"
PACKAGE_ROOT = "phase2_11_adjudication_package"
SUPPLEMENT = ROOT / "phase2_9_review_supplement.zip"
SUPPLEMENT_ROOT = "phase2_9_review_supplement"
FIXED_TIME = (2026, 11, 18, 0, 0, 0)


LOCAL_FILES = {
    "phase2_10B/phase2_10B_eligibility_and_leakage_decision_queue.csv": ROOT / "data/phase2_10B_eligibility_and_leakage_decision_queue.csv",
    "phase2_10B/phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl": ROOT / "data/phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl",
    "phase2_10B/phase2_10B_readiness_report.md": ROOT / "reports/phase2_10B_readiness_report.md",
    "phase2_10B/phase2_10B_experiment_config_PROPOSED.json": ROOT / "data/phase2_10B_experiment_config_PROPOSED.json",
}

SUPPLEMENT_FILES = {
    "phase2_9_issue_text/bm25_top10_candidates_full.jsonl": "exports/bm25_top10_candidates_full.jsonl",
    "phase2_9_issue_text/group_c_proposed_context_full.jsonl": "exports/group_c_proposed_context_full.jsonl",
    "phase2_9_issue_text/target_issues_full.jsonl": "exports/target_issues_full.jsonl",
    "gold/gold_references_phase2_6_candidate.jsonl": "reference/gold_references_phase2_6_candidate.jsonl",
    "gold/gold_references_draft.jsonl": "reference/gold_references_draft.jsonl",
    "gold/gold_references_refined_draft.jsonl": "reference/gold_references_refined_draft.jsonl",
    "gold/gold_evidence_full_original_fields.jsonl": "exports/gold_evidence_full_original_fields.jsonl",
    "gold/gold_source_records_full.jsonl": "exports/gold_source_records_full.jsonl",
    "gold/gold_repair_commit_availability.jsonl": "exports/gold_repair_commit_availability.jsonl",
    "gold/gold_unresolved_evidence.jsonl": "exports/gold_unresolved_evidence.jsonl",
    "gold/phase2_9_abc_gold_adjudication_workbook.md": "reference/phase2_9_abc_gold_adjudication_workbook.md",
    "reference/test_scenario_evaluation_rubric_abc_PROPOSED.md": "reference/test_scenario_evaluation_rubric_abc_PROPOSED.md",
    "reference/phase2_9_abc_leakage_policy_PROPOSED.json": "reference/phase2_9_abc_leakage_policy_PROPOSED.json",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jsonl(data: bytes) -> list[dict]:
    return [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]


def add_bytes(archive: zipfile.ZipFile, relative: str, data: bytes) -> None:
    info = zipfile.ZipInfo(f"{PACKAGE_ROOT}/{relative}", FIXED_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, data)


def main() -> None:
    payload: dict[str, bytes] = {name: path.read_bytes() for name, path in LOCAL_FILES.items()}
    with zipfile.ZipFile(SUPPLEMENT) as source:
        for target_name, source_name in SUPPLEMENT_FILES.items():
            payload[target_name] = source.read(f"{SUPPLEMENT_ROOT}/{source_name}")

    queue = list(csv.DictReader(io.StringIO(payload["phase2_10B/phase2_10B_eligibility_and_leakage_decision_queue.csv"].decode("utf-8"))))
    manifest_records = jsonl(payload["phase2_10B/phase2_10B_validated_candidate_manifest_NOT_FROZEN.jsonl"])
    top10 = jsonl(payload["phase2_9_issue_text/bm25_top10_candidates_full.jsonl"])
    group_c = jsonl(payload["phase2_9_issue_text/group_c_proposed_context_full.jsonl"])
    targets = jsonl(payload["phase2_9_issue_text/target_issues_full.jsonl"])
    gold = jsonl(payload["gold/gold_references_phase2_6_candidate.jsonl"])
    gold_evidence = jsonl(payload["gold/gold_evidence_full_original_fields.jsonl"])
    gold_sources = jsonl(payload["gold/gold_source_records_full.jsonl"])

    checks = {
        "phase2_10B_queue_234": len(queue) == 234,
        "phase2_10B_manifest_18": len(manifest_records) == 18,
        "all_queue_human_decisions_blank": all(not row["human_eligibility_decision"] for row in queue),
        "bm25_top10_full_180": len(top10) == 180,
        "group_c_full_54": len(group_c) == 54,
        "target_issues_full_18": len(targets) == 18,
        "all_issue_titles_and_bodies_present": all(row.get("title") and row.get("body") for row in top10 + group_c + targets),
        "gold_candidate_records_18": len(gold) == 18,
        "gold_evidence_items_135": len(gold_evidence) == 135,
        "gold_source_records_44": len(gold_sources) == 44,
        "gold_unresolved_evidence_empty": payload["gold/gold_unresolved_evidence.jsonl"] == b"",
        "abc_rubric_included": "ABC" in payload["reference/test_scenario_evaluation_rubric_abc_PROPOSED.md"].decode("utf-8"),
        "no_official_model_outputs": True,
        "no_full_corpus": True,
    }
    validation = {
        "status": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "counts": {"phase2_10B_queue": len(queue), "phase2_10B_manifest_targets": len(manifest_records),
                   "bm25_candidate_records": len(top10), "group_c_candidate_records": len(group_c),
                   "target_issue_records": len(targets), "gold_records": len(gold),
                   "gold_evidence_items": len(gold_evidence), "gold_unique_source_records": len(gold_sources)},
        "limitations": {"current_snapshot_not_historical_issue_text": True,
                        "repair_commits_missing_locally_are_reported_not_invented": True,
                        "semantic_correctness_not_established": True},
    }
    if validation["status"] != "PASS":
        raise SystemExit(json.dumps(validation, indent=2))

    readme = """# Phase 2.11 Human Adjudication Package

Status: HUMAN REVIEW ONLY / NOT FROZEN / NO OFFICIAL GENERATION.

This package combines the requested Phase 2.10B decision artifacts with full Phase 2.9 target, BM25 Top-10, and Group C Issue text; Gold drafts and original supporting evidence; the Gold adjudication workbook; the proposed ABC rubric; and the eligibility policy.

Issue text is from the current OSSGameBench dataset snapshot, not verified creation-time text. Gold PR/commit evidence is evaluation-only and must never be included in model input. AI recommendations are advisory. Human approval fields remain blank.

See `manifest.json` for byte hashes and `validation_report.json` for record counts and checks.
""".encode("utf-8")
    payload["README.md"] = readme
    payload["validation_report.json"] = (json.dumps(validation, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    manifest = {
        "package": "phase2_11_adjudication_package",
        "status": "HUMAN_REVIEW_ONLY_NOT_FROZEN",
        "source_supplement_sha256": sha256(SUPPLEMENT.read_bytes()),
        "counts": validation["counts"],
        "files": [{"path": name, "size_bytes": len(data), "sha256": sha256(data)} for name, data in sorted(payload.items())],
        "human_approvals_modified": False,
        "official_generation_included": False,
    }
    payload["manifest.json"] = (json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

    with zipfile.ZipFile(OUTPUT, "w") as archive:
        for name, data in sorted(payload.items()):
            add_bytes(archive, name, data)

    with zipfile.ZipFile(OUTPUT) as archive:
        bad = archive.testzip()
        names = archive.namelist()
        if bad or len(names) != len(payload) or len(names) != len(set(names)):
            raise SystemExit(f"ZIP validation failed: bad={bad}, entries={len(names)}, unique={len(set(names))}")
    print(json.dumps({"status": "PASS", "output": str(OUTPUT), "size_bytes": OUTPUT.stat().st_size,
                      "sha256": sha256(OUTPUT.read_bytes()), "entries": len(payload), "counts": validation["counts"]}, indent=2))


if __name__ == "__main__":
    main()
