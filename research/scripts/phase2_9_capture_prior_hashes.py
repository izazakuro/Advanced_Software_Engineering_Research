#!/usr/bin/env python3
"""Hash pre-Phase-2.9 research artifacts to detect accidental modification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    files = []
    for base in (ROOT, ROOT / "data", ROOT / "reports", ROOT / "scripts"):
        for path in base.iterdir():
            if not path.is_file():
                continue
            if path.name.startswith("phase2_9_") or path.name == "test_scenario_evaluation_rubric_abc_PROPOSED.md":
                continue
            if base == ROOT and path.suffix not in {".md", ".txt", ".json", ".jsonl"}:
                continue
            files.append(path)
    records = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(set(files))}
    out = {"status": "PRE_PHASE_2_9_ARTIFACT_HASHES", "file_count": len(records), "files": records}
    (ROOT / "data/phase2_9_prior_artifact_hashes.json").write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "CAPTURED", "file_count": len(records)}, indent=2))


if __name__ == "__main__":
    main()
