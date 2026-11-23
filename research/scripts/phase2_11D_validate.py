#!/usr/bin/env python3
"""Final offline structural validation for Phase 2.11D."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from phase2_11D_cross_project_select import DATA, ROOT, canonical_project, load_jsonl, sample_random


def main() -> int:
    selection_result_path = ROOT / "reports/phase2_11D_validation_results.json"
    base = json.loads(selection_result_path.read_text(encoding="utf-8"))
    targets = {x["bug_id"]: x for x in load_jsonl(DATA / "phase2_9_abc_target_issue_inputs.jsonl")}
    b = load_jsonl(DATA / "phase2_11D_B_cross_project_top3_PROPOSED.jsonl")
    c = load_jsonl(DATA / "phase2_11D_C_cross_project_random3_PROPOSED.jsonl")
    manifest = load_jsonl(DATA / "phase2_11D_ABC_manifest_NOT_FROZEN.jsonl")
    c_map = {x["bug_id"]: x for x in c}
    errors: list[str] = []
    if len(targets) != 18 or len(b) != 18 or len(c) != 18 or len(manifest) != 18:
        errors.append("18-target cardinality invariant failed")
    db = sqlite3.connect(DATA / "phase2_9_abc_issue_corpus.sqlite")
    reproducible_c = True
    for b_record in b:
        bug_id = b_record["bug_id"]
        c_record = c_map.get(bug_id, {"documents": []})
        b_docs, c_docs = b_record["documents"], c_record["documents"]
        if len(b_docs) != 3 or len(c_docs) != 3:
            errors.append(f"{bug_id}: K != 3")
            continue
        b_ids, c_ids = [x["doc_id"] for x in b_docs], [x["doc_id"] for x in c_docs]
        if len(set(b_ids)) != 3 or len(set(c_ids)) != 3 or set(b_ids) & set(c_ids) or bug_id in b_ids + c_ids:
            errors.append(f"{bug_id}: duplicate/self/B-C overlap")
        target_project = canonical_project(targets[bug_id]["project"])
        if any(canonical_project(x["project"]) == target_project for x in b_docs + c_docs):
            errors.append(f"{bug_id}: same-project selection")
        ranks = [x["original_bm25_rank"] for x in b_docs]
        if ranks != sorted(ranks):
            errors.append(f"{bug_id}: B rank order changed")
        for item in b_docs + c_docs:
            row = db.execute("SELECT content_sha256 FROM documents WHERE doc_id=?", (item["doc_id"],)).fetchone()
            if row is None or row[0] != item["content_sha256"]:
                errors.append(f"{bug_id}/{item['doc_id']}: source hash mismatch")
        rerun, _ = sample_random(db, targets[bug_id], b_docs)
        if [(x["doc_id"], x["seed"]) for x in rerun] != [(x["doc_id"], x["seed"]) for x in c_docs]:
            reproducible_c = False
            errors.append(f"{bug_id}: C sampling not reproducible")
    db.close()
    with (DATA / "phase2_11D_old_vs_new_context_comparison.csv").open(encoding="utf-8", newline="") as handle:
        comparison = list(csv.DictReader(handle))
    if len(comparison) != 108:
        errors.append("old/new comparison must contain 108 slot rows")
    prompt = DATA / "phase2_9_abc_generation_prompt_PROPOSED.txt"
    prompt_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()
    if prompt_hash != "d42621f35d0ae6d2b0dcece8291cc686c83fa318e51f2548c73cd7aa5f43206e":
        errors.append("prompt hash mismatch")
    changed_sources = [name for name, value in base["source_hashes_before_and_after"].items()
                       if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != value["before"]]
    if changed_sources:
        errors.append(f"source artifacts changed: {changed_sources}")
    tests = subprocess.run([sys.executable, str(ROOT / "scripts/phase2_11D_test_cross_project.py")],
                           cwd=ROOT, text=True, capture_output=True)
    if tests.returncode:
        errors.append("offline unit tests failed")
    base.update({
        "status": "PASS_STRUCTURAL_CROSS_PROJECT_SELECTION" if not errors else "BLOCKED_CROSS_PROJECT_SELECTION",
        "final_validation_errors": errors, "C_sampling_reproducible": reproducible_c,
        "selected_source_ids_and_hashes_verified": not any("source hash" in x for x in errors),
        "no_duplicate_self_or_BC_overlap": not any("duplicate/self" in x for x in errors),
        "source_artifacts_unchanged": not changed_sources, "changed_source_artifacts": changed_sources,
        "offline_test_command": "python3 scripts/phase2_11D_test_cross_project.py",
        "offline_tests_passed": tests.returncode == 0, "offline_test_output": (tests.stdout + tests.stderr).strip(),
        "official_model_calls": 0, "experiment_frozen": False,
    })
    selection_result_path.write_text(json.dumps(base, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": base["status"], "errors": errors, "C_sampling_reproducible": reproducible_c,
                      "offline_tests_passed": tests.returncode == 0, "source_artifacts_unchanged": not changed_sources,
                      "official_model_calls": 0}, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
