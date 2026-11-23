#!/usr/bin/env python3
"""Apply the explicitly authorized Stage 1 Secondary-QC decisions to new artifacts."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
GOLD = HERE.parent / "gold_v1_frozen" / "gold_v1.jsonl"
BASE_RUBRIC = HERE.parent / "gold_v1_frozen" / "strict_oracle_rubric_v1.json"
BASE_STAT_PLAN = EVAL / "stage1_statistical_plan_approval_candidate.md"
SOURCE_STAT_PLAN = HERE.parent / "phase3c_h_ai_first_amendment" / "phase3c_h_statistical_analysis_plan_v2_PROPOSED.md"
SOURCE_WB = EVAL / "stage1_secondary_qc_human_review_workbook.csv"
SOURCE_QUEUE = EVAL / "stage1_secondary_qc_review_queue_immutable.jsonl"
PROPOSED = EVAL / "stage1_secondary_qc_adjudication_PROPOSED.csv"
ORACLE_AUDIT = EVAL / "stage1_oracle_applicability_records.csv"
PROPOSED_EFFECTIVE = EVAL / "stage1_oracle_effective_scores_PROPOSED.csv"
PROPOSED_SUPPLEMENTAL = EVAL / "stage1_oracle_supplemental_adjudication_PROPOSED.csv"
QC_MANIFEST = EVAL / "stage1_secondary_qc_review_manifest.json"
DEVIATION_AUDIT = EVAL / "stage1_mapping_access_deviation_audit.md"
CSPC_WB = EVAL / "stage1_cspc_human_verification_preparation" / "cspc_human_verification_workbook.csv"
CSPC_VALIDATION = EVAL / "stage1_cspc_human_verification_preparation" / "human_verification_validation_report.json"

FINAL_WB = EVAL / "stage1_secondary_qc_human_review_workbook_FINAL.csv"
FINAL_SUPPLEMENTAL = EVAL / "stage1_oracle_supplemental_adjudication_FINAL.csv"
FINAL_EFFECTIVE = EVAL / "stage1_oracle_effective_scores_FINAL.csv"
SUMMARY = EVAL / "stage1_secondary_qc_adjudication_summary_FINAL.md"
POLICY = EVAL / "stage1_secondary_qc_policy_v1_APPROVED.json"
RUBRIC_AMENDMENT = EVAL / "stage1_strict_oracle_applicability_amendment_v1_FROZEN.json"
STAT_AMENDMENT = EVAL / "stage1_statistical_plan_oracle_amendment_v1_FROZEN.md"
DEVIATION_DISPOSITION = EVAL / "stage1_mapping_access_deviation_disposition_v1_APPROVED.md"
MANIFEST = EVAL / "stage1_score_lock_readiness_manifest.json"

REVIEWER_ID = "RESEARCHER_OF_RECORD__SINGLE_RESEARCHER_AI_ASSISTED"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    qc_manifest = json.loads(QC_MANIFEST.read_text(encoding="utf-8"))
    proposed = read_csv(PROPOSED)
    workbook = read_csv(SOURCE_WB)
    audit = read_csv(ORACLE_AUDIT)
    effective = read_csv(PROPOSED_EFFECTIVE)
    supplemental = read_csv(PROPOSED_SUPPLEMENTAL)

    # Fail before writing if the authorization does not match the proposed population.
    assert qc_manifest["counts"]["valid_responses"] == 53
    assert qc_manifest["counts"]["eligible_response_metric_records"] == 133
    assert qc_manifest["counts"]["total_review"] == 43
    assert Counter(x["proposed_decision"] for x in proposed) == {"ACCEPT": 41, "CORRECT": 2}
    assert len(workbook) == 43 and len({x["review_id"] for x in workbook}) == 43
    assert len(supplemental) == 1 and supplemental[0]["proposed_decision"] == "CORRECT"
    assert Counter(x["applicability_classification"] for x in audit) == {"APPLICABLE": 27, "NOT_APPLICABLE": 3}
    assert len(effective) == 30

    proposal_by_id = {x["review_id"]: x for x in proposed}
    audit_by_response_oracle = {(x["anonymous_response_id"], x["oracle_id"]): x for x in audit}
    final_workbook = []
    for row in workbook:
        decision = proposal_by_id[row["review_id"]]
        assert decision["immutable_review_record_sha256"] == row["immutable_review_record_sha256"]
        assert decision["original_ai_score_json"] == row["original_ai_score_json"]
        out = dict(row)
        out["human_decision"] = decision["proposed_decision"]
        out["reviewer_id"] = REVIEWER_ID
        out["review_timestamp_utc"] = timestamp
        if decision["proposed_decision"] == "ACCEPT":
            out["human_corrected_score_or_label_json"] = ""
            out["human_correction_reason"] = ""
            out["human_evidence_reference"] = (
                "AUTHORIZED_PHASE3D_STAGE1_FINAL_APPROVAL; "
                f"immutable_review_record_sha256={row['immutable_review_record_sha256']}; "
                "single-researcher AI-assisted adjudication"
            )
        else:
            correction = json.loads(decision["proposed_correction_json"])
            assert len(correction) == 1 and correction[0]["oracle_id"] == "O2"
            evidence = audit_by_response_oracle[(row["anonymous_response_id"], "O2")]
            assert evidence["applicability_classification"] == "NOT_APPLICABLE"
            out["human_corrected_score_or_label_json"] = canon(correction)
            out["human_correction_reason"] = decision["proposed_rationale"]
            out["human_evidence_reference"] = canon({
                "oracle_entry_id": evidence["oracle_entry_id"],
                "selected_scenario_id": evidence["official_selected_scenario_id"],
                "selected_gold_path_id": evidence["official_selected_gold_path_id"],
                "oracle_id": evidence["oracle_id"],
                "oracle_gold_path_id": evidence["oracle_gold_path_id"],
                "classification_evidence": json.loads(evidence["classification_evidence_json"]),
                "authorization": "PHASE3D_STAGE1_FINAL_RESEARCHER_APPROVAL",
            })
        final_workbook.append(out)
    write_csv(FINAL_WB, final_workbook, list(workbook[0]))

    final_supplemental = []
    for row in supplemental:
        out = dict(row)
        out["human_decision"] = "CORRECT"
        out["researcher_confirmation"] = "CONFIRMED"
        out["reviewer_id"] = REVIEWER_ID
        out["review_timestamp_utc"] = timestamp
        out["approval_provenance"] = "PHASE3D_STAGE1_FINAL_RESEARCHER_APPROVAL"
        final_supplemental.append(out)
    write_csv(FINAL_SUPPLEMENTAL, final_supplemental)

    final_effective = []
    for row in effective:
        evidence = json.loads(row["classification_evidence_json"])
        applicable = row["applicability_classification"] == "APPLICABLE"
        out = {
            "oracle_entry_id": row["oracle_entry_id"],
            "anonymous_response_id": row["anonymous_response_id"],
            "bug_id": row["bug_id"],
            "oracle_id": row["oracle_id"],
            "original_ai_semantic_label": row["original_ai_semantic_label"],
            "original_ai_binary_score": row["original_ai_binary_score"],
            "original_gold_scoring_eligibility": evidence["oracle_gold_eligibility"],
            "human_approved_applicability": row["applicability_classification"],
            "effective_scoring_eligibility": "INCLUDED_APPLICABLE" if applicable else "EXCLUDED_NOT_APPLICABLE",
            "effective_score": row["original_ai_binary_score"] if applicable else "",
            "denominator_contribution": "1" if applicable else "0",
            "numerator_contribution": row["original_ai_binary_score"] if applicable else "0",
            "correction_reason": "" if applicable else row["exclusion_reason"],
            "classification_evidence_json": row["classification_evidence_json"],
            "original_evaluation_provenance": row["review_decision_provenance"],
            "human_approval_provenance": "PHASE3D_STAGE1_FINAL_RESEARCHER_APPROVAL",
            "reviewer_id": REVIEWER_ID,
            "approval_timestamp_utc": timestamp,
        }
        final_effective.append(out)
    write_csv(FINAL_EFFECTIVE, final_effective)

    policy = {
        "artifact_version": "stage1-secondary-qc-policy-v1",
        "status": "APPROVED",
        "approval_timestamp_utc": timestamp,
        "review_designation": "single-researcher, AI-assisted human adjudication, with a complete condition-blind Oracle applicability audit",
        "targeted": {"records": 20, "major_errors": 2, "minor_errors": 0, "accepted_without_change": 18},
        "random": {"records": 23, "major_errors": 0, "minor_errors": 0, "accepted_without_change": 23},
        "random_frame": 113,
        "sampling_unit": "response_x_metric_record",
        "stratification": "metric_x_ai_polarity",
        "seed": 29012026,
        "sampling_implementation": qc_manifest["sampling"],
        "supplemental_oracle_correction": {"records": 1, "reported_separately_from_original_43": True},
        "systematic_error_escalation": {
            "triggered": True,
            "pattern": "alternative-path Strict Oracle applicability",
            "disposition": "complete audit of all 30 original Strict Oracle entries",
            "scope_limit": "does not independently validate TCC or Scenario Adequacy",
        },
        "source_manifest_sha256": sha(QC_MANIFEST),
        "condition_mapping_used": False,
    }
    POLICY.write_text(json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rubric = {
        "artifact_version": "stage1-strict-oracle-path-applicability-amendment-v1",
        "status": "FROZEN",
        "approval_timestamp_utc": timestamp,
        "base_rubric_path": str(BASE_RUBRIC.resolve()),
        "base_rubric_sha256": sha(BASE_RUBRIC),
        "rules": [
            "Only Oracle branches applicable to the officially selected scenario and Gold path contribute to Strict Oracle scoring.",
            "Missing, partial, incorrect, or unusable Oracles on applicable paths remain eligible and may receive zero.",
            "NOT_APPLICABLE branches are excluded from numerator and denominator; they receive no positive credit.",
            "INDETERMINATE applicability requires explicit human adjudication before final scoring.",
            "NOT_SCORABLE_GOLD remains distinct from NOT_APPLICABLE.",
            "Original AI labels and scores remain immutable; applicability is a derived adjudicated layer.",
        ],
        "unchanged_metrics": ["CSPC", "TCC"],
        "planned_analysis_unit": "Bug-level paired comparison",
        "applied_stage1_counts": {"original": {"numerator": 25, "denominator": 30}, "effective": {"numerator": 24, "denominator": 27}},
        "condition_mapping_used": False,
    }
    RUBRIC_AMENDMENT.write_text(json.dumps(rubric, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    stat_text = f"""# Stage 1 Statistical Plan — Frozen Oracle Applicability Amendment v1

