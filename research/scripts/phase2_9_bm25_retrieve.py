#!/usr/bin/env python3
"""Deterministic research-only BM25 over the Phase 2.9 Issue corpus."""

from __future__ import annotations

import difflib
import heapq
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from datetime import datetime

from phase2_9_abc_common import SOLUTION_RE, jaccard, normalize, shingles, snippet, tokens


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data/phase2_9_abc_issue_corpus.sqlite"
TOP10_PATH = ROOT / "data/phase2_9_abc_bm25_top10_REVIEW_REQUIRED.jsonl"
TOP3_PATH = ROOT / "data/phase2_9_abc_top3_similar_PROPOSED.jsonl"
SUMMARY_PATH = ROOT / "data/phase2_9_abc_retrieval_feasibility.jsonl"
K1, B = 1.5, 0.75
HEAP_SIZE = 80


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def risk(target: dict, candidate: tuple) -> dict:
    doc_id, project, number, title, body, created_at, token_count, content_hash = candidate
    target_text = f"{target['title']}\n{target['body']}"
    candidate_text = f"{title}\n{body}"
    shingle_score = jaccard(shingles(target_text), shingles(candidate_text))
    sequence_score = difflib.SequenceMatcher(None, normalize(target_text), normalize(candidate_text), autojunk=True).ratio()
    title_score = jaccard(shingles(target["title"], 2), shingles(title, 2))
    same_project = project == target["project"]
    solution_language = bool(SOLUTION_RE.search(candidate_text))
    if shingle_score >= 0.85 or sequence_score >= 0.92:
        status = "QUARANTINE_NEAR_DUPLICATE"
    elif same_project and (title_score >= 0.50 or sequence_score >= 0.65):
        status = "REVIEW_HIGH_RISK_POSSIBLE_SAME_BUG"
    elif solution_language and (shingle_score >= 0.35 or sequence_score >= 0.60):
        status = "REVIEW_HIGH_RISK_POTENTIAL_SOLUTION"
    else:
        status = "LOW_AUTOMATED_RISK_REVIEW_STILL_REQUIRED"
    return {"doc_id": doc_id, "project": project, "issue_number": number,
            "title": title, "created_at": created_at,
            "created_after_target": datetime.fromisoformat(created_at.replace("Z", "+00:00")) > datetime.fromisoformat(target["created_at"].replace("Z", "+00:00")) if created_at else None,
            "token_count": token_count, "content_sha256": content_hash,
            "evidence_snippet": snippet(title, body), "same_project": same_project,
            "five_shingle_jaccard": round(shingle_score, 6),
            "normalized_sequence_ratio": round(sequence_score, 6),
            "title_bigram_jaccard": round(title_score, 6),
            "solution_language_flag": solution_language, "automated_risk_status": status,
            "human_relevance_decision": None, "human_leakage_decision": None}


