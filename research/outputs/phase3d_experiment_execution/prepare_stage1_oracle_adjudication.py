#!/usr/bin/env python3
"""Prepare condition-blind Oracle applicability and proposed Secondary-QC adjudication."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
EVAL = HERE / "stage1_ai_evaluation"
RESPONSES = EVAL / "responses"
PROVISIONAL = EVAL / "public" / "provisional_ai_metric_results.jsonl"
GOLD = HERE.parent / "gold_v1_frozen" / "gold_v1.jsonl"
RUBRIC = HERE.parent / "gold_v1_frozen" / "strict_oracle_rubric_v1.json"
QC_QUEUE = EVAL / "stage1_secondary_qc_review_queue_immutable.jsonl"
QC_MANIFEST = EVAL / "stage1_secondary_qc_review_manifest.json"
QC_WORKBOOK = EVAL / "stage1_secondary_qc_human_review_workbook.csv"
STAT_PLAN = EVAL / "stage1_statistical_plan_approval_candidate.md"
APPROVAL_CHECKLIST = EVAL / "stage1_prelock_approval_checklist.md"

AUDIT_MD = EVAL / "stage1_oracle_applicability_audit.md"
RECORDS_CSV = EVAL / "stage1_oracle_applicability_records.csv"
EFFECTIVE_CSV = EVAL / "stage1_oracle_effective_scores_PROPOSED.csv"
ADJUDICATION_CSV = EVAL / "stage1_secondary_qc_adjudication_PROPOSED.csv"
SUPPLEMENTAL_CSV = EVAL / "stage1_oracle_supplemental_adjudication_PROPOSED.csv"
APPROVAL_MD = EVAL / "stage1_secondary_qc_final_approval.md"
MANIFEST = EVAL / "stage1_oracle_adjudication_manifest.json"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def canon(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def effective_assessment(response_id: str) -> tuple[dict, Path]:
    directory = RESPONSES / response_id
    retry = directory / "authorized_retry_1" / "ai_assessment.json"
    path = retry if retry.exists() else directory / "ai_assessment.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def audit_oracles() -> tuple[list[dict], dict]:
    gold_by_bug = {x["bug_id"]: x for x in load_jsonl(GOLD)}
    queue = {x["review_id"]: x for x in load_jsonl(QC_QUEUE)}
    valid = [x for x in load_jsonl(PROVISIONAL) if x["evaluation_status"] == "AI_VALID_PROVISIONAL"]
    if len(valid) != 53:
        raise ValueError("Expected 53 valid responses")
    records = []
    responses_without_entries = 0
    for result in valid:
        rid = result["anonymous_response_id"]
        bug_id = result["bug_id"]
        assessment, assessment_path = effective_assessment(rid)
        ai_oracles = assessment["strict_oracle_assessments"]
        score_by_id = {x["oracle_id"]: x for x in result["provisional_scores"]["strict_oracle"]}
        if not ai_oracles:
            responses_without_entries += 1
            continue
        gold = gold_by_bug[bug_id]
        oracle_by_id = {x["oracle_id"]: x for x in gold["test_oracles"]}
        path_by_id = {x["path_id"]: x for x in gold["trigger_paths"]}
        tcc = result["provisional_scores"]["tcc"]
        selected_path = tcc["selected_gold_path_id"]
        selected_scenario = tcc["selected_scenario_id"]
        for ai in ai_oracles:
            oracle_id = ai["oracle_id"]
            score = score_by_id[oracle_id]
            gold_oracle = oracle_by_id[oracle_id]
            oracle_path = gold_oracle["trigger_path_id"]
            path_assessment = next(x for x in assessment["tcc_path_assessments"] if x["gold_path_id"] == oracle_path)
            comparison = next(x for x in path_assessment["scenario_comparisons"] if x["scenario_id"] == selected_scenario)
            gold_eligibility = gold_oracle.get("strict_oracle_scoring_eligibility", "")
            if not str(gold_eligibility).startswith("eligible"):
                applicability = "NOT_SCORABLE_GOLD"
            elif oracle_path != selected_path:
                # Official TCC path is distinct; retain comparison evidence to prove whether the alternative was attempted.
                required = set(path_by_id[oracle_path].get("required_condition_ids", []))
                covered = set(comparison.get("covered_trigger_ids", []))
                missing = set(comparison.get("missing_trigger_ids", []))
                if not covered and required and missing == required:
                    applicability = "NOT_APPLICABLE"
                else:
                    applicability = "INDETERMINATE"
            elif comparison.get("coherent"):
                applicability = "APPLICABLE"
            else:
                applicability = "INDETERMINATE"

            enters = applicability == "APPLICABLE"
            review_id = f"SECONDARY::{rid}::STRICT_ORACLE"
            evidence = {
                "official_selected_scenario_id": selected_scenario,
                "official_selected_gold_path_id": selected_path,
                "oracle_gold_path_id": oracle_path,
                "oracle_gold_eligibility": gold_eligibility,
                "oracle_gold_completeness": gold_oracle.get("completeness"),
                "selected_scenario_on_oracle_path": {
                    "coherent": comparison.get("coherent"),
                    "covered_trigger_ids": comparison.get("covered_trigger_ids", []),
                    "missing_trigger_ids": comparison.get("missing_trigger_ids", []),
                    "evidence": comparison.get("evidence", []),
                },
                "oracle_assessment_evidence": ai.get("evidence", []),
            }
            exclusion_reason = {
                "APPLICABLE": "",
                "NOT_APPLICABLE": "Oracle belongs exclusively to an unselected Gold path that the official selected scenario does not attempt.",
                "INDETERMINATE": "Available path-attempt evidence is insufficient; researcher adjudication required.",
                "NOT_SCORABLE_GOLD": "Frozen Gold marks the Oracle ineligible or insufficiently specified.",
            }[applicability]
            records.append({
                "oracle_entry_id": f"ORACLE::{rid}::{oracle_id}",
                "anonymous_response_id": rid,
                "bug_id": bug_id,
                "official_selected_scenario_id": selected_scenario,
                "official_selected_gold_path_id": selected_path,
                "oracle_id": oracle_id,
                "oracle_gold_path_id": oracle_path,
                "original_ai_semantic_label": ai["semantic_label"],
                "original_ai_binary_score": score["binary_score"],
                "applicability_classification": applicability,
                "classification_evidence_json": canon(evidence),
                "enters_statistical_denominator_proposed": enters,
                "human_adjudication_required": applicability in {"NOT_APPLICABLE", "INDETERMINATE"},
                "in_original_43_qc_queue": review_id in queue,
                "original_qc_category": queue.get(review_id, {}).get("qc_category", "OUTSIDE_43"),
                "effective_scoring_eligibility_proposed": "INCLUDED_APPLICABLE" if enters else f"EXCLUDED_{applicability}",
                "effective_score_proposed": score["binary_score"] if enters else None,
                "denominator_contribution_proposed": 1 if enters else 0,
                "numerator_contribution_proposed": score["binary_score"] if enters else 0,
                "exclusion_reason": exclusion_reason,
                "review_decision_provenance": "AI_ASSISTED_APPLICABILITY_AUDIT_PENDING_RESEARCHER_CONFIRMATION",
                "researcher_confirmation": "PENDING",
                "effective_assessment_sha256": sha(assessment_path),
            })
    metadata = {
        "valid_responses": len(valid),
        "responses_with_original_strict_oracle_entries": len(valid) - responses_without_entries,
        "responses_without_original_entries_due_frozen_gold_scope": responses_without_entries,
        "original_oracle_entries": len(records),
    }
    return records, metadata


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        # Each artifact is an explicit projection of the in-memory audit row.
        # Failures should come from missing required fields, not from other fields
        # intentionally reserved for a sibling output table.
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    records, metadata = audit_oracles()
    if len(records) != 30:
        raise ValueError(f"Expected 30 original Oracle entries, got {len(records)}")
    classifications = Counter(x["applicability_classification"] for x in records)
    original_labels = Counter(x["original_ai_semantic_label"] for x in records)
    original_scores = Counter(x["original_ai_binary_score"] for x in records)
    not_applicable = [x for x in records if x["applicability_classification"] == "NOT_APPLICABLE"]
    outside_43 = [x for x in not_applicable if not x["in_original_43_qc_queue"]]
    zero_absent = [x for x in not_applicable if x["original_ai_binary_score"] == 0]

    record_fields = [
        "oracle_entry_id", "anonymous_response_id", "bug_id", "official_selected_scenario_id",
        "official_selected_gold_path_id", "oracle_id", "oracle_gold_path_id",
        "original_ai_semantic_label", "original_ai_binary_score", "applicability_classification",
        "classification_evidence_json", "enters_statistical_denominator_proposed",
        "human_adjudication_required", "in_original_43_qc_queue", "original_qc_category",
        "researcher_confirmation", "effective_assessment_sha256",
    ]
    write_csv(RECORDS_CSV, records, record_fields)
    effective_fields = [
        "oracle_entry_id", "anonymous_response_id", "bug_id", "oracle_id",
        "original_ai_semantic_label", "original_ai_binary_score", "applicability_classification",
        "effective_scoring_eligibility_proposed", "effective_score_proposed",
        "denominator_contribution_proposed", "numerator_contribution_proposed", "exclusion_reason",
        "classification_evidence_json", "review_decision_provenance", "researcher_confirmation",
    ]
    write_csv(EFFECTIVE_CSV, records, effective_fields)

    queue = load_jsonl(QC_QUEUE)
    affected_queue_ids = {f"SECONDARY::{x['anonymous_response_id']}::STRICT_ORACLE" for x in not_applicable if x["in_original_43_qc_queue"]}
    proposed_rows = []
    for item in queue:
        correction_entries = [x for x in not_applicable if f"SECONDARY::{x['anonymous_response_id']}::STRICT_ORACLE" == item["review_id"]]
        if correction_entries:
            correction = [{
                "oracle_id": x["oracle_id"],
                "original_ai_score_preserved": x["original_ai_binary_score"],
                "original_ai_label_preserved": x["original_ai_semantic_label"],
                "derived_applicability": "NOT_APPLICABLE",
                "effective_score": None,
                "denominator_contribution": 0,
                "numerator_contribution": 0,
            } for x in correction_entries]
            decision = "CORRECT"
            rationale = "The Oracle belongs to an unselected alternative Gold path; correct only derived eligibility/denominator while preserving the original AI label and score."
            severity = "MAJOR_SCORING_ELIGIBILITY"
        else:
            correction = None
            decision = "ACCEPT"
            rationale = "AI-assisted QC recommendation retains the original metric assessment; no approved correction is identified in this audit."
            severity = "NO_SCORING_CHANGE_RECOMMENDED"
        proposed_rows.append({
            "review_id": item["review_id"],
            "anonymous_response_id": item["anonymous_response_id"],
            "metric": item["metric"],
            "qc_category": item["qc_category"],
            "immutable_review_record_sha256": item["immutable_review_record_sha256"],
            "original_ai_score_json": canon(item["original_ai_score_or_label"]),
            "proposed_decision": decision,
            "proposed_correction_json": "" if correction is None else canon(correction),
            "proposed_rationale": rationale,
            "unresolved_eligibility_question": "",
            "scoring_issue_severity": severity,
            "researcher_confirmation": "PENDING",
        })
    adjudication_fields = list(proposed_rows[0])
    write_csv(ADJUDICATION_CSV, proposed_rows, adjudication_fields)

    supplemental_rows = []
    for x in outside_43:
        supplemental_rows.append({
            "supplemental_review_id": f"SUPPLEMENTAL::{x['oracle_entry_id']}",
            "oracle_entry_id": x["oracle_entry_id"],
            "anonymous_response_id": x["anonymous_response_id"],
            "metric": "STRICT_ORACLE",
            "original_ai_semantic_label": x["original_ai_semantic_label"],
            "original_ai_binary_score": x["original_ai_binary_score"],
            "proposed_decision": "CORRECT",
            "proposed_correction_json": canon({
                "derived_applicability": "NOT_APPLICABLE", "effective_score": None,
                "denominator_contribution": 0, "numerator_contribution": 0,
                "original_ai_label_and_score_preserved": True,
            }),
            "proposed_rationale": x["exclusion_reason"],
            "scoring_issue_severity": "MAJOR_SCORING_ELIGIBILITY",
            "source_oracle_audit_evidence_json": x["classification_evidence_json"],
            "researcher_confirmation": "PENDING",
        })
    supplemental_fields = list(supplemental_rows[0]) if supplemental_rows else [
        "supplemental_review_id", "oracle_entry_id", "anonymous_response_id", "metric",
        "original_ai_semantic_label", "original_ai_binary_score", "proposed_decision",
        "proposed_correction_json", "proposed_rationale", "scoring_issue_severity",
        "source_oracle_audit_evidence_json", "researcher_confirmation",
    ]
    write_csv(SUPPLEMENTAL_CSV, supplemental_rows, supplemental_fields)

    applicable = [x for x in records if x["applicability_classification"] == "APPLICABLE"]
    original_denominator = len(records)
    original_numerator = sum(x["original_ai_binary_score"] for x in records)
    proposed_denominator = sum(x["denominator_contribution_proposed"] for x in records)
    proposed_numerator = sum(x["numerator_contribution_proposed"] for x in records)
    audit = f"""# Stage 1 Strict Oracle Applicability Audit

