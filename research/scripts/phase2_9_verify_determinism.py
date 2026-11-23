#!/usr/bin/env python3
"""Rerun retrieval/random preparation and verify byte-stable manifests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    ROOT / "data/phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl",
    ROOT / "data/phase2_9_abc_top3_similar_PROPOSED.jsonl",
    ROOT / "data/phase2_9_abc_retrieval_feasibility.jsonl",
    ROOT / "data/phase2_9_abc_random_context_PROPOSED.jsonl",
)


def hashes() -> dict[str, str]:
    return {x.name: hashlib.sha256(x.read_bytes()).hexdigest() for x in FILES}


def main() -> int:
    before = hashes()
    subprocess.run([sys.executable, str(ROOT / "scripts/phase2_9_bm25_retrieve.py")], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, str(ROOT / "scripts/phase2_9_select_random_context.py")], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
    after = hashes()
    result = {"status": "PASS" if before == after else "FAIL", "byte_stable": before == after,
              "files": {name: {"before": before[name], "after": after[name]} for name in before}}
    print(json.dumps(result, indent=2))
    return 0 if before == after else 1


if __name__ == "__main__":
    sys.exit(main())