def main() -> None:
    targets = load_jsonl(ROOT / "data/phase2_9_abc_target_issue_inputs.jsonl")
    db = sqlite3.connect(DB_PATH)
    n_docs, total_len = db.execute("SELECT count(*), sum(token_count) FROM documents").fetchone()
    avg_len = total_len / n_docs
    query_terms = [set(tokens(f"{t['title']}\n{t['body']}")) for t in targets]
    term_to_queries: dict[str, list[int]] = defaultdict(list)
    for qid, terms in enumerate(query_terms):
        for term in terms:
            term_to_queries[term].append(qid)
    union_terms = set(term_to_queries)
    df = Counter()
    cursor = db.execute("SELECT normalized_text FROM documents ORDER BY doc_id")
    while batch := cursor.fetchmany(2000):
        for (text,) in batch:
            df.update(set(tokens(text)) & union_terms)
    idf = {term: math.log(1 + (n_docs - freq + 0.5) / (freq + 0.5)) for term, freq in df.items()}
    heaps: list[list[tuple[float, int, str]]] = [[] for _ in targets]
    cursor = db.execute("SELECT doc_id, normalized_text, token_count FROM documents ORDER BY doc_id")
    order = 0
    while batch := cursor.fetchmany(1000):
        for doc_id, text, doc_len in batch:
            order += 1
            counts = Counter(tokens(text))
            scores: dict[int, float] = defaultdict(float)
            # Stable term order is required because floating-point addition is
            # not associative and Python set iteration varies across processes.
            for term in sorted(counts.keys() & union_terms):
                tf = counts[term]
                contribution = idf[term] * tf * (K1 + 1) / (tf + K1 * (1 - B + B * doc_len / avg_len))
                for qid in term_to_queries[term]:
                    scores[qid] += contribution
            for qid, score in scores.items():
                item = (score, -order, doc_id)
                if len(heaps[qid]) < HEAP_SIZE:
                    heapq.heappush(heaps[qid], item)
                elif item > heaps[qid][0]:
                    heapq.heapreplace(heaps[qid], item)

    top10_records, top3_records, summaries = [], [], []
    for qid, target in enumerate(targets):
        ranked = sorted(heaps[qid], key=lambda x: (-x[0], -x[1], x[2]))
        reviewed = []
        for score, _, doc_id in ranked:
            row = db.execute("SELECT doc_id, project, issue_number, title, body, created_at, token_count, content_sha256 FROM documents WHERE doc_id=?", [doc_id]).fetchone()
            candidate = risk(target, row)
            candidate["bm25_score"] = round(score, 12)
            candidate["rank_before_human_review"] = len(reviewed) + 1
            reviewed.append(candidate)
            if len(reviewed) >= 10:
                break
        proposed = [x for x in reviewed if x["automated_risk_status"] == "LOW_AUTOMATED_RISK_REVIEW_STILL_REQUIRED"][:3]
        top10_records.append({"bug_id": target["bug_id"], "query_source": "target Issue title+body current dataset snapshot",
                              "retrieval_scope": "retrospective_no_temporal_cutoff", "k_review": 10,
                              "bm25": {"k1": K1, "b": B, "query_terms_unique": True,
                                       "tie_break": "score descending then doc_id ascending"},
                              "candidates": reviewed, "human_review_complete": False})
        top3_records.append({"bug_id": target["bug_id"], "requested_k": 3, "proposed_count": len(proposed),
                             "status": "PROPOSED_MACHINE_NOT_HUMAN_APPROVED" if len(proposed) == 3 else "RETRIEVAL_INCOMPLETE",
                             "document_ids": [x["doc_id"] for x in proposed],
                             "documents": [{k: x[k] for k in ("doc_id", "created_at", "created_after_target", "bm25_score", "token_count", "content_sha256", "automated_risk_status")} for x in proposed],
                             "human_approval": None})
        high_risk = sum(x["automated_risk_status"].startswith(("QUARANTINE", "REVIEW_HIGH_RISK")) for x in reviewed)
        summaries.append({"bug_id": target["bug_id"], "top10_count": len(reviewed),
                          "low_automated_risk_count_in_top10": len(reviewed) - high_risk,
                          "high_risk_or_quarantined_count": high_risk,
                          "proposed_top3_count": len(proposed), "retrieval_incomplete": len(proposed) < 3,
                          "manual_leakage_review_required": True, "human_approved_safe_count": 0})
    TOP10_PATH.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in top10_records), encoding="utf-8")
    TOP3_PATH.write_text("".join(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n" for x in top3_records), encoding="utf-8")
    SUMMARY_PATH.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in summaries), encoding="utf-8")
    result = {"corpus_documents": n_docs, "targets": len(targets),
              "targets_with_top10": sum(x["top10_count"] >= 10 for x in summaries),
              "targets_with_proposed_top3": sum(x["proposed_top3_count"] == 3 for x in summaries),
              "targets_requiring_manual_review": sum(x["manual_leakage_review_required"] for x in summaries),
              "retrieval_incomplete_targets": sum(x["retrieval_incomplete"] for x in summaries),
              "human_approved_candidates": 0, "official_retrieval": False}
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
