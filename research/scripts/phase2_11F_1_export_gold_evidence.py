#!/usr/bin/env python3
"""Build the read-only-source Phase 2.11F-1 Gold human-review package."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import zipfile
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HANDOFF = ROOT / "phase2_11E_9_codex_handoff"
FINAL_DIR = ROOT / "data/phase2_11F_1_gold_evidence"
STAGE_DIR = ROOT / "data/.phase2_11F_1_gold_evidence.tmp"
FINAL_ZIP = ROOT / "phase2_11F_1_gold_reference_evidence_package.zip"
STAGE_ZIP = ROOT / ".phase2_11F_1_gold_reference_evidence_package.zip.tmp"


def load_jsonl_with_raw(path: Path) -> tuple[list[dict], dict[str, str]]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    return rows, {row["bug_id"]: line for row, line in zip(rows, lines)}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def canonical_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def evidence_ids_in(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "evidence_ids" and isinstance(item, list):
                found.update(str(x) for x in item)
            else:
                found.update(evidence_ids_in(item))
    elif isinstance(value, list):
        for item in value:
            found.update(evidence_ids_in(item))
    return found


def stage_exists() -> bool:
    return any(path.exists() for path in (FINAL_DIR, STAGE_DIR, FINAL_ZIP, STAGE_ZIP))


def main() -> None:
    if stage_exists():
        raise SystemExit("Phase 2.11F-1 output/staging path exists; refusing to overwrite")
    STAGE_DIR.mkdir(parents=True)

    status = json.loads((HANDOFF / "machine_readable_status.json").read_text(encoding="utf-8"))
    handoff_hash_checks = {}
    for name, expected in status["provenance_sha256"].items():
        actual = sha_file(HANDOFF / name)
        handoff_hash_checks[name] = {"expected": expected, "actual": actual, "matches": actual == expected}
    with (HANDOFF / "approved_pairs_108.csv").open(encoding="utf-8", newline="") as handle:
        approved_pairs = list(csv.DictReader(handle))
    with (HANDOFF / "phase2_11C_gold_18_structural_evidence_audit.csv").open(encoding="utf-8", newline="") as handle:
        structural_rows = list(csv.DictReader(handle))
    structural = {row["bug_id"]: row for row in structural_rows}
    abc_manifest = load_jsonl(HANDOFF / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl")
    manifest_ids = [row["target_bug_id"] for row in abc_manifest]
    manifest_by_id = {row["target_bug_id"]: row for row in abc_manifest}

    draft_path = ROOT / "data/gold_references_draft.jsonl"
    refined_path = ROOT / "data/gold_references_refined_draft.jsonl"
    candidate_path = ROOT / "data/gold_references_phase2_6_candidate.jsonl"
    draft, draft_raw = load_jsonl_with_raw(draft_path)
    refined, refined_raw = load_jsonl_with_raw(refined_path)
    candidate, candidate_raw = load_jsonl_with_raw(candidate_path)
    draft_map, refined_map, candidate_map = ({row["bug_id"]: row for row in rows} for rows in (draft, refined, candidate))

    target_snapshots = {row["doc_id"]: row for row in load_jsonl(ROOT / "data/phase2_11E_evidence/phase2_11E_target_issues.jsonl")}
    linked_evidence = load_jsonl(ROOT / "data/phase2_11E_evidence/phase2_11E_linked_evidence.jsonl")
    linked_by_id = {row["evidence_id"]: row for row in linked_evidence}

    with zipfile.ZipFile(ROOT / "phase2_9_review_supplement.zip") as source:
        prefix = "phase2_9_review_supplement/exports/"
        full_evidence = [json.loads(line) for line in source.read(prefix + "gold_evidence_full_original_fields.jsonl").decode("utf-8").splitlines() if line]
        source_records = [json.loads(line) for line in source.read(prefix + "gold_source_records_full.jsonl").decode("utf-8").splitlines() if line]
        repair_availability = [json.loads(line) for line in source.read(prefix + "gold_repair_commit_availability.jsonl").decode("utf-8").splitlines() if line]
    full_evidence_by_bug: dict[str, list[dict]] = {}
    for row in full_evidence:
        full_evidence_by_bug.setdefault(row["bug_id"], []).append(row)
    source_record_map = {(row["source_file"], row["source_record_id"]): row for row in source_records}
    repair_by_bug: dict[str, list[dict]] = {}
    for row in repair_availability:
        repair_by_bug.setdefault(row["bug_id"], []).append(row)

    source_paths = [
        *sorted(HANDOFF.iterdir()), draft_path, refined_path, candidate_path,
        ROOT / "data/phase2_11E_evidence/phase2_11E_target_issues.jsonl",
        ROOT / "data/phase2_11E_evidence/phase2_11E_linked_evidence.jsonl",
        ROOT / "phase2_9_review_supplement.zip",
        ROOT / "reports/gold_reference_human_review_checklist.md",
        ROOT / "reports/gold_reference_review.md",
        ROOT / "reports/phase2_6_human_review_checklist.md",
        ROOT / "reports/phase2_8_gold_adjudication_support.md",
        ROOT / "reports/phase2_9_abc_gold_adjudication_workbook.md",
    ]
    source_hashes = {str(path.relative_to(ROOT)): sha_file(path) for path in source_paths if path.is_file()}

    records: list[dict] = []
    queue_rows: list[dict] = []
    notes = [
        "# Phase 2.11F-1 Gold Review Notes", "",
        "Status: **AI evidence consolidation only**. No Gold Reference is approved or frozen.", "",
        "## Source selection and version conflicts", "",
        "`data/gold_references_phase2_6_candidate.jsonl` is used as the authoritative review candidate because it is the explicit schema `2.6-audit-remediation-candidate`, cryptographically links each record to the Phase 2.5 refined draft, and adds branch/oracle/path remediation. The original and Phase 2.5 records are preserved as prior versions, not discarded.", "",
        "The Phase 2.11C structural audit disagrees with the Phase 2.6 machine suggestion for three bugs: CorsixTH/CorsixTH#1799, flame-engine/flame#1264, and vcmi/vcmi#5462. The conflict is recorded per target. Neither suggestion is a human decision.", "",
    ]
    mandatory_errors: list[str] = []
    suggestion_conflicts: list[dict] = []

    for bug_id in manifest_ids:
        gold = candidate_map.get(bug_id)
        target = target_snapshots.get(bug_id)
        if gold is None or target is None or bug_id not in draft_map or bug_id not in refined_map:
            mandatory_errors.append(f"{bug_id}: missing Gold version or target snapshot")
            continue
        evidence_entries = full_evidence_by_bug.get(bug_id, [])
        evidence_map = {row["evidence_id"]: row for row in evidence_entries}
        referenced_ids = sorted(evidence_ids_in(gold))
        missing_evidence_ids = sorted(set(referenced_ids) - set(evidence_map))
        unresolved_excerpts = sorted(row["evidence_id"] for row in evidence_entries if not row.get("resolved_exact_excerpt"))
        relevant_source_keys = sorted({(row["source_file"], row["source_record_id"]) for row in evidence_entries})
        supporting_sources = [source_record_map[key] for key in relevant_source_keys if key in source_record_map]
        missing_source_keys = [list(key) for key in relevant_source_keys if key not in source_record_map]

        target_hash = target["original_content_sha256"]
        manifest_hash = manifest_by_id[bug_id]["target_issue"]["content_sha256"]
        target_hash_ok = target_hash == manifest_hash and target["hash_verification"]["status"] == "PASS"
        draft_chain_ok = refined_map[bug_id]["source_phase2_record_sha256"] == canonical_digest(draft_map[bug_id])
        refined_chain_ok = gold["source_phase2_5_record_sha256"] == canonical_digest(refined_map[bug_id])

        conditions = gold.get("trigger_conditions", [])
        paths = gold.get("trigger_paths", [])
        oracles = gold.get("test_oracles", [])
        preventive = gold.get("preventive_oracles", [])
        missing_prerequisites = [
            {"condition_id": item["condition_id"], "description": item["description"], "reason": "necessity_not_established_or_uncertain"}
            for item in conditions if item.get("role") != "root_cause_only"
            and (item.get("necessity") == "not_established" or item.get("status") == "uncertain")
        ]
        unsupported_inferred_steps = [
            {"condition_id": item["condition_id"], "description": item["description"], "status": item.get("status")}
            for item in conditions if item.get("role") != "root_cause_only"
            and item.get("status") in {"inferred", "uncertain"} and item.get("necessity") == "supported"
        ]
        partial_paths = [item["path_id"] for item in paths if not item.get("fully_supported") or item.get("completeness") != "complete"]
        oracle_gaps = [
            {"oracle_id": item["oracle_id"], "completeness": item.get("completeness"), "status": item.get("status"),
             "ambiguity_status": item.get("ambiguity_status"), "ambiguity_reasons": item.get("ambiguity_reasons", [])}
            for item in oracles if item.get("completeness") != "complete" or item.get("status") != "explicit"
            or item.get("ambiguity_status") not in {None, "clear"}
        ]
        expected_gap = None if gold["expected_behavior"].get("status") == "explicit" else {
            "status": gold["expected_behavior"].get("status"),
            "reason": "Expected behavior is inferred rather than explicit in target evidence."
        }
        unavailable_repair_commits = [row for row in repair_by_bug.get(bug_id, []) if not row["found_in_local_dataset"]]
        future_evidence_ids = {row["evidence_id"] for row in evidence_entries if row.get("temporal_role") == "evaluation_only_future"}
        required_step_future_only = []
        for item in conditions:
            ids = set(item.get("evidence_ids", []))
            if item.get("role") != "root_cause_only" and ids and ids <= future_evidence_ids:
                required_step_future_only.append(item["condition_id"])
        oracle_future_evidence = {
            item["oracle_id"]: sorted(set(item.get("evidence_ids", [])) & future_evidence_ids)
            for item in oracles if set(item.get("evidence_ids", [])) & future_evidence_ids
        }
        unresolved_questions = gold.get("phase2_6_review", {}).get("unresolved_human_questions", [])
        fixture_limitations = [text for text in gold.get("known_limitations", []) if any(
            word in text.casefold() for word in ("external", "attached", "fixture", "save", "historical", "timing", "unavailable", "not downloaded")
        )]

        machine_value = gold["phase2_6_machine_suggestion"]["value"]
        audit_value = structural[bug_id]["suggested_review_decision"]
        if machine_value != audit_value:
            conflict = {"bug_id": bug_id, "phase2_6_machine_suggestion": machine_value,
                        "phase2_11C_structural_suggestion": audit_value,
                        "resolution": "UNRESOLVED_AI_SUGGESTION_CONFLICT_HUMAN_DECISION_REQUIRED"}
            suggestion_conflicts.append(conflict)
        else:
            conflict = None

        trace_ok = not missing_evidence_ids and not unresolved_excerpts and not missing_source_keys and target_hash_ok and draft_chain_ok and refined_chain_ok
        if not trace_ok:
            recommendation = "REVIEW_REQUIRED"
        elif (machine_value == "Revise" or partial_paths or oracle_gaps or expected_gap
              or unsupported_inferred_steps or required_step_future_only):
            recommendation = "PROPOSE_REVISE"
        else:
            recommendation = "PROPOSE_APPROVE"
        reason_parts = []
        if partial_paths:
            reason_parts.append(f"partial/unsupported paths: {', '.join(partial_paths)}")
        if oracle_gaps:
            reason_parts.append("oracle is incomplete, inferred, or ambiguous")
        if expected_gap:
            reason_parts.append("expected behavior is inferred")
        if missing_prerequisites:
            reason_parts.append("one or more prerequisite necessities are not established")
        if fixture_limitations:
            reason_parts.append("fixture/environment preservation limitation remains")
        if conflict:
            reason_parts.append("prior AI suggestion conflict is unresolved")
        if not reason_parts:
            reason_parts.append("complete supported path and scorable oracle are present; remaining questions are preservation/governance checks")
        ai_reason = "; ".join(reason_parts) + ". Human semantic approval is still required."

        modifications = []
        if partial_paths:
            modifications.append("Keep affected paths partial/exploratory unless the missing trigger sequence is established from source evidence; do not invent steps.")
        if missing_prerequisites:
            modifications.append("Separate observed setup from proven-required prerequisites and ask the reviewer to resolve necessity.")
        if expected_gap or oracle_gaps:
            modifications.append("Retain inferred/incomplete Oracle labeling and mark scoring not-scorable or exploratory until the expected result is human-confirmed.")
        if unsupported_inferred_steps:
            modifications.append("Review inferred required steps; remove or relabel any step that cannot be supported by target evidence.")
        if fixture_limitations:
            modifications.append("Record unavailable fixture/environment material explicitly; do not substitute repair-only information as reproduction input.")
        if oracle_future_evidence:
            modifications.append("Keep repair/future evidence evaluation-only and verify that it supports the Gold Oracle without entering generation input.")
        if not modifications:
            modifications.append("No semantic rewrite proposed; preserve the candidate and request explicit human approval after evidence review.")

        target_linked_records = [linked_by_id[eid] for eid in target.get("all_evidence_ids", []) if eid in linked_by_id]
        record = {
            "phase": "2.11F-1", "status": "GOLD_REVIEW_REQUIRED_NOT_APPROVED", "bug_id": bug_id,
            "target_issue": {"doc_id": target["doc_id"], "title": target["title"], "body": target["body"],
                "state": target["state"], "created_at": target["created_at"], "closed_at": target["closed_at"],
                "labels": target["labels"], "author": target["author"], "available_comments": target["available_comments"],
                "content_sha256": target_hash, "manifest_content_sha256": manifest_hash,
                "hash_verification_passed": target_hash_ok, "source_provenance": {
                    "dataset": target["source_dataset"], "table": target["source_table"], "record_id": target["source_record_id"]}},
            "authoritative_gold_source": {"path": str(candidate_path.relative_to(ROOT)), "schema_version": gold["schema_version"],
                "file_sha256": sha_file(candidate_path), "record_sha256": canonical_digest(gold),
                "raw_json_line_verbatim": candidate_raw[bug_id], "selection_rationale": "Latest explicit audit-remediation candidate with per-record provenance links; still pending human review."},
            "prior_gold_versions": [
                {"path": str(draft_path.relative_to(ROOT)), "file_sha256": sha_file(draft_path),
                 "record_sha256": canonical_digest(draft_map[bug_id]), "raw_json_line_verbatim": draft_raw[bug_id]},
                {"path": str(refined_path.relative_to(ROOT)), "schema_version": refined_map[bug_id].get("schema_version"),
                 "file_sha256": sha_file(refined_path), "record_sha256": canonical_digest(refined_map[bug_id]),
                 "raw_json_line_verbatim": refined_raw[bug_id]},
            ],
            "gold_candidate": gold,
            "review_projection": {"trigger_preconditions": conditions, "reproduction_paths": paths,
                "expected_behavior": gold["expected_behavior"], "observed_failure": gold["observed_behavior"],
                "test_oracles": oracles, "preventive_oracles": preventive, "known_limitations": gold["known_limitations"],
                "existing_ai_suggestions": {"phase2_6": gold["phase2_6_machine_suggestion"],
                    "phase2_11C_structural": audit_value, "conflict": conflict}},
            "supporting_gold_evidence": evidence_entries, "supporting_source_records": supporting_sources,
            "repair_commit_availability": repair_by_bug.get(bug_id, []),
            "target_linked_evidence_records": target_linked_records,
            "traceability_audit": {"referenced_evidence_ids": referenced_ids, "missing_evidence_ids": missing_evidence_ids,
                "unresolved_exact_excerpts": unresolved_excerpts, "missing_source_record_keys": missing_source_keys,
                "draft_to_refined_record_hash_link_passed": draft_chain_ok,
                "refined_to_phase2_6_record_hash_link_passed": refined_chain_ok,
                "target_manifest_hash_passed": target_hash_ok, "traceability_passed": trace_ok},
            "gap_audit": {"missing_or_uncertain_prerequisites": missing_prerequisites,
                "partial_or_unsupported_path_ids": partial_paths, "unsupported_inferred_required_steps": unsupported_inferred_steps,
                "expected_behavior_gap": expected_gap, "oracle_gaps": oracle_gaps,
                "unresolved_human_questions": unresolved_questions, "fixture_or_environment_limitations": fixture_limitations,
                "required_steps_using_only_future_repair_evidence": required_step_future_only,
                "oracle_future_evidence_ids_evaluation_only": oracle_future_evidence,
                "unavailable_repair_commit_count": len(unavailable_repair_commits)},
            "ai_review": {"recommendation": recommendation, "is_human_decision": False,
                "reason": ai_reason, "proposed_modifications": modifications},
            "human_decision": None, "human_reason": None, "gold_frozen": False,
        }
        records.append(record)
        queue_rows.append({
            "bug_id": bug_id, "issue_title": gold["issue_title"], "authoritative_schema": gold["schema_version"],
            "traceability_passed": trace_ok, "complete_supported_path_count": sum(bool(x.get("fully_supported")) for x in paths),
            "partial_path_count": len(partial_paths), "oracle_gap_count": len(oracle_gaps),
            "expected_behavior_status": gold["expected_behavior"].get("status"),
            "missing_or_uncertain_prerequisite_count": len(missing_prerequisites),
            "unsupported_inferred_required_step_count": len(unsupported_inferred_steps),
            "unavailable_repair_commit_count": len(unavailable_repair_commits),
            "phase2_6_machine_suggestion": machine_value,
            "phase2_11C_structural_suggestion": audit_value,
            "ai_recommendation": recommendation, "ai_reason": ai_reason,
            "human_decision": "", "human_reason": "", "reviewer_name": "", "review_timestamp": "",
        })
        notes.extend([
            f"## `{bug_id}` — {gold['issue_title']}", "",
            f"- AI recommendation: **{recommendation}** (not a human decision).",
            f"- Traceability: {'PASS' if trace_ok else 'REVIEW REQUIRED'}; Gold evidence IDs: {', '.join(referenced_ids)}.",
            f"- Paths: {len(paths)} total; partial/unsupported: {', '.join(partial_paths) if partial_paths else 'none'}.",
            f"- Expected behavior: `{gold['expected_behavior'].get('status')}`; Oracle gaps: {len(oracle_gaps)}.",
            f"- Missing/uncertain prerequisites: {len(missing_prerequisites)}; unsupported inferred required steps: {len(unsupported_inferred_steps)}.",
            f"- Evidence limitations: {'; '.join(fixture_limitations) if fixture_limitations else 'no target-specific fixture limitation beyond recorded snapshot-history limits'}.",
            f"- Outstanding human questions: {'; '.join(unresolved_questions) if unresolved_questions else 'none recorded'}.",
            f"- AI reason: {ai_reason}",
            "- Proposed modifications:", *[f"  - {item}" for item in modifications],
            f"- Suggestion conflict: {json.dumps(conflict, ensure_ascii=False) if conflict else 'none'}.",
            "- Human decision: ______", "- Human reason: ______", "",
        ])

    output_evidence = STAGE_DIR / "phase2_11F_1_gold_evidence.jsonl"
    output_queue = STAGE_DIR / "phase2_11F_1_gold_review_queue.csv"
    output_notes = STAGE_DIR / "phase2_11F_1_gold_review_notes.md"
    output_validation = STAGE_DIR / "phase2_11F_1_validation.json"
    write_jsonl(output_evidence, records)
    with output_queue.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(queue_rows[0])); writer.writeheader(); writer.writerows(queue_rows)
    output_notes.write_text("\n".join(notes), encoding="utf-8")

    source_hashes_after = {name: sha_file(ROOT / name) for name in source_hashes}
    record_ids = [row["bug_id"] for row in records]
    queue_ids = [row["bug_id"] for row in queue_rows]
    mandatory_pass = (
        len(records) == 18 and record_ids == manifest_ids and queue_ids == manifest_ids
        and not mandatory_errors and all(row["traceability_audit"]["traceability_passed"] for row in records)
        and all(row["human_decision"] is None for row in records)
        and all(not row["human_decision"] for row in queue_rows)
        and all(item["matches"] for item in handoff_hash_checks.values())
        and len(approved_pairs) == 108 and all(row["human_decision"] == "APPROVE" for row in approved_pairs)
        and all(source_hashes[name] == source_hashes_after[name] for name in source_hashes)
    )
    validation = {
        "status": "PASS_GOLD_EVIDENCE_EXPORT" if mandatory_pass else "BLOCKED_GOLD_EVIDENCE_EXPORT",
        "counts": {"targets": len(manifest_ids), "gold_records": len(records), "review_queue_rows": len(queue_rows),
            "PROPOSE_APPROVE": sum(row["ai_review"]["recommendation"] == "PROPOSE_APPROVE" for row in records),
            "PROPOSE_REVISE": sum(row["ai_review"]["recommendation"] == "PROPOSE_REVISE" for row in records),
            "REVIEW_REQUIRED": sum(row["ai_review"]["recommendation"] == "REVIEW_REQUIRED" for row in records),
            "supporting_gold_evidence_items": sum(len(row["supporting_gold_evidence"]) for row in records),
            "supporting_source_records_exported_with_repetition_by_bug": sum(len(row["supporting_source_records"]) for row in records)},
        "authoritative_source": {"path": str(candidate_path.relative_to(ROOT)), "schema_version": "2.6-audit-remediation-candidate",
            "sha256": sha_file(candidate_path), "prior_versions": [str(draft_path.relative_to(ROOT)), str(refined_path.relative_to(ROOT))]},
        "target_id_checks": {"manifest_ids": manifest_ids, "gold_ids_equal_manifest": record_ids == manifest_ids,
            "queue_ids_equal_manifest": queue_ids == manifest_ids},
        "traceability": {"all_18_pass": len(records) == 18 and all(row["traceability_audit"]["traceability_passed"] for row in records),
            "mandatory_errors": mandatory_errors, "suggestion_conflicts": suggestion_conflicts},
        "handoff": {"hash_checks": handoff_hash_checks, "E9_approved_pairs_preserved": len(approved_pairs) == 108 and all(row["human_decision"] == "APPROVE" for row in approved_pairs),
            "approved_pair_count": len(approved_pairs), "eligibility_decisions_modified": False},
        "governance": {"human_gold_approvals_recorded": 0, "rubric_approved": False, "ABC_manifest_frozen": False,
            "gold_frozen": False, "official_model_calls": 0, "official_generation_authorized": False},
        "source_integrity": {name: {"before": source_hashes[name], "after": source_hashes_after[name],
            "unchanged": source_hashes[name] == source_hashes_after[name]} for name in source_hashes},
    }
    output_validation.write_text(json.dumps(validation, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    if not mandatory_pass:
        raise SystemExit(json.dumps(validation, ensure_ascii=False))

    with zipfile.ZipFile(STAGE_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(STAGE_DIR.iterdir()):
            info = zipfile.ZipInfo(f"phase2_11F_1_gold_evidence/{path.name}", (2026, 11, 18, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(STAGE_ZIP) as archive:
        if archive.testzip() or len(archive.namelist()) != 4:
            raise SystemExit("ZIP integrity/entry-count validation failed")
    os.replace(STAGE_DIR, FINAL_DIR)
    os.replace(STAGE_ZIP, FINAL_ZIP)
    print(json.dumps({"status": validation["status"], "output_directory": str(FINAL_DIR),
        "zip": str(FINAL_ZIP), "zip_sha256": sha_file(FINAL_ZIP), "counts": validation["counts"],
        "suggestion_conflicts": len(suggestion_conflicts), "E9_approved_pairs_preserved": True,
        "official_model_calls": 0}, indent=2))


if __name__ == "__main__":
    main()