Status: complete condition-blind audit; derived dispositions and human confirmation remain proposed.

## Rule applied

An Oracle is APPLICABLE only when it belongs to the officially selected Gold path and its selected scenario coherently attempts that path. An Oracle exclusive to a different, unattempted alternative path is NOT_APPLICABLE and contributes neither numerator nor denominator. Its original AI score and label remain immutable. `missing_unusable` is not automatically treated as NOT_APPLICABLE.

## Population reconciliation

- Valid anonymous responses: {metadata['valid_responses']}
- Responses with one or more original Strict Oracle entries: {metadata['responses_with_original_strict_oracle_entries']}
- Responses with no original entry because frozen Gold/eligibility excluded Strict Oracle: {metadata['responses_without_original_entries_due_frozen_gold_scope']}
- Original Strict Oracle entries audited: {metadata['original_oracle_entries']}

The 26 no-entry responses are reported at response level; they are not fabricated as AI Oracle entries. Consequently, `NOT_SCORABLE_GOLD` among the 30 original entries is {classifications['NOT_SCORABLE_GOLD']}.

## Entry-level applicability

| Classification | Entries |
|---|---:|
| APPLICABLE | {classifications['APPLICABLE']} |
| NOT_APPLICABLE | {classifications['NOT_APPLICABLE']} |
| INDETERMINATE | {classifications['INDETERMINATE']} |
| NOT_SCORABLE_GOLD | {classifications['NOT_SCORABLE_GOLD']} |

