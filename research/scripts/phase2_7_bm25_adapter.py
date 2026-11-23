#!/usr/bin/env python3
"""Deterministic, dependency-free BM25 adapter with fail-closed temporal filters."""

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any


TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text.lower())


def eligibility(doc: dict[str, Any], *, target_bug_id: str, cutoff: str,
                target_content_hashes: set[str]) -> tuple[bool, str]:
    required = {"document_id", "source", "content", "content_timestamp",
                "content_timestamp_verified", "snapshot_verified", "linked_bug_ids",
                "linked_repair_ids", "repair_only"}
    if required - set(doc):
        return False, "missing_required_provenance"
    if not doc["content_timestamp_verified"]:
        return False, "content_timestamp_unverified"
    if not doc["snapshot_verified"]:
        return False, "historical_snapshot_unverified"
    try:
        doc_time = parse_timestamp(doc["content_timestamp"])
        cutoff_time = parse_timestamp(cutoff)
    except (TypeError, ValueError):
        return False, "invalid_or_unknown_timestamp"
    if doc_time >= cutoff_time:
        return False, "not_strictly_pre_cutoff"
    if target_bug_id in doc["linked_bug_ids"]:
        return False, "target_bug_linked"
    if doc["linked_repair_ids"]:
        return False, "repair_record_linked"
    if doc["repair_only"]:
        return False, "repair_only"
    if content_sha256(doc["content"]) in target_content_hashes:
        return False, "exact_target_content_duplicate"
    return True, "eligible"


def search(documents: list[dict[str, Any]], query: str, *, target_bug_id: str,
           cutoff: str, target_content_hashes: set[str], top_k: int = 10,
           k1: float = 1.5, b: float = 0.75) -> dict[str, Any]:
    screened = []
    eligible_docs = []
    for doc in documents:
        allowed, reason = eligibility(doc, target_bug_id=target_bug_id, cutoff=cutoff,
                                      target_content_hashes=target_content_hashes)
        item = {"document_id": doc.get("document_id"), "source": doc.get("source"),
                "content_timestamp": doc.get("content_timestamp"), "cutoff": cutoff,
                "eligible": allowed, "filter_reason": reason,
                "sha256": content_sha256(doc.get("content", ""))}
        screened.append(item)
        if allowed:
            eligible_docs.append((doc, item))
    corpora = [tokens(doc["content"]) for doc, _ in eligible_docs]
    n_docs = len(corpora)
    avgdl = sum(map(len, corpora)) / n_docs if n_docs else 0.0
    query_terms = list(dict.fromkeys(tokens(query)))
    document_frequency = {term: sum(term in corpus for corpus in corpora) for term in query_terms}
    results = []
    for (doc, provenance), corpus in zip(eligible_docs, corpora):
        counts = Counter(corpus)
        score = 0.0
        for term in query_terms:
            freq = counts[term]
            if not freq:
                continue
            df = document_frequency[term]
            inverse = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            denom = freq + k1 * (1 - b + b * len(corpus) / avgdl)
            score += inverse * freq * (k1 + 1) / denom
        result = dict(provenance)
        result["bm25_score"] = round(score, 12)
        results.append(result)
    results.sort(key=lambda x: (-x["bm25_score"], x["document_id"]))
    return {"adapter": "phase2_7_bm25_adapter", "parameters": {"k1": k1, "b": b, "top_k": top_k},
            "query_sha256": content_sha256(query), "eligible_count": n_docs,
            "screened": screened, "results": results[:top_k]}
