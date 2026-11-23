#!/usr/bin/env python3
"""Shared deterministic text helpers for Phase 2.9 ABC preparation."""

from __future__ import annotations

import hashlib
import re
import unicodedata


TOKEN_RE = re.compile(r"[a-z0-9_]+")
SOLUTION_RE = re.compile(r"\b(fix(?:ed|es|ing)?|resolved?|solution|workaround|patch|root\s+cause)\b", re.I)


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text or "").casefold().split())


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(normalize(text))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shingles(text: str, n: int = 5) -> set[tuple[str, ...]]:
    words = tokens(text)
    return {tuple(words[i:i+n]) for i in range(max(0, len(words)-n+1))}


def jaccard(left: set, right: set) -> float:
    return len(left & right) / len(left | right) if left | right else 1.0


def snippet(title: str, body: str, limit: int = 360) -> str:
    return " ".join(f"{title} — {body}".split())[:limit]