Original labels: `{dict(original_labels)}`. Original binary scores: `{dict(original_scores)}`.

## MapTool #2416 mandatory investigation

Three anonymous responses contain O1 and O2. In all three, the official selected scenario is S1 and the official selected Gold path is P1. O1 belongs to P1 and remains APPLICABLE with its original assessment preserved. O2 belongs to P2; the S1/P2 comparison covers none of P2's required trigger and explicitly records that trigger as missing. O2 is therefore NOT_APPLICABLE.

- Two affected records are the existing targeted QC records identified in the instruction. Their O2 labels are `missing_unusable` with original score 0.
- One additional affected response lies outside the 43-record QC sample. Its O2 label is `correct` with original score 1. Correctness does not make an unselected-path Oracle applicable.
- CSPC and TCC are unchanged.

## Derived scoring effect — not a group comparison

- Original entry denominator if all 30 entries were counted: {original_denominator}
- Original numerator: {original_numerator}
- Proposed applicability-aware denominator: {proposed_denominator}
- Proposed numerator: {proposed_numerator}
- Original zero entries caused solely by an unselected path not being attempted: {len(zero_absent)}
- Applicability corrections required: {len(not_applicable)}
- Corrections outside the 43-record QC sample: {len(outside_43)}

All three exclusions retain their original scores. Effective scores are null; denominator and numerator contributions are zero.