Status: **FROZEN AMENDMENT**  
Approval timestamp: `{timestamp}`

## Preserved bases

- Stage 1 approval candidate: `{BASE_STAT_PLAN.resolve()}`  
  SHA-256: `{sha(BASE_STAT_PLAN)}`
- Original Phase 3C-H proposed plan: `{SOURCE_STAT_PLAN.resolve()}`  
  SHA-256: `{sha(SOURCE_STAT_PLAN)}`

Neither base document is modified by this amendment.

## Approved amendment

Strict Oracle eligibility is evaluated at Oracle-entry level after the official scenario and Gold path have been selected. Only Oracle branches applicable to that selected scenario/path enter the Strict Oracle numerator and denominator. Applicable missing, partial, incorrect, or unusable Oracle results remain eligible for zero. A non-applicable alternative-path Oracle is excluded with a null effective score and zero numerator/denominator contribution; it is not reclassified as correct. Indeterminate applicability requires human adjudication.

For Stage 1, this produces pooled Oracle-entry counts of 24/27 from the original 25/30. These pooled counts are not A/B/C, Bug-level, or inferential results.

CSPC and TCC definitions are unchanged. The Bug remains the paired statistical analysis unit. The Repeat-1-only design, contrast-specific missingness, exact paired denominators, two-sided exact paired sign-flip test, and paired Bug-level bootstrap specifications in the preserved approval candidate are unchanged. No condition comparison is authorized or performed by this amendment.
"""
    STAT_AMENDMENT.write_text(stat_text, encoding="utf-8")

    deviation = f"""# Stage 1 Mapping-Access Deviation — Approved Disposition v1

