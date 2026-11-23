#!/usr/bin/env python3
"""Execute the explicitly authorized Gold v1 freeze without modifying Gold content."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from verify_gold_v1_frozen_snapshot import verify

ROOT = Path(__file__).resolve().parents[1]
FINAL = ROOT / "outputs/gold_v1_frozen"
AUTHORIZED_GOLD_SHA = "0242d3ba61fa94dcc086bb2fe39861e1d6dcefea0b415fc7f10daf6d16e57f66"
AUTHORIZATION = Path("/Users/zakuro/.codex/attachments/19ba44de-dcfe-4d8f-a43f-21412aa93c50/貼り付けたテキスト.txt")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def display_source(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main() -> None:
    if FINAL.exists():
        result = verify(FINAL)
        print(json.dumps({"status": "EXISTING_SNAPSHOT_VERIFIED_NO_OVERWRITE" if not result["errors"] else "CONFLICT",
                          "verification": result}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if not result["errors"] else 1)

    prep = ROOT / "outputs/gold_v1_freeze_preparation"
    gold_source = ROOT / "outputs/phase2_11F_3/gold_references_temporal_corrected.jsonl"
    rubric_source = prep / "strict_oracle_rubric_v1.json"
    acceptance_source = prep / "gold_v1_human_acceptance_decisions.json"
    draft_source = prep / "gold_v1_freeze_manifest_draft.json"
    allowlist_source = ROOT / "outputs/phase2_11F_3/generation_input_allowlist.json"
    serializer_source = ROOT / "scripts/phase2_11F_3_generation_input.py"
    projection_source = ROOT / "outputs/phase2_11F_3/generation_input_safe_projection.jsonl"
    blockers_source = ROOT / "outputs/phase2_11F_4_lite/f4_lite_blockers.json"

    gold = jsonl(gold_source)
    rubric = json.loads(rubric_source.read_text(encoding="utf-8"))
    acceptance = json.loads(acceptance_source.read_text(encoding="utf-8"))
    draft = json.loads(draft_source.read_text(encoding="utf-8"))
    blockers = json.loads(blockers_source.read_text(encoding="utf-8"))
    authorization_text = AUTHORIZATION.read_text(encoding="utf-8")
    pre_errors: list[str] = []
    if sha(gold_source) != AUTHORIZED_GOLD_SHA:
        pre_errors.append("authorized Gold SHA-256 mismatch")
    if len(gold) != 18 or len({item["bug_id"] for item in gold}) != 18:
        pre_errors.append("Gold does not contain 18 unique records")
    if acceptance.get("corrected_gold_acceptance_count") != 9:
        pre_errors.append("nine corrected Gold acceptances missing")
    if acceptance.get("final_verify_preserved", {}).get("bug_id") != "CorsixTH/CorsixTH#1799":
        pre_errors.append("CorsixTH#1799 FINAL_VERIFY missing")
    if blockers.get("blocker_count") != 0:
        pre_errors.append("unresolved engineering blocker exists")
    dispositions = Counter(item["strict_scoring_disposition"] for item in rubric["entries"])
    if dispositions != Counter({"INCLUDED": 10, "EXCLUDED_INELIGIBLE": 7, "EXCLUDED_PENDING": 2}):
        pre_errors.append("Rubric disposition counts mismatch")
    pending = [item for item in rubric["entries"] if item["strict_scoring_disposition"] == "EXCLUDED_PENDING"]
    if {item["bug_id"] for item in pending} != {"tmodloader/tmodloader#2644", "vcmi/vcmi#5462"}:
        pre_errors.append("pending Oracle IDs mismatch")
    if any(item["underlying_gold_classification"] != "pending_rubric_decision" for item in pending):
        pre_errors.append("pending Oracle label was changed")
    if draft["candidate_gold"]["sha256"] != AUTHORIZED_GOLD_SHA:
        pre_errors.append("draft manifest Gold hash mismatch")
    if draft["generation_boundary"]["serializer_sha256"] != sha(serializer_source):
        pre_errors.append("serializer differs from validated preparation version")
    if draft["generation_boundary"]["safe_projection_sha256"] != sha(projection_source):
        pre_errors.append("safe projection differs from validated preparation version")
    if "explicitly authorized Gold v1 Freeze" not in authorization_text:
        pre_errors.append("explicit freeze authorization missing")
    if "Phase 3, LLM calls, RAG/BM25 indexing, and experiments are NOT authorized" not in authorization_text:
        pre_errors.append("Phase 3 prohibition missing from authorization")
    if any(item.get("gold_frozen") is not False for item in gold):
        pre_errors.append("Gold content has unexpected frozen flag")
    if pre_errors:
        print(json.dumps({"status": "FAIL_PRE_FREEZE_VALIDATION", "errors": pre_errors}, indent=2))
        raise SystemExit(1)

    copy_sources: list[tuple[str, Path, str]] = [
        ("gold", gold_source, "gold_v1.jsonl"),
        ("strict_oracle_rubric", rubric_source, "strict_oracle_rubric_v1.json"),
        ("human_acceptance", acceptance_source, "gold_v1_human_acceptance_decisions.json"),
        ("freeze_preparation_manifest", draft_source, "gold_v1_freeze_manifest_draft.json"),
        ("generation_allowlist", allowlist_source, "generation_input_allowlist.json"),
        ("generation_serializer", serializer_source, "generation_input_serializer.py"),
        ("generation_safe_projection", projection_source, "generation_input_safe_projection.jsonl"),
        ("enforced_experiment_runner", ROOT / "scripts/phase2_10B_experiment_runner.py", "enforced_experiment_runner.py"),
        ("generation_prompt", ROOT / "data/phase2_9_abc_generation_prompt_PROPOSED.txt", "abc_generation_prompt.txt"),
        ("phase2_11F_2_human_decisions", ROOT / "gold_human_decisions_working.json", "phase2_11F_2_human_decisions.json"),
        ("phase2_11F_3_human_decisions", ROOT / "gold_final_human_verification_decisions.json", "phase2_11F_3_human_verification_decisions.json"),
        ("freeze_authorization", AUTHORIZATION, "gold_v1_freeze_authorization.md"),
        ("f3_correction_log", ROOT / "outputs/phase2_11F_3/per_record_correction_log.jsonl", "phase2_11F_3_correction_log.jsonl"),
        ("f3_temporal_leakage_audit", ROOT / "outputs/phase2_11F_3/temporal_leakage_audit_all18.jsonl", "phase2_11F_3_temporal_leakage_audit.jsonl"),
        ("f3_invariant_validation", ROOT / "outputs/phase2_11F_3/invariant_validation_results.json", "phase2_11F_3_invariant_validation.json"),
        ("f3_generation_validation", ROOT / "outputs/phase2_11F_3/generation_input_validation_report.json", "phase2_11F_3_generation_input_validation.json"),
        ("f4_acceptance_report", ROOT / "outputs/phase2_11F_4_lite/f4_lite_acceptance_report.md", "phase2_11F_4_lite_acceptance_report.md"),
        ("f4_blockers", blockers_source, "phase2_11F_4_lite_blockers.json"),
        ("f4b_validation", ROOT / "outputs/phase2_11F_4b/validation_results.json", "phase2_11F_4b_validation_results.json"),
        ("freeze_preparation_report", prep / "gold_v1_freeze_preparation_report.md", "gold_v1_freeze_preparation_report.md"),
        ("independent_verifier", ROOT / "scripts/verify_gold_v1_frozen_snapshot.py", "verify_frozen_snapshot.py"),
        ("freeze_execution_tool", Path(__file__).resolve(), "freeze_execution_tool.py"),
    ]
    staging = Path(tempfile.mkdtemp(prefix=".gold_v1_frozen.staging-", dir=ROOT / "outputs"))
    inventory: list[dict] = []
    for artifact_id, source, filename in copy_sources:
        frozen = staging / filename
        shutil.copyfile(source, frozen)
        digest = sha(source)
        if sha(frozen) != digest or frozen.read_bytes() != source.read_bytes():
            raise SystemExit(f"copy verification failed: {artifact_id}")
        inventory.append({"artifact_id": artifact_id, "role": "byte_identical_source_copy",
                          "source_path": display_source(source), "frozen_filename": filename,
                          "sha256": digest, "source_and_frozen_byte_identical": True})

    record_metric_rows = []
    rubric_by_bug: dict[str, list[dict]] = {}
    for entry in rubric["entries"]:
        rubric_by_bug.setdefault(entry["bug_id"], []).append({"oracle_id": entry["oracle_id"],
            "underlying_gold_classification": entry["underlying_gold_classification"],
            "strict_scoring_disposition": entry["strict_scoring_disposition"]})
    for record in gold:
        record_metric_rows.append({
            "bug_id": record["bug_id"],
            "tcc_paths": [{"path_id": path["path_id"], "required_condition_ids": path["required_condition_ids"],
                           "denominator": path["tcc_denominator"]} for path in record["trigger_paths"]],
            "cspc_eligible_path_ids": record["evaluation_targets_v2"]["cspc_eligible_path_ids"],
            "partial_exploratory_only": record["evaluation_targets_v2"]["partial_exploratory_only"],
            "scenario_adequacy_required": record["evaluation_targets_v2"]["scenario_adequacy_required"],
            "strict_oracles": rubric_by_bug[record["bug_id"]],
        })
    metric = {
        "artifact_id": "gold-v1-frozen-metric-eligibility",
        "source_gold_sha256": AUTHORIZED_GOLD_SHA,
        "source_rubric_sha256": sha(rubric_source),
        "frozen_metric_scopes": {
            "tcc_bugs": 18, "tcc_paths": 19, "tcc_required_condition_denominator": 24,
            "cspc_bugs": 11, "cspc_paths": 12, "strict_oracle_entries": 10,
            "strict_oracle_bugs": 9, "strict_pending_excluded": 2,
            "strict_ineligible_incomplete_excluded": 7, "scenario_adequacy_bugs": 18,
        },
        "pending_strict_oracles": ["tmodloader/tmodloader#2644::O1", "vcmi/vcmi#5462::O1"],
        "pending_labels_preserved": True,
        "other_metric_eligibility_independent": True,
        "per_record": record_metric_rows,
    }
    metric_path = staging / "gold_v1_metric_eligibility.json"
    write_json(metric_path, metric)
    inventory.append({"artifact_id": "metric_eligibility", "role": "derived_and_validated_freeze_artifact",
                      "source_paths": [display_source(gold_source), display_source(rubric_source), display_source(draft_source)],
                      "frozen_filename": metric_path.name, "sha256": sha(metric_path),
                      "source_and_frozen_byte_identical": None})

    # Reproduce the generation boundary entirely from staging files before finalization.
    spec = importlib.util.spec_from_file_location("staging_gold_v1_serializer", staging / "generation_input_serializer.py")
    if spec is None or spec.loader is None:
        raise SystemExit("staging serializer cannot be loaded")
    serializer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(serializer)
    projection = serializer.load_safe_projection(staging / "generation_input_safe_projection.jsonl")
    if len(projection) != 18:
        raise SystemExit("staging safe projection does not contain 18 records")

    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    manifest = {
        "dataset_version": "Gold v1",
        "freeze_status": "FROZEN_VERIFIED",
        "freeze_timestamp": timestamp,
        "human_authorization": {"status": True, "frozen_filename": "gold_v1_freeze_authorization.md",
                                "sha256": sha(staging / "gold_v1_freeze_authorization.md")},
        "technical_validation": {"status": "PASS", "preparation_validation": "PASS_GOLD_V1_FREEZE_PREPARATION",
                                 "engineering_blockers": 0, "source_copy_byte_identity": True,
                                 "generation_boundary_reproduced_from_staging": True},
        "runtime_reproduction": {"claimed": False, "performed_during_freeze": False},
        "gold": {"source_path": display_source(gold_source), "frozen_filename": "gold_v1.jsonl",
                 "sha256": AUTHORIZED_GOLD_SHA, "record_count": 18,
                 "bug_ids_in_file_order": [item["bug_id"] for item in gold]},
        "rubric": {"source_path": display_source(rubric_source), "frozen_filename": "strict_oracle_rubric_v1.json",
                   "sha256": sha(rubric_source), "version": "1.0", "strict_oracle_denominator": 10},
        "metric_eligibility": {"frozen_filename": "gold_v1_metric_eligibility.json", "sha256": sha(metric_path),
                               "frozen_scopes": metric["frozen_metric_scopes"]},
        "generation_boundary": {"policy_version": "2.11F-3-explicit-allowlist-v1",
                                "allowlist_filename": "generation_input_allowlist.json",
                                "allowlist_sha256": sha(staging / "generation_input_allowlist.json"),
                                "serializer_filename": "generation_input_serializer.py",
                                "serializer_sha256": sha(staging / "generation_input_serializer.py"),
                                "safe_projection_filename": "generation_input_safe_projection.jsonl",
                                "safe_projection_sha256": sha(staging / "generation_input_safe_projection.jsonl"),
                                "validation_status": "PASS_ENFORCED_APPROVED_SERIALIZER_BOUNDARY"},
        "human_acceptance": {"frozen_filename": "gold_v1_human_acceptance_decisions.json",
                             "sha256": sha(staging / "gold_v1_human_acceptance_decisions.json"),
                             "all_18_accepted_for_freeze_consideration": True,
                             "nine_corrected_records_accepted": True,
                             "corsix_1799_final_verify_preserved": True},
        "known_research_limitations": [
            "Issue edit histories are unavailable; creation-time equivalence is not claimed.",
            "No independent runtime reproduction is claimed by structural validation or freeze execution.",
            "Some fixtures, historical services, and environment dependencies remain unavailable or intermittent.",
            "Seven incomplete/ineligible Oracles are excluded from strict scoring.",
            "tmodloader/tmodloader#2644::O1 and vcmi/vcmi#5462::O1 remain pending_rubric_decision and are excluded from strict scoring only.",
        ],
        "frozen_artifacts": inventory,
        "artifact_hash_scope": "All copied and derived content artifacts; manifest and post-finalization verification/report files are administrative and excluded from self-referential inventory.",
        "immutability_policy": "Snapshot directory and files are made read-only after independent verification.",
        "freeze_authorized": True,
        "gold_frozen": True,
        "gold_records_modified_to_mark_frozen": False,
        "phase3_experiments_authorized": False,
        "llm_calls_authorized": False,
        "rag_bm25_indexing_authorized": False,
        "official_model_calls": 0,
    }
    write_json(staging / "gold_v1_freeze_manifest.json", manifest)
    internal_result = verify(staging)
    if internal_result["status"] != "PASS_GOLD_V1_FROZEN_SNAPSHOT":
        print(json.dumps({"status": "FAIL_STAGING_VERIFICATION", "staging": str(staging),
                          "verification": internal_result}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    staging.rename(FINAL)
    print(json.dumps({"status": "FROZEN_STAGING_VERIFIED_AWAITING_INDEPENDENT_FINAL_PASS",
                      "snapshot": str(FINAL), "gold_sha256": AUTHORIZED_GOLD_SHA,
                      "record_count": 18, "freeze_authorized": True, "gold_frozen": True,
                      "phase3_experiments_authorized": False, "official_model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
