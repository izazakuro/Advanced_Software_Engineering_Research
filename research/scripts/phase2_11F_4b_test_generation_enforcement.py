#!/usr/bin/env python3
"""Focused regression tests for the Phase 2.11F-4b generation boundary."""

from __future__ import annotations

import ast
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from phase2_11F_3_generation_input import (
    POLICY_VERSION,
    content_sha,
    load_safe_projection,
    render_abc_prompt,
    serialize_context_issue,
)
from phase2_10B_experiment_runner import load_safe_contexts
from phase2_10B_select_contexts import run as select_contexts

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "Target={{TARGET_TITLE}}\n{{TARGET_BODY}}\nContexts:\n{{ADDITIONAL_CONTEXT_OR_NONE}}"


def safe_target(title: str = "Target title", body: str = "Target body") -> dict:
    bug_id = "owner/repo#1"
    return {
        "bug_id": bug_id,
        "target_issue": {"doc_id": bug_id, "title": title, "body": body,
                         "original_content_sha256": content_sha(title, body), "created_at": "2020-01-01T00:00:00Z"},
        "gold_fields_in_model_visible_content": [],
        "model_visible_sources": ["target_issue.title", "target_issue.body"],
        "generation_input_policy_version": POLICY_VERSION,
    }


class GenerationBoundaryTests(unittest.TestCase):
    def test_target_and_context_title_body_are_retained(self) -> None:
        context = serialize_context_issue({"doc_id": "other/repo#2", "title": "Context title", "body": "Context body"})
        rendered = render_abc_prompt(TEMPLATE, safe_target(), [context])
        self.assertIn("Target=Target title\nTarget body", rendered)
        self.assertIn("Context 1 (other/repo#2)\nTitle: Context title\nBody:\nContext body", rendered)

    def test_evaluation_and_repair_metadata_cannot_enter(self) -> None:
        secrets = ["EVAL_SECRET", "REPAIR_PR_SECRET", "COMMIT_SECRET", "DIFF_SECRET",
                   "GOLD_ANNOTATION_SECRET", "AUDIT_SECRET"]
        target = safe_target()
        target["repair_evidence_summary"] = secrets[1]
        target["audit_log"] = secrets[-1]
        with self.assertRaisesRegex(ValueError, "allowlist"):
            render_abc_prompt(TEMPLATE, target, [])
        for key, secret in (("evidence_ids", secrets[0]), ("repair_pr", secrets[1]),
                            ("commit", secrets[2]), ("diff", secrets[3]),
                            ("gold_annotation", secrets[4]), ("audit_metadata", secrets[5])):
            raw = {"doc_id": "other/repo#2", "title": "Context title", "body": "Context body", key: secret}
            with self.assertRaisesRegex(ValueError, "exactly"):
                serialize_context_issue(raw)
        clean = render_abc_prompt(TEMPLATE, safe_target(), [])
        self.assertTrue(all(secret not in clean for secret in secrets))

    def test_missing_projection_fails_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing.jsonl"
            with self.assertRaises(FileNotFoundError):
                load_safe_projection(missing)

    def test_invalid_projection_fails_without_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.jsonl"
            bad = safe_target()
            bad["gold_annotation"] = "must never be model-visible"
            path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid approved"):
                load_safe_projection(path)

    def test_repository_safe_projection_loads(self) -> None:
        rows = load_safe_projection()
        self.assertEqual(len(rows), 18)
        self.assertTrue(all(row["gold_fields_in_model_visible_content"] == [] for row in rows.values()))

    def test_runner_has_no_raw_target_or_local_render_fallback(self) -> None:
        path = ROOT / "scripts/phase2_10B_experiment_runner.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("phase2_9_abc_target_issue_inputs", source)
        self.assertNotIn("load_jsonl", source)
        self.assertFalse(any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "render"
                             for node in ast.walk(tree)))
        self.assertIn("load_safe_projection", source)
        self.assertIn("serialize_context_issue", source)
        self.assertIn("render_abc_prompt", source)

    def test_all_actual_abc_inputs_render_through_boundary(self) -> None:
        targets = load_safe_projection()
        selections = select_contexts(ROOT / "data/phase2_10B_eligibility_and_leakage_decision_queue.csv", True)
        template = (ROOT / "data/phase2_9_abc_generation_prompt_PROPOSED.txt").read_text(encoding="utf-8")
        db = sqlite3.connect(ROOT / "data/phase2_9_abc_issue_corpus.sqlite")
        rendered_count = 0
        try:
            for selection in selections:
                bug_id = selection["target_bug_id"]
                for contexts in ([], selection["group_B"]["selected"], selection["group_C"]["selected"]):
                    safe_contexts = load_safe_contexts(contexts, db)
                    rendered = render_abc_prompt(template, targets[bug_id], safe_contexts)
                    self.assertNotIn("{{TARGET_TITLE}}", rendered)
                    self.assertNotIn("{{TARGET_BODY}}", rendered)
                    self.assertNotIn("{{ADDITIONAL_CONTEXT_OR_NONE}}", rendered)
                    rendered_count += 1
        finally:
            db.close()
        self.assertEqual(rendered_count, 54)

    def test_all_known_prompt_or_model_entry_points_use_boundary(self) -> None:
        offenders: list[str] = []
        discovered: list[str] = []
        for path in sorted((ROOT / "scripts").glob("*.py")):
            if "_test_" in path.name or path.name.startswith("test_"):
                continue
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            defines_legacy_render = any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "render"
                                        for node in ast.walk(tree))
            call_names: list[str] = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                parts: list[str] = []
                current = node.func
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name):
                    parts.append(current.id)
                call_names.append(".".join(reversed(parts)))
            uses_renderer = any(name.endswith("render_abc_prompt") for name in call_names)
            calls_model = any(name.endswith(("responses.create", "chat.completions.create", "generate_content", "client.invoke"))
                              for name in call_names)
            if defines_legacy_render or uses_renderer or calls_model:
                discovered.append(path.name)
                imports_boundary = any(isinstance(node, ast.ImportFrom)
                                       and node.module == "phase2_11F_3_generation_input"
                                       and any(alias.name == "render_abc_prompt" for alias in node.names)
                                       for node in ast.walk(tree))
                if defines_legacy_render or not imports_boundary:
                    offenders.append(path.name)
        self.assertIn("phase2_10B_experiment_runner.py", discovered)
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
