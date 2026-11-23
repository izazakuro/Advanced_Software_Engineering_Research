#!/usr/bin/env python3
"""Independently verify a Gold v1 snapshot using only files inside it."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from collections import Counter
from pathlib import Path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def verify(snapshot: Path) -> dict:
    errors: list[str] = []
    manifest_path = snapshot / "gold_v1_freeze_manifest.json"
    if not manifest_path.is_file():
        return {"status": "FAIL", "errors": ["freeze manifest missing"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    inventory_results: list[dict] = []
    for artifact in manifest.get("frozen_artifacts", []):
        path = snapshot / artifact["frozen_filename"]
        actual = sha(path) if path.is_file() else None
        passed = actual == artifact["sha256"]
        inventory_results.append({"artifact_id": artifact["artifact_id"], "filename": artifact["frozen_filename"],
                                  "expected_sha256": artifact["sha256"], "actual_sha256": actual, "passed": passed})
        if not passed:
            errors.append(f"artifact hash mismatch: {artifact['frozen_filename']}")

    gold = jsonl(snapshot / manifest["gold"]["frozen_filename"])
    ids = [item["bug_id"] for item in gold]
    if len(gold) != 18 or len(set(ids)) != 18:
        errors.append("Gold does not contain 18 unique records")
    if sha(snapshot / manifest["gold"]["frozen_filename"]) != "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66":
        errors.append("authorized Gold hash mismatch")
    if ids != manifest["gold"]["bug_ids_in_file_order"]:
        errors.append("manifest Gold ID order mismatch")
    if any(item.get("gold_frozen") is not False for item in gold):
        errors.append("Gold content was modified to mark a record frozen")

    rubric = json.loads((snapshot / manifest["rubric"]["frozen_filename"]).read_text(encoding="utf-8"))
    dispositions = Counter(item["strict_scoring_disposition"] for item in rubric["entries"])
    if dispositions != Counter({"INCLUDED": 10, "EXCLUDED_INELIGIBLE": 7, "EXCLUDED_PENDING": 2}):
        errors.append(f"strict Oracle disposition mismatch: {dict(dispositions)}")
    pending = [item for item in rubric["entries"] if item["strict_scoring_disposition"] == "EXCLUDED_PENDING"]
    if {item["bug_id"] for item in pending} != {"tmodloader/tmodloader#2644", "vcmi/vcmi#5462"}:
        errors.append("pending strict Oracle IDs mismatch")
    if any(item["underlying_gold_classification"] != "pending_rubric_decision" for item in pending):
        errors.append("pending strict Oracle classification changed")

    metric = json.loads((snapshot / manifest["metric_eligibility"]["frozen_filename"]).read_text(encoding="utf-8"))
    expected_counts = {"tcc_bugs": 18, "tcc_paths": 19, "tcc_required_condition_denominator": 24,
                       "cspc_bugs": 11, "cspc_paths": 12, "strict_oracle_entries": 10,
                       "strict_oracle_bugs": 9, "strict_pending_excluded": 2,
                       "strict_ineligible_incomplete_excluded": 7, "scenario_adequacy_bugs": 18}
    if metric.get("frozen_metric_scopes") != expected_counts:
        errors.append(f"metric eligibility mismatch: {metric.get('frozen_metric_scopes')}")
    if metric.get("pending_strict_oracles") != ["tmodloader/tmodloader#2644::O1", "vcmi/vcmi#5462::O1"]:
        errors.append("metric pending Oracle list mismatch")

    acceptance = json.loads((snapshot / manifest["human_acceptance"]["frozen_filename"]).read_text(encoding="utf-8"))
    if acceptance.get("corrected_gold_acceptance_count") != 9 or acceptance.get("human_freeze_authorization") is not False:
        errors.append("pre-freeze acceptance artifact mismatch")
    authorization = (snapshot / manifest["human_authorization"]["frozen_filename"]).read_text(encoding="utf-8")
    if "explicitly authorized Gold v1 Freeze" not in authorization or "Phase 3, LLM calls, RAG/BM25 indexing, and experiments are NOT authorized" not in authorization:
        errors.append("human freeze authorization text missing or incomplete")

    blockers = json.loads((snapshot / "phase2_11F_4_lite_blockers.json").read_text(encoding="utf-8"))
    if blockers.get("blocker_count") != 0:
        errors.append("unresolved engineering freeze blocker")
    if not (manifest.get("freeze_authorized") is True and manifest.get("gold_frozen") is True
            and manifest.get("freeze_status") == "FROZEN_VERIFIED"):
        errors.append("manifest is not finalized as FROZEN_VERIFIED")
    if manifest.get("phase3_experiments_authorized") is not False or manifest.get("official_model_calls") != 0:
        errors.append("Phase 3/model-call governance violation")

    # Load the copied serializer and copied projection with no mutable workspace access.
    serializer_path = snapshot / manifest["generation_boundary"]["serializer_filename"]
    spec = importlib.util.spec_from_file_location("frozen_gold_v1_serializer", serializer_path)
    if spec is None or spec.loader is None:
        errors.append("copied serializer cannot be loaded")
        projection_count = None
    else:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        try:
            projection = module.load_safe_projection(snapshot / manifest["generation_boundary"]["safe_projection_filename"])
            projection_count = len(projection)
            sample_target = projection[ids[0]]
            sample_context = module.serialize_context_issue({"doc_id": "synthetic/context#1", "title": "safe title", "body": "safe body"})
            rendered = module.render_abc_prompt("{{TARGET_TITLE}}\n{{TARGET_BODY}}\n{{ADDITIONAL_CONTEXT_OR_NONE}}", sample_target, [sample_context])
            if "safe title" not in rendered or "safe body" not in rendered:
                errors.append("frozen generation boundary render check failed")
        except Exception as exc:  # validation must convert failures to a clear result
            projection_count = None
            errors.append(f"frozen generation boundary cannot be reproduced: {exc}")

    return {
        "status": "PASS_GOLD_V1_FROZEN_SNAPSHOT" if not errors else "FAIL_GOLD_V1_FROZEN_SNAPSHOT",
        "snapshot_directory": str(snapshot.resolve()),
        "manifest_sha256": sha(manifest_path),
        "gold_sha256": sha(snapshot / manifest["gold"]["frozen_filename"]),
        "record_count": len(gold), "unique_bug_ids": len(set(ids)),
        "strict_oracle_dispositions": dict(dispositions),
        "metric_scopes_verified": not any("metric eligibility" in item for item in errors),
        "generation_boundary_projection_records": projection_count,
        "snapshot_loadable_without_mutable_working_files": projection_count == 18,
        "freeze_authorized": manifest.get("freeze_authorized"),
        "gold_frozen": manifest.get("gold_frozen"),
        "phase3_experiments_authorized": manifest.get("phase3_experiments_authorized"),
        "official_model_calls": manifest.get("official_model_calls"),
        "artifact_hash_results": inventory_results,
        "errors": errors,
    }


def write_reports(snapshot: Path, result: dict) -> None:
    (snapshot / "gold_v1_freeze_validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = f"""# Gold v1 Freeze Execution Report

