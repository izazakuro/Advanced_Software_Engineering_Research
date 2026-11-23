#!/usr/bin/env python3
"""Fail-closed validation for the proposed Stage 1 Oracle adjudication artifacts."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
BUILDER = HERE / "prepare_stage1_oracle_adjudication.py"
RECORDS = EVAL / "stage1_oracle_applicability_records.csv"
EFFECTIVE = EVAL / "stage1_oracle_effective_scores_PROPOSED.csv"
ADJUDICATION = EVAL / "stage1_secondary_qc_adjudication_PROPOSED.csv"
SUPPLEMENTAL = EVAL / "stage1_oracle_supplemental_adjudication_PROPOSED.csv"
AUDIT = EVAL / "stage1_oracle_applicability_audit.md"
APPROVAL = EVAL / "stage1_secondary_qc_final_approval.md"
MANIFEST = EVAL / "stage1_oracle_adjudication_manifest.json"
REPORT = EVAL / "stage1_oracle_adjudication_validation.json"
QC_MANIFEST = EVAL / "stage1_secondary_qc_review_manifest.json"
QC_QUEUE = EVAL / "stage1_secondary_qc_review_queue_immutable.jsonl"
GOLD = HERE.parent / "gold_v1_frozen" / "gold_v1.jsonl"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def load_builder():
    spec = importlib.util.spec_from_file_location("oracle_builder", BUILDER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load audit builder")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checks: list[dict] = []


def check(name: str, passed: bool, detail: object) -> None:
    checks.append({"check": name, "status": "PASS" if passed else "FAIL", "detail": detail})


def main() -> int:
    required = [BUILDER, RECORDS, EFFECTIVE, ADJUDICATION, SUPPLEMENTAL, AUDIT, APPROVAL, MANIFEST]
    check("all_required_artifacts_exist", all(p.is_file() for p in required), [p.name for p in required if not p.is_file()])
    if not all(p.is_file() for p in required):
        return finish()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    qc_manifest = json.loads(QC_MANIFEST.read_text(encoding="utf-8"))
    records = read_csv(RECORDS)
    effective = read_csv(EFFECTIVE)
    adjudication = read_csv(ADJUDICATION)
    supplemental = read_csv(SUPPLEMENTAL)

    # Recompute from immutable inputs rather than trusting generated tables.
    builder = load_builder()
    recomputed, metadata = builder.audit_oracles()
    by_id = {x["oracle_entry_id"]: x for x in recomputed}
    check("all_30_original_entries_scanned_once", len(records) == 30 == len(by_id) and len({x["oracle_entry_id"] for x in records}) == 30, {"csv": len(records), "recomputed": len(by_id)})
    check("all_53_valid_responses_reconciled", metadata == {
        "valid_responses": 53,
        "responses_with_original_strict_oracle_entries": 27,
        "responses_without_original_entries_due_frozen_gold_scope": 26,
        "original_oracle_entries": 30,
    }, metadata)

    actual_classes = Counter(x["applicability_classification"] for x in records)
    expected_classes = {"APPLICABLE": 27, "NOT_APPLICABLE": 3, "INDETERMINATE": 0, "NOT_SCORABLE_GOLD": 0}
    check("classification_counts", all(actual_classes[k] == v for k, v in expected_classes.items()), dict(actual_classes))
    allowed = set(expected_classes)
    check("classification_vocabulary_closed", all(x["applicability_classification"] in allowed for x in records), sorted({x["applicability_classification"] for x in records}))

    # Exact field comparison for facts that originate in immutable AI/Gold inputs.
    mismatch = []
    for row in records:
        src = by_id.get(row["oracle_entry_id"])
        if not src:
            mismatch.append({"id": row["oracle_entry_id"], "error": "not in recomputation"})
            continue
        fields = [
            "anonymous_response_id", "bug_id", "official_selected_scenario_id",
            "official_selected_gold_path_id", "oracle_id", "oracle_gold_path_id",
            "original_ai_semantic_label", "applicability_classification",
            "classification_evidence_json", "effective_assessment_sha256",
        ]
        for field in fields:
            if row[field] != str(src[field]):
                mismatch.append({"id": row["oracle_entry_id"], "field": field, "csv": row[field], "source": src[field]})
        if int(row["original_ai_binary_score"]) != src["original_ai_binary_score"]:
            mismatch.append({"id": row["oracle_entry_id"], "field": "original_ai_binary_score"})
    check("audit_rows_match_immutable_sources", not mismatch, mismatch[:10])

    effective_by_id = {x["oracle_entry_id"]: x for x in effective}
    check("effective_table_complete", len(effective) == 30 and set(effective_by_id) == set(by_id), len(effective))
    effective_errors = []
    for oid, src in by_id.items():
        row = effective_by_id.get(oid, {})
        if not row:
            continue
        # Original judgment is copied, never replaced by a derived score.
        if row["original_ai_semantic_label"] != src["original_ai_semantic_label"] or int(row["original_ai_binary_score"]) != src["original_ai_binary_score"]:
            effective_errors.append({"id": oid, "error": "original AI judgment changed"})
        applicable = src["applicability_classification"] == "APPLICABLE"
        if applicable:
            expected_score = str(src["original_ai_binary_score"])
            if row["effective_score_proposed"] != expected_score or row["denominator_contribution_proposed"] != "1" or row["numerator_contribution_proposed"] != expected_score:
                effective_errors.append({"id": oid, "error": "applicable contribution mismatch"})
        else:
            if row["effective_score_proposed"] != "" or row["denominator_contribution_proposed"] != "0" or row["numerator_contribution_proposed"] != "0" or not row["exclusion_reason"].strip():
                effective_errors.append({"id": oid, "error": "excluded entry lacks null score/zero contribution/reason"})
    check("derived_effective_scores_are_non_destructive", not effective_errors, effective_errors)

    applicable_zeros = [x for x in effective if x["applicability_classification"] == "APPLICABLE" and x["original_ai_binary_score"] == "0"]
    check("no_applicable_zero_excluded", len(applicable_zeros) == 3 and all(x["denominator_contribution_proposed"] == "1" and x["effective_score_proposed"] == "0" for x in applicable_zeros), [x["oracle_entry_id"] for x in applicable_zeros])
    excluded = [x for x in effective if x["applicability_classification"] != "APPLICABLE"]
    check("all_exclusions_have_explicit_evidence", len(excluded) == 3 and all(x["classification_evidence_json"].strip() and x["exclusion_reason"].strip() for x in excluded), [x["oracle_entry_id"] for x in excluded])
    denominator = sum(int(x["denominator_contribution_proposed"]) for x in effective)
    numerator = sum(int(x["numerator_contribution_proposed"]) for x in effective)
    check("proposed_effective_totals", denominator == 27 and numerator == 24, {"denominator": denominator, "numerator": numerator})

    decisions = Counter(x["proposed_decision"] for x in adjudication)
    categories = Counter(x["qc_category"] for x in adjudication)
    check("original_43_qc_recommendations_complete", len(adjudication) == 43 and decisions == {"ACCEPT": 41, "CORRECT": 2}, {"decisions": dict(decisions), "categories": dict(categories)})
    check("all_43_researcher_confirmations_pending", all(x["researcher_confirmation"] == "PENDING" for x in adjudication), Counter(x["researcher_confirmation"] for x in adjudication))
    check("two_in_sample_corrections_are_traceable", sum(x["proposed_decision"] == "CORRECT" and x["metric"] == "STRICT_ORACLE" and x["qc_category"] == "TARGETED" for x in adjudication) == 2, [x["review_id"] for x in adjudication if x["proposed_decision"] == "CORRECT"])
    check("supplemental_queue_exactly_one_pending_correction", len(supplemental) == 1 and supplemental[0]["proposed_decision"] == "CORRECT" and supplemental[0]["researcher_confirmation"] == "PENDING" and supplemental[0]["oracle_entry_id"] in by_id, supplemental)

    sampling = qc_manifest["sampling"]
    check("sampling_design_unchanged", qc_manifest["counts"]["total_review"] == 43 and qc_manifest["counts"]["targeted"] == 20 and qc_manifest["counts"]["random"] == 23 and sampling["frame_size_after_targeted_exclusion"] == 113 and sampling["sample_size"] == 23 and sampling["seed"] == 29012026 and sampling["strata"] == {
        "SCENARIO_ADEQUACY::POSITIVE": {"population": 38, "selected": 8},
        "STRICT_ORACLE::ALL_CORRECT": {"population": 22, "selected": 4},
        "TCC::FULL": {"population": 53, "selected": 11},
    }, {"counts": qc_manifest["counts"], "sampling": sampling})

    # Hash guards for frozen/immutable inputs.
    inputs = manifest["input_hashes"]
    check("frozen_gold_hash_preserved", inputs["frozen_gold"] == sha(GOLD) == qc_manifest["input_hashes"]["frozen_gold"], sha(GOLD))
    check("original_qc_queue_hash_preserved", inputs["original_qc_queue"] == sha(QC_QUEUE) == qc_manifest["output_hashes"]["immutable_queue"], sha(QC_QUEUE))
    output_hash_errors = {name: {"manifest": expected, "actual": sha(EVAL / name)} for name, expected in manifest["output_hashes"].items() if not (EVAL / name).exists() or sha(EVAL / name) != expected}
    check("generated_output_hashes_match_manifest", not output_hash_errors, output_hash_errors)

    # Public artifacts must remain condition-blind and cannot contain frozen task IDs.
    public_paths = [RECORDS, EFFECTIVE, ADJUDICATION, SUPPLEMENTAL, AUDIT, APPROVAL]
    text = "\n".join(p.read_text(encoding="utf-8") for p in public_paths)
    forbidden_tokens = ["condition_label", "condition_id", "bm25_score", "bm25_rank", "retrieval_rank", "group_assignment"]
    found_tokens = [x for x in forbidden_tokens if re.search(rf"\b{re.escape(x)}\b", text, re.IGNORECASE)]
    task_ids = sorted(set(re.findall(r"\bP3-\d{3}-[ABC]-R1\b", text)))
    check("no_condition_or_retrieval_mapping_exposed", not found_tokens and not task_ids, {"forbidden_tokens": found_tokens, "task_ids": task_ids})
    check("no_lock_unblind_or_group_comparison_performed", manifest["score_lock_performed"] is False and manifest["unblinding_performed"] is False and manifest["group_comparison_performed"] is False and manifest["restricted_mapping_accessed"] is False, {k: manifest[k] for k in ["score_lock_performed", "unblinding_performed", "group_comparison_performed", "restricted_mapping_accessed"]})
    check("all_outputs_remain_proposed", manifest["status"] == "PROPOSED_AWAITING_RESEARCHER_CONFIRMATION" and manifest["researcher_confirmations"] == 0, {"status": manifest["status"], "confirmations": manifest["researcher_confirmations"]})
    return finish(manifest)


def finish(manifest: dict | None = None) -> int:
    passed = all(x["status"] == "PASS" for x in checks)
    report = {
        "artifact_version": "stage1-oracle-adjudication-validation-v1",
        "status": "PASS" if passed else "FAIL",
        "checks_passed": sum(x["status"] == "PASS" for x in checks),
        "checks_failed": sum(x["status"] == "FAIL" for x in checks),
        "checks": checks,
        "condition_mapping_accessed": False,
        "score_lock_performed": False,
        "unblinding_performed": False,
        "group_comparison_performed": False,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    # Add non-circular validation artifact hashes to the manifest after the report is stable.
    if manifest is not None:
        manifest["validation_artifacts"] = {
            BUILDER.name: sha(BUILDER),
            Path(__file__).name: sha(Path(__file__)),
            REPORT.name: sha(REPORT),
        }
        MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks_passed": report["checks_passed"], "checks_failed": report["checks_failed"]}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