Status: **APPROVED DOCUMENTED DEVIATION**  
Approval timestamp: `{timestamp}`

## Preserved audit

Source: `{DEVIATION_AUDIT.resolve()}`  
SHA-256: `{sha(DEVIATION_AUDIT)}`

The source audit remains unchanged.

## Factual disposition

- A preceding score-lock preflight process opened the restricted Stage 1 task mapping and mapping content appeared in its terminal/tool output.
- Effective AI evaluator calls and immutable AI assessments preceded that access; condition assignments did not enter AI evaluator inputs.
- The researcher communicated the semantic 36/36 CSPC accept-all decision before the access. Mechanical workbook recording and validation occurred afterward.
- The condition-blind CSPC review package was prepared before access and contained no condition labels, task IDs, BM25 scores, or retrieval ranks.
- No score lock, condition comparison, p-value, confidence interval, or figure resulted from that access.
- The present adjudication/final-readiness task did not open the restricted mapping and did not use condition identities.

## Methodological disposition

This is retained as a blinding-procedure deviation and final-study limitation. It is not treated as proof of biased scoring, but it is also not declared harmless: repository evidence cannot exclude possible cognitive influence on later researcher-facing activity. The pre-access AI evaluations and already-communicated CSPC decision remain preserved; subsequent Secondary-QC decisions are identified as single-researcher, AI-assisted adjudication rather than independent blinded review.

