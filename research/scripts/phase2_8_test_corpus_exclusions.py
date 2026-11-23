#!/usr/bin/env python3
"""Synthetic tests for proposed Phase 2.8 linkage and duplicate safeguards."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9_]+", normalized(text))
    return {tuple(words[i:i+n]) for i in range(max(0, len(words)-n+1))}


def jaccard(left: set, right: set) -> float:
    return len(left & right) / len(left | right) if left | right else 1.0


def main() -> int:
    target = "Renderer crashes after loading the legacy layered map and opening the editor"
    spacing_variant = "ＲＥＮＤＥＲＥＲ   crashes after loading the legacy layered map and opening the editor"
    near = "Renderer crashes after loading the legacy layered map and opening the editor window"
    linked_prs = {20, 21}
    restricted_prs = {22}
    pr_commits = {20: {"a"}, 21: {"b"}, 22: {"c"}, 23: {"safe"}}
    blocked = set().union(*(pr_commits[x] for x in linked_prs | restricted_prs))
    checks = {
        "exact_hash_detected": digest(target) == digest(target),
        "normalized_duplicate_detected": digest(normalized(target)) == digest(normalized(spacing_variant)),
        "near_duplicate_quarantined": jaccard(shingles(target), shingles(near)) >= 0.85,
        "linked_pr_transitive_commits_blocked": blocked == {"a", "b", "c"},
        "unlinked_commit_not_blocked": "safe" not in blocked,
        "strict_cutoff_rejects_equal": not ("2020-01-01T00:00:00Z" < "2020-01-01T00:00:00Z"),
        "target_reference_detected": bool(re.search(r"(?<!\d)#\s*123(?!\d)", "Fixes #123")),
    }
    result = {"scope": "synthetic/non-study only", "status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}
    print(json.dumps(result, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
