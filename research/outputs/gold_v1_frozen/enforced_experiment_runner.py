#!/usr/bin/env python3
"""Offline-only Phase 2.10B runner: validates and renders a dry-run plan, never calls a model."""

from __future__ import annotations

import hashlib
import json
import sqlite3

from phase2_10B_select_contexts import ROOT, run
from phase2_11F_3_generation_input import load_safe_projection, render_abc_prompt, serialize_context_issue


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_safe_contexts(contexts: list[dict], corpus: sqlite3.Connection) -> list[dict]:
    safe_contexts: list[dict] = []
    for context in contexts:
        row = corpus.execute("SELECT doc_id, title, body FROM documents WHERE doc_id = ?", (context["doc_id"],)).fetchone()
        if row is None:
            raise ValueError(f"missing corpus document {context['doc_id']}")
        safe_contexts.append(serialize_context_issue({"doc_id": row[0], "title": row[1], "body": row[2]}))
    return safe_contexts


def main() -> None:
    config = json.loads((ROOT / "data/phase2_10B_experiment_config_PROPOSED.json").read_text(encoding="utf-8"))
    template_path = ROOT / config["prompt"]["path"]
    template = template_path.read_text(encoding="utf-8")
    targets = load_safe_projection()
    hypothetical = run(ROOT / "data/phase2_10B_eligibility_and_leakage_decision_queue.csv", True)
    actual_gate = run(ROOT / "data/phase2_10B_eligibility_and_leakage_decision_queue.csv", False)
    db = sqlite3.connect(ROOT / "data/phase2_9_abc_issue_corpus.sqlite")
    plans = []
    for selection in hypothetical:
        bug = selection["target_bug_id"]
        conditions = {"A": [], "B": selection["group_B"]["selected"], "C": selection["group_C"]["selected"]}
        for condition, contexts in conditions.items():
            safe_contexts = load_safe_contexts(contexts, db)
            rendered = render_abc_prompt(template, targets[bug], safe_contexts)
            for repetition in range(1, 4):
                plans.append({"target_bug_id": bug, "condition": condition, "repetition": repetition,
                              "context_document_ids": [x["doc_id"] for x in contexts], "rendered_prompt_sha256": sha(rendered),
                              "status": "SYNTHETIC_DRY_RUN_ONLY_NOT_EXECUTABLE"})
    db.close()
    blockers = {
        "targets_with_B_pending": sum(x["group_B"]["status"] == "BLOCKED_PENDING_HUMAN_DECISION" for x in actual_gate),
        "targets_with_C_pending": sum(x["group_C"]["status"] == "BLOCKED_PENDING_HUMAN_DECISION" for x in actual_gate),
        "model_identifier_missing": config["model_identifier"] is None,
        "model_version_missing": config["model_version"] is None,
        "rubric_unapproved": config["rubric"]["human_approval"] is None,
        "gold_unapproved": True,
        "experiment_unapproved": config["human_approval"] is None,
    }
    output = {"status": "DRY_RUN_PASSED_OFFICIAL_EXECUTION_BLOCKED", "planned_records": len(plans), "official_model_calls": 0,
              "prompt_hash_verified": sha(template) == config["prompt"]["actual_sha256"], "unique_targets": len(targets),
              "conditions": ["A", "B", "C"], "repetitions": 3, "structural_plan": plans, "blockers": blockers}
    path = ROOT / "data/phase2_10B_dry_run_results.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: output[k] for k in ["status", "planned_records", "official_model_calls", "prompt_hash_verified", "unique_targets"]}, indent=2))


if __name__ == "__main__":
    main()