## Consistency and amendment recommendation

The error is systematic but limited to alternative-path Oracle applicability for one multi-path Bug. A metric-wide entry audit was therefore performed across all 30 entries and found no additional mismatches. The Strict Oracle scoring/rubric implementation requires a versioned amendment explicitly separating Gold eligibility from selected-path applicability before score lock. No frozen artifact is modified by this report.

No condition mapping, condition-level metric, score lock, unblinding, or group comparison was used.
"""
    AUDIT_MD.write_text(audit, encoding="utf-8")

    targeted_major = sum(x["qc_category"] == "TARGETED" and x["proposed_decision"] == "CORRECT" for x in proposed_rows)
    random_major = sum(x["qc_category"] == "RANDOM" and x["proposed_decision"] == "CORRECT" for x in proposed_rows)
    approval = f"""# Stage 1 Secondary QC Final Approval — PENDING

This document contains AI-assisted recommendations, not completed independent human review. No signature, approval timestamp, or researcher confirmation is inferred.

## Proposed 43-record decisions

- ACCEPT: {sum(x['proposed_decision'] == 'ACCEPT' for x in proposed_rows)}
- CORRECT: {sum(x['proposed_decision'] == 'CORRECT' for x in proposed_rows)}
- UNRESOLVED: {sum(x['proposed_decision'] == 'UNRESOLVED' for x in proposed_rows)}
- Researcher-confirmed: 0

The two proposed corrections are the existing targeted MapTool Strict Oracle records. O1 remains applicable and unchanged. O2 is proposed as NOT_APPLICABLE; its original zero/`missing_unusable` result remains preserved, while effective score becomes null and denominator contribution becomes zero.

## Supplemental adjudication

