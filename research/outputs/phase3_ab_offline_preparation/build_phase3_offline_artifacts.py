#!/usr/bin/env python3
"""Deterministically build Phase 3 offline manifests. Performs no model calls."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sqlite3


HERE = Path(__file__).resolve().parent
RESEARCH = HERE.parents[1]
HANDOFF = RESEARCH / "phase2_11E_9_codex_handoff"
FROZEN = RESEARCH / "outputs/gold_v1_frozen"
SEED = 29012026


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def text_sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def load_serializer():
    path = FROZEN / "generation_input_serializer.py"
    spec = importlib.util.spec_from_file_location("frozen_generation_serializer", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def canonical_project(doc_id: str) -> str:
    return doc_id.rsplit("#", 1)[0].casefold()


def validate_and_load_contexts() -> tuple[list[dict], dict, list[str]]:
    errors: list[str] = []
    manifest_path = HANDOFF / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl"
    rows = jsonl(manifest_path)
    approvals: dict[tuple[str, str, int], dict] = {}
    with (HANDOFF / "approved_pairs_108.csv").open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            approvals[(row["target_bug_id"], row["group"], int(row["slot"]))] = row
    db = sqlite3.connect(RESEARCH / "data/phase2_9_abc_issue_corpus.sqlite")
    verified: list[dict] = []
    seen_pairs: set[tuple[str, str, int]] = set()
    for row in rows:
        bug = row["target_bug_id"]
        target_project = row["target_issue"]["canonical_project"].casefold()
        group_records = {}
        for group in ("B", "C"):
            docs = row[f"group_{group}"]["documents"]
            if len(docs) != 3:
                errors.append(f"{bug}/{group}: expected three contexts")
            group_records[group] = []
            for index, doc in enumerate(docs, 1):
                slot = doc.get("post_filter_selection_position") if group == "B" else doc.get("slot")
                key = (bug, group, int(slot))
                seen_pairs.add(key)
                approval = approvals.get(key)
                if not approval or approval["human_decision"] != "APPROVE" or approval["candidate_doc_id"] != doc["doc_id"]:
                    errors.append(f"{bug}/{group}/{slot}: authoritative approval mismatch")
                corpus_row = db.execute("SELECT title, body, created_at, content_sha256 FROM documents WHERE doc_id=?", (doc["doc_id"],)).fetchone()
                if corpus_row is None:
                    errors.append(f"{bug}/{group}/{slot}: source content missing")
                    continue
                title, body, created_at, corpus_hash = corpus_row
                calculated = text_sha((title or "") + "\n" + (body or ""))
                if calculated != doc["content_sha256"] or corpus_hash != doc["content_sha256"]:
                    errors.append(f"{bug}/{group}/{slot}: content hash mismatch")
                if canonical_project(doc["doc_id"]) == target_project:
                    errors.append(f"{bug}/{group}/{slot}: not cross-project")
                group_records[group].append({
                    "slot": int(slot), "doc_id": doc["doc_id"], "content_sha256": doc["content_sha256"],
                    "created_at": created_at, "project": doc["project"],
                    "original_bm25_rank": doc.get("original_bm25_rank"), "seed": doc.get("seed"),
                    "human_decision": approval["human_decision"] if approval else None,
                    "title": title or "", "body": body or "",
                })
            if [x["slot"] for x in group_records[group]] != [1, 2, 3]:
                errors.append(f"{bug}/{group}: slot order changed")
            if group == "B" and [x["original_bm25_rank"] for x in group_records[group]] != sorted(x["original_bm25_rank"] for x in group_records[group]):
                errors.append(f"{bug}/B: original BM25 ranks reordered")
        verified.append({"target_bug_id": bug, "target_issue": row["target_issue"], "groups": group_records})
    db.close()
    if len(rows) != 18 or len(approvals) != 108 or len(seen_pairs) != 108 or set(approvals) != seen_pairs:
        errors.append(f"cardinality mismatch: targets={len(rows)}, approvals={len(approvals)}, pairs={len(seen_pairs)}")
    integrity = {
        "status": "PASS" if not errors else "FAIL",
        "authoritative_manifest": str(manifest_path.relative_to(RESEARCH)),
        "authoritative_manifest_sha256": sha(manifest_path),
        "approval_file": str((HANDOFF / "approved_pairs_108.csv").relative_to(RESEARCH)),
        "approval_file_sha256": sha(HANDOFF / "approved_pairs_108.csv"),
        "targets": len(rows), "approved_B_assignments": 54, "approved_C_assignments": 54,
        "approved_total_assignments": len(approvals), "verified_source_texts": len(seen_pairs),
        "cross_project_definition": "canonical repository owner/name inequality",
        "retrospective_snapshot_semantics": True,
        "strict_historical_availability_claimed": False,
        "selection_preserved": True,
        "global_random_seed": SEED,
        "random_length_tolerance": 0.20,
        "known_boundary_limitations": ["fork relationships not exhaustively resolved", "repository transfers/renames may obscure lineage", "shared code lineage and tightly coupled auxiliary repositories are not ruled out"],
        "errors": errors,
        "records": [{"target_bug_id": x["target_bug_id"], "B": [{k: v for k, v in d.items() if k not in {"title", "body"}} for d in x["groups"]["B"]], "C": [{k: v for k, v in d.items() if k not in {"title", "body"}} for d in x["groups"]["C"]]} for x in verified],
    }
    return verified, integrity, errors


def build_schedule(verified: list[dict], serializer, prompt: str, schema_hash: str) -> tuple[list[dict], list[dict]]:
    projections = {x["bug_id"]: x for x in jsonl(FROZEN / "generation_input_safe_projection.jsonl")}
    projection_errors = serializer.validate_projection(list(projections.values()))
    if projection_errors:
        raise ValueError("frozen projection failed validation: " + "; ".join(projection_errors))
    rng = random.Random(SEED)
    target_order = [row["target_bug_id"] for row in verified]
    rng.shuffle(target_order)
    by_bug = {row["target_bug_id"]: row for row in verified}
    schedules: list[dict] = []
    payloads: list[dict] = []
    execution_order = 0
    for repeat in range(1, 4):
        for round_index in range(3):
            for target_index, bug in enumerate(target_order):
                condition = "ABC"[(round_index + target_index + repeat - 1) % 3]
                execution_order += 1
                source_contexts = [] if condition == "A" else by_bug[bug]["groups"][condition]
                safe_contexts = [serializer.serialize_context_issue({"doc_id": x["doc_id"], "title": x["title"], "body": x["body"]}) for x in source_contexts]
                for safe, source in zip(safe_contexts, source_contexts):
                    if safe["content_sha256"] != source["content_sha256"]:
                        raise ValueError(f"{bug}/{condition}: serializer hash mismatch")
                template = prompt.replace("{{BUG_ID}}", bug)
                rendered = serializer.render_abc_prompt(template, projections[bug], safe_contexts)
                task_id = f"P3-{execution_order:03d}-{condition}-R{repeat}"
                output_path = f"responses/formal/{task_id}/raw_response.bin"
                refs = [{"slot": x["slot"], "doc_id": x["doc_id"], "content_sha256": x["content_sha256"]} for x in source_contexts]
                schedule = {
                    "task_id": task_id, "target_bug_id": bug, "condition": condition, "repeat_index": repeat,
                    "context_manifest_reference": "phase2_11E_9_codex_handoff/phase2_11D_ABC_manifest_NOT_FROZEN.jsonl",
                    "context_document_ids": [x["doc_id"] for x in refs], "context_content_sha256": [x["content_sha256"] for x in refs],
                    "prompt_sha256": text_sha(rendered), "planned_execution_order": execution_order,
                    "output_artifact_path": output_path, "execution_status": "PLANNED_NOT_AUTHORIZED",
                }
                payload = {
                    "task_id": task_id, "bug_id": bug, "condition": condition, "repeat_index": repeat,
                    "prompt_sha256": text_sha(rendered), "context_documents": refs, "rendered_prompt": rendered,
                    "output_artifact_path": output_path, "generation_schema_sha256": schema_hash,
                    "authorization_status": "NOT_AUTHORIZED_FOR_EXECUTION",
                }
                schedules.append(schedule); payloads.append(payload)
    return schedules, payloads


def fixture_response(bug: str, ids=("S1", "S2", "S3"), missing_trigger=False, bad_order=False, wrong_oracle=False) -> dict:
    scenarios = []
    for index, scenario_id in enumerate(ids, 1):
        scenarios.append({
            "scenario_id": scenario_id, "preconditions": ["synthetic setup"], "environment_assumptions": [],
            "trigger_conditions": [] if missing_trigger and index == 1 else ["synthetic trigger"],
            "ordered_execution_steps": [{"step": 2 if bad_order and index == 1 else 1, "action": "perform synthetic action"}],
            "expected_observable_behavior": "synthetic observation",
            "test_oracle": "incorrect synthetic oracle" if wrong_oracle else "observe the synthetic result",
            "uncertainty_or_unsupported_assumptions": [],
        })
    return {"bug_id": bug, "scenarios": scenarios}


def main() -> None:
    verified, integrity, errors = validate_and_load_contexts()
    write_json(HERE / "phase3_approved_context_integrity.json", integrity)
    if errors:
        raise SystemExit("\n".join(errors))
    serializer = load_serializer()
    prompt_path = HERE / "phase3_generation_prompt_v1_PROPOSED.txt"
    schema_path = HERE / "phase3_generation_output.schema.json"
    formal, payloads = build_schedule(verified, serializer, prompt_path.read_text(encoding="utf-8"), sha(schema_path))
    write_jsonl(HERE / "phase3_formal_162_task_schedule.jsonl", formal)
    write_jsonl(HERE / "phase3_formal_162_task_payloads_NOT_AUTHORIZED.jsonl", payloads)
    # Six bugs are selected before outputs by taking the first six of the seeded target permutation.
    ordered_bugs = []
    for row in formal:
        if row["target_bug_id"] not in ordered_bugs:
            ordered_bugs.append(row["target_bug_id"])
    pilot_bugs = set(ordered_bugs[:6])
    pilot_source = [row for row in formal if row["repeat_index"] == 1 and row["target_bug_id"] in pilot_bugs]
    pilot_source.sort(key=lambda x: x["planned_execution_order"])
    pilot = []
    pilot_payloads = []
    payload_by_task = {x["task_id"]: x for x in payloads}
    for order, row in enumerate(pilot_source, 1):
        old_task = row["task_id"]
        new = dict(row); new["task_id"] = f"P3P-{order:02d}-{row['condition']}"; new["planned_execution_order"] = order
        new["output_artifact_path"] = f"responses/pilot/{new['task_id']}/raw_response.bin"; new["execution_status"] = "ENGINEERING_PILOT_NOT_AUTHORIZED"
        pilot.append(new)
        payload = dict(payload_by_task[old_task]); payload["task_id"] = new["task_id"]; payload["output_artifact_path"] = new["output_artifact_path"]
        pilot_payloads.append(payload)
    write_jsonl(HERE / "phase3_pilot_18_task_schedule.jsonl", pilot)
    write_jsonl(HERE / "phase3_pilot_18_task_payloads_NOT_AUTHORIZED.jsonl", pilot_payloads)
    fixtures = [
        {"fixture_id": "full_tcc", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1")), "expected_validation": "STRUCTURALLY_VALID", "human_judgments": [{"metric": "tcc", "numerator": 2, "denominator": 2}]},
        {"fixture_id": "partial_tcc", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1")), "expected_validation": "STRUCTURALLY_VALID", "human_judgments": [{"metric": "tcc", "numerator": 1, "denominator": 2}]},
        {"fixture_id": "missing_trigger", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1", missing_trigger=True)), "expected_validation": "SEMANTICALLY_INCOMPLETE"},
        {"fixture_id": "incorrect_trigger_order", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1", bad_order=True)), "expected_validation": "SCHEMA_FAILURE"},
        {"fixture_id": "incomplete_scenario_path", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1", missing_trigger=True)), "expected_validation": "SEMANTICALLY_INCOMPLETE", "human_judgments": [{"metric": "cspc", "numerator": 0, "denominator": 1}]},
        {"fixture_id": "incorrect_oracle", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1", wrong_oracle=True)), "expected_validation": "STRUCTURALLY_VALID", "human_judgments": [{"metric": "strict_oracle", "numerator": 0, "denominator": 1}]},
        {"fixture_id": "pending_oracle_exclusion", "synthetic": True, "oracle_id": "tmodloader/tmodloader#2644::O1", "expected_disposition": "EXCLUDED_PENDING"},
        {"fixture_id": "multiple_oracle_entries", "synthetic": True, "oracle_ids": ["rptools/maptool#2416::O1", "rptools/maptool#2416::O2"], "expected_included": 2},
        {"fixture_id": "invalid_json", "synthetic": True, "raw_response": "{not-json", "expected_validation": "PARSING_FAILURE"},
        {"fixture_id": "duplicate_scenarios", "synthetic": True, "raw_response": json.dumps(fixture_response("synthetic/project#1", ids=("S1", "S1", "S3"))), "expected_validation": "SCHEMA_FAILURE"},
        {"fixture_id": "missing_response", "synthetic": True, "raw_response": None, "expected_validation": "GENERATION_FAILURE"},
        {"fixture_id": "context_manifest_mismatch", "synthetic": True, "expected_adapter_error": "context count or identity mismatch"},
        {"fixture_id": "unsafe_context_serialization", "synthetic": True, "expected_serializer_error": "context source keys must be exactly"},
        {"fixture_id": "prompt_hash_mismatch", "synthetic": True, "expected_adapter_error": "rendered prompt hash mismatch"},
    ]
    write_jsonl(HERE / "phase3_mock_fixtures.jsonl", fixtures)
    build_meta = {
        "status": "OFFLINE_ARTIFACTS_BUILT", "official_model_calls": 0, "bm25_indexes_built": 0,
        "formal_tasks": len(formal), "pilot_tasks": len(pilot), "seed": SEED,
        "schedule_algorithm": "seeded target permutation; repeat-major, 3 rounds; condition=(round+target_index+repeat-1) mod 3",
        "pilot_selection": "first six unique bugs in the pre-output seeded formal order",
        "prompt_template_sha256": sha(prompt_path), "schema_sha256": sha(schema_path),
        "serializer_policy_version": serializer.POLICY_VERSION,
    }
    write_json(HERE / "phase3_build_metadata.json", build_meta)
    print(json.dumps(build_meta, indent=2))


if __name__ == "__main__":
    main()