## Result

**{result['status']}**

- Gold SHA-256: `{result['gold_sha256']}`
- Records: {result['record_count']} ({result['unique_bug_ids']} unique Bug IDs)
- Freeze authorized: {str(result['freeze_authorized']).lower()}
- Gold frozen by manifest: {str(result['gold_frozen']).lower()}
- Snapshot independently loadable: {str(result['snapshot_loadable_without_mutable_working_files']).lower()}
- Phase 3 authorized: {str(result['phase3_experiments_authorized']).lower()}
- Official model calls: {result['official_model_calls']}

Strict Oracle: 10 included, 2 pending excluded, 7 ineligible/incomplete excluded. Pending labels remain unchanged.

The Gold JSONL itself was not modified to mark records frozen. Freeze state exists only in the verified manifest. No generation, evaluation, RAG/BM25 indexing, or Phase 3 work was performed.

Errors: {json.dumps(result['errors'], ensure_ascii=False)}
"""
    (snapshot / "gold_v1_freeze_execution_report.md").write_text(report, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--seal", action="store_true")
    args = parser.parse_args()
    result = verify(args.snapshot.resolve())
    if args.seal:
        result["snapshot_sealed_read_only"] = result["status"] == "PASS_GOLD_V1_FROZEN_SNAPSHOT"
    if not args.check_only:
        write_reports(args.snapshot.resolve(), result)
    if args.seal and result["status"] == "PASS_GOLD_V1_FROZEN_SNAPSHOT":
        for path in args.snapshot.resolve().iterdir():
            if path.is_file():
                path.chmod(0o444)
        args.snapshot.resolve().chmod(0o555)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS_GOLD_V1_FROZEN_SNAPSHOT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