The full-population audit found {len(outside_43)} additional affected Oracle entry outside the 43-record sample. It is the same P1-versus-P2 applicability pattern, but its original O2 result is `correct`/1. It is proposed for the same eligibility correction, with original score preserved. See `{SUPPLEMENTAL_CSV.name}`.

## QC error accounting

- TARGETED denominator: 20 proposed reviews; major scoring-eligibility issues: {targeted_major}; minor issues: 0; recommended accepts: {20-targeted_major}.
- RANDOM denominator: 23 proposed reviews; major issues: {random_major}; minor issues: 0; recommended accepts: {23-random_major}.
- Supplemental full-population Oracle audit: {len(outside_43)} additional major eligibility issue.

The previously proposed repeated-systematic-error trigger is met by three instances. The required metric-wide Oracle applicability audit has now been completed over all 30 original entries. No previously approved escalation threshold was frozen, so the researcher must approve this disposition.

## Required amendment

Before score lock, adopt a versioned Strict Oracle rule: frozen Gold eligibility is necessary but not sufficient; the Oracle's path must also be applicable to the officially selected path/scenario. NOT_APPLICABLE entries are excluded with null effective score. APPLICABLE zero-score entries remain in the denominator. This must be reflected in the statistical-plan/rubric implementation without rewriting original AI output.

## Mapping-access deviation

The previously documented pre-lock mapping-access deviation remains pending methodological disposition. This audit did not access the mapping and contains no condition-level results.

## Explicit researcher approvals

- [ ] Approve the Oracle applicability rule and the derived implementation.
- [ ] Confirm all 41 proposed ACCEPT decisions.
- [ ] Confirm the two existing MapTool O2 NOT_APPLICABLE corrections.
- [ ] Confirm the one supplemental MapTool O2 NOT_APPLICABLE correction.
- [ ] Approve the QC sampling policy: 23 of 113, metric×polarity, seed 29012026.
- [ ] Approve the escalation policy and accept the completed 30-entry Oracle-wide audit as the required expansion.
- [ ] Approve a versioned statistical-plan/rubric amendment for selected-path applicability.
- [ ] Record a disposition for the mapping-access deviation.

Researcher name/identifier: ____________________  
Approval decision: ____________________  
Approval timestamp: ____________________  
Notes: ____________________

Until these fields are explicitly confirmed, all adjudications and effective scores remain proposed and score lock remains blocked.
"""
    APPROVAL_MD.write_text(approval, encoding="utf-8")

    output_files = [AUDIT_MD, RECORDS_CSV, EFFECTIVE_CSV, ADJUDICATION_CSV, SUPPLEMENTAL_CSV, APPROVAL_MD]
    manifest = {
        "artifact_version": "stage1-oracle-applicability-adjudication-v1",
        "status": "PROPOSED_AWAITING_RESEARCHER_CONFIRMATION",
        "counts": {
            **metadata,
            "applicable": classifications["APPLICABLE"],
            "not_applicable": classifications["NOT_APPLICABLE"],
            "indeterminate": classifications["INDETERMINATE"],
            "not_scorable_gold_original_entries": classifications["NOT_SCORABLE_GOLD"],
            "zero_due_unselected_path": len(zero_absent),
            "applicability_corrections": len(not_applicable),
            "corrections_outside_43": len(outside_43),
            "qc_proposed_accept": sum(x["proposed_decision"] == "ACCEPT" for x in proposed_rows),
            "qc_proposed_correct": sum(x["proposed_decision"] == "CORRECT" for x in proposed_rows),
            "supplemental_proposed_correct": len(supplemental_rows),
        },
        "original_ai_scores_modified": False,
        "frozen_gold_modified": False,
        "original_43_queue_modified": False,
        "researcher_confirmations": 0,
        "score_lock_performed": False,
        "unblinding_performed": False,
        "group_comparison_performed": False,
        "restricted_mapping_accessed": False,
        "input_hashes": {
            "provisional_results": sha(PROVISIONAL), "frozen_gold": sha(GOLD),
            "strict_oracle_rubric": sha(RUBRIC), "original_qc_queue": sha(QC_QUEUE),
            "original_qc_manifest": sha(QC_MANIFEST), "original_qc_workbook": sha(QC_WORKBOOK),
            "statistical_plan_candidate": sha(STAT_PLAN), "approval_checklist": sha(APPROVAL_CHECKLIST),
        },
        "output_hashes": {p.name: sha(p) for p in output_files},
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(manifest["counts"], indent=2))


if __name__ == "__main__":
    main()