## Required safeguards for later score lock and unblinding

1. Perform score lock in a fresh process where feasible, using only manifest-verified final scoring inputs.
2. Prohibit semantic score/evidence edits in the lock process.
3. Complete and hash the condition-blind score lock before opening the mapping for formal unblinding.
4. Preserve an access log, lock manifest, and all missingness decisions.
5. Disclose this deviation and its residual uncertainty in research reporting.

This disposition authorizes readiness assessment only. It does not itself execute score lock or unblinding.
"""
    DEVIATION_DISPOSITION.write_text(deviation, encoding="utf-8")

    summary = f"""# Stage 1 Secondary QC Final Adjudication Summary

Status: human adjudication recorded; score lock not executed.  
Recorded at: `{timestamp}`  
Designation: **Single-researcher, AI-assisted human adjudication, with a complete condition-blind Oracle applicability audit.**

## Final decisions

- Original 43-record QC sample: 41 ACCEPT, 2 CORRECT, 0 UNRESOLVED.
- TARGETED: 20 total; 18 ACCEPT and 2 major scoring/eligibility CORRECT.
- RANDOM: 23 total; 23 ACCEPT and no identified correction.
- Supplemental: 1 major Oracle applicability correction outside the original sample.

The supplemental record remains separate and does not alter the original sample provenance.

## Oracle-wide disposition

All 30 original Oracle entries were audited: 27 APPLICABLE, 3 NOT_APPLICABLE, 0 INDETERMINATE, and 0 NOT_SCORABLE_GOLD among original entries. Original pooled entries were 25/30. Human-approved effective pooled entries are 24/27. Three applicable original zero-score entries remain in the denominator. Original AI labels and scores are unchanged.

The repeated alternative-path error triggered systematic escalation. The approved resolution is the complete 30-entry condition-blind Oracle audit. This expansion does not independently validate TCC or Scenario Adequacy.

## Methodology

The QC sampling policy, Oracle rubric amendment, statistical-plan amendment, and mapping-access deviation disposition are recorded in separate versioned artifacts. CSPC decisions and definitions are unchanged. No restricted mapping was accessed during this task; no condition comparison, score lock, or unblinding was performed.
"""
    SUMMARY.write_text(summary, encoding="utf-8")

    outputs = [FINAL_WB, FINAL_SUPPLEMENTAL, FINAL_EFFECTIVE, SUMMARY, POLICY, RUBRIC_AMENDMENT, STAT_AMENDMENT, DEVIATION_DISPOSITION]
    inputs = [GOLD, BASE_RUBRIC, BASE_STAT_PLAN, SOURCE_STAT_PLAN, SOURCE_WB, SOURCE_QUEUE, PROPOSED, ORACLE_AUDIT, PROPOSED_EFFECTIVE, PROPOSED_SUPPLEMENTAL, QC_MANIFEST, DEVIATION_AUDIT, CSPC_WB, CSPC_VALIDATION]
    manifest = {
        "artifact_version": "stage1-score-lock-readiness-artifacts-v1",
        "status": "FINAL_ADJUDICATION_RECORDED_VALIDATION_PENDING",
        "processing_timestamp_utc": timestamp,
        "reviewer_id": REVIEWER_ID,
        "review_designation": "single-researcher AI-assisted human adjudication with complete condition-blind Oracle applicability audit",
        "counts": {
            "valid_responses": 53, "eligible_response_metric_records": 133,
            "qc_total": 43, "accept": 41, "correct": 2, "unresolved": 0,
            "supplemental_correct": 1, "oracle_entries": 30, "oracle_applicable": 27,
            "oracle_not_applicable": 3, "oracle_indeterminate": 0,
            "oracle_not_scorable_gold": 0, "effective_numerator": 24, "effective_denominator": 27,
        },
        "input_hashes": {p.name: sha(p) for p in inputs},
        "output_hashes": {p.name: sha(p) for p in outputs},
        "frozen_gold_modified": False,
        "original_ai_scores_modified": False,
        "original_cspc_decisions_modified": False,
        "restricted_mapping_accessed_during_task": False,
        "score_lock_performed": False,
        "unblinding_performed": False,
        "condition_comparison_performed": False,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"timestamp": timestamp, "counts": manifest["counts"]}, indent=2))


if __name__ == "__main__":
    main()
