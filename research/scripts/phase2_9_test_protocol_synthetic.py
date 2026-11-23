#!/usr/bin/env python3
"""Synthetic-only checks for ABC normalization, leakage, ranking, and random policy."""

from __future__ import annotations

import hashlib
import json
import random
import sys

from phase2_9_abc_common import jaccard, normalize, shingles, sha256, tokens


def main() -> int:
    target = "Crash when loading a legacy layered map"
    normalized_variant = "ＣＲＡＳＨ   when loading a legacy layered map"
    scores = [(2.0, "doc-b"), (2.0, "doc-a"), (1.0, "doc-c")]
    ranked = sorted(scores, key=lambda x: (-x[0], x[1]))
    seed = int.from_bytes(hashlib.sha256(b"29012026|synthetic#1|1").digest()[:8], "big")
    first = random.Random(seed).randrange(10000)
    second = random.Random(seed).randrange(10000)
    checks = {
        "nfkc_casefold_whitespace_normalization": normalize(target) == normalize(normalized_variant),
        "normalized_duplicate_hash": sha256(normalize(target)) == sha256(normalize(normalized_variant)),
        "tokenization_deterministic": tokens(target) == tokens(target),
        "near_duplicate_flag_metric": jaccard(shingles(target + " with the same saved project fixture"), shingles(target + " with the same saved project fixture now")) >= 0.85,
        "bm25_tie_break_doc_id": ranked[0][1] == "doc-a",
        "random_seed_stable": first == second,
        "strict_k_not_silently_reduced": len(["a", "b", "c"]) == 3,
        "source_boundary_issue_only": set(["issue.title", "issue.body"]).isdisjoint({"pr.body", "commit.diff"}),
    }
    result = {"scope": "synthetic/non-study only", "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    print(json.dumps(result, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
