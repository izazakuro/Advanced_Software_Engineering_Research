#!/usr/bin/env python3
"""Focused regression tests for the CSPC human-workbook validator."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
VALIDATOR = HERE / "validate_stage1_cspc_human_review.py"
SOURCE = HERE / "stage1_ai_evaluation" / "stage1_cspc_human_verification_preparation" / "cspc_human_verification_workbook.csv"


class ValidatorTests(unittest.TestCase):
    def run_case(self, mutate):
        with SOURCE.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            rows, fields = list(reader), list(reader.fieldnames or [])
        mutate(rows, fields)
        with tempfile.TemporaryDirectory() as tmp:
            workbook = Path(tmp) / "review.csv"
            report = Path(tmp) / "report.json"
            with workbook.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            result = subprocess.run(
                [sys.executable, str(VALIDATOR), "--workbook", str(workbook), "--report", str(report)],
                capture_output=True,
                text=True,
                check=False,
            )
            return result.returncode, json.loads(report.read_text(encoding="utf-8"))

    @staticmethod
    def accept_all(rows, _fields):
        for row in rows:
            row["human_decision"] = "ACCEPT"

    def test_all_explicit_accepts_pass_without_overwriting_ai(self):
        code, report = self.run_case(self.accept_all)
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "PASS_READY_FOR_SEPARATE_SCORE_LOCK_STEP")
        self.assertFalse(report["ai_assessment_overwrite_detected"])

    def test_override_requires_reason(self):
        def mutate(rows, fields):
            self.accept_all(rows, fields)
            rows[0]["human_decision"] = "OVERRIDE_TO_0"
        code, report = self.run_case(mutate)
        self.assertEqual(code, 1)
        self.assertIn("OVERRIDE_WITHOUT_REASON", {e["code"] for e in report["errors"]})

    def test_unresolved_cannot_finalize(self):
        def mutate(rows, fields):
            self.accept_all(rows, fields)
            rows[0]["human_decision"] = "UNRESOLVED"
            rows[0]["human_correction_reason"] = "Needs another reviewer"
        code, report = self.run_case(mutate)
        self.assertEqual(code, 1)
        self.assertIn("UNRESOLVED_NOT_FINALIZABLE", {e["code"] for e in report["errors"]})

    def test_ai_judgment_cannot_be_changed(self):
        def mutate(rows, fields):
            self.accept_all(rows, fields)
            rows[0]["ai_cspc_judgment"] = "0" if rows[0]["ai_cspc_judgment"] == "1" else "1"
        code, report = self.run_case(mutate)
        self.assertEqual(code, 1)
        self.assertTrue(report["ai_assessment_overwrite_detected"])

    def test_condition_mapping_header_is_rejected(self):
        def mutate(rows, fields):
            self.accept_all(rows, fields)
            fields.append("condition_label")
            for row in rows:
                row["condition_label"] = "A"
        code, report = self.run_case(mutate)
        self.assertEqual(code, 1)
        self.assertTrue(report["condition_mapping_exposure_detected"])


if __name__ == "__main__":
    unittest.main()
