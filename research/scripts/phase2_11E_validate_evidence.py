#!/usr/bin/env python3
"""Independent structural validation of the Phase 2.11E evidence export."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

try:
    import duckdb
except ImportError as exc:  # pragma: no cover
    raise SystemExit("duckdb is required; set PYTHONPATH to the approved temporary dependency directory") from exc


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT.parent / "OSSGameBench"
EXPORT = ROOT / "data/phase2_11E_evidence"
ARCHIVE = ROOT / "phase2_11E_human_adjudication_evidence_package.zip"


def jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (EXPORT / name).read_text(encoding="utf-8").splitlines() if line.strip()]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    errors: list[str] = []
    snapshots = jsonl("phase2_11E_unique_issue_snapshots.jsonl")
    pairs = jsonl("phase2_11E_target_candidate_pairs.jsonl")
    targets = jsonl("phase2_11E_target_issues.jsonl")
    evidence = jsonl("phase2_11E_linked_evidence.jsonl")
    validation = json.loads((EXPORT / "phase2_11E_evidence_export_validation.json").read_text(encoding="utf-8"))
    with (EXPORT / "phase2_11E_human_adjudication_queue.csv").open(encoding="utf-8", newline="") as handle:
        queue = list(csv.DictReader(handle))
    snapshot_map = {x["doc_id"]: x for x in snapshots}
    if len(snapshots) != 123 or len(snapshot_map) != 123:
        errors.append("unique snapshot count/identity mismatch")
    if len(targets) != 18 or len({x["doc_id"] for x in targets}) != 18:
        errors.append("target count mismatch")
    if len(pairs) != 108 or sum(x["group"] == "B" for x in pairs) != 54 or sum(x["group"] == "C" for x in pairs) != 54:
        errors.append("pair/group count mismatch")
    if len(queue) != 108 or any(x["ai_recommendation"] or x["human_decision"] or x["human_reason"] for x in queue):
        errors.append("worksheet count or blank-decision invariant failed")
    if any(x["target_bug_id"] not in snapshot_map or x["candidate_doc_id"] not in snapshot_map for x in pairs):
        errors.append("pair does not resolve to snapshots")

    manifest = [json.loads(line) for line in (ROOT / "data/phase2_11D_ABC_manifest_NOT_FROZEN.jsonl").read_text().splitlines() if line]
    expected_pairs = []
    for record in manifest:
        for group_key, group in (("group_B", "B"), ("group_C", "C")):
            for slot, item in enumerate(record[group_key]["documents"], 1):
                expected_pairs.append((record["target_bug_id"], group, slot, item["doc_id"]))
    actual_pairs = [(x["target_bug_id"], x["group"], x["slot"], x["candidate_doc_id"]) for x in pairs]
    if actual_pairs != expected_pairs:
        errors.append("Phase 2.11D pair/order mismatch")

    con = duckdb.connect()
    con.execute("CREATE TEMP TABLE selected(project VARCHAR, number BIGINT, doc_id VARCHAR)")
    con.executemany("INSERT INTO selected VALUES (?, ?, ?)", [(*x["doc_id"].rsplit("#", 1)[:1], int(x["doc_id"].rsplit("#", 1)[1]), x["doc_id"]) for x in snapshots])
    source_issues = con.execute(
        "SELECT s.doc_id, i.title, i.body FROM read_parquet(?) i JOIN selected s ON i.project=s.project AND i.number=s.number",
        [str(DATASET / "issue.parquet")],
    ).fetchall()
    source_issue_map = {row[0]: (row[1] or "", row[2] or "") for row in source_issues}
    for doc_id, snapshot in snapshot_map.items():
        if doc_id not in source_issue_map or source_issue_map[doc_id] != (snapshot["title"], snapshot["body"]):
            errors.append(f"{doc_id}: Issue text is missing or differs from source")
        calculated = hashlib.sha256(f"{snapshot['title']}\n{snapshot['body']}".encode()).hexdigest()
        if calculated != snapshot["original_content_sha256"] or snapshot["hash_verification"]["status"] != "PASS":
            errors.append(f"{doc_id}: hash verification mismatch")
    source_comments = con.execute(
        "WITH source AS (SELECT row_number() OVER () AS source_order, * FROM read_parquet(?)) "
        "SELECT s.doc_id, c.contributor, c.body FROM source c JOIN selected s "
        "ON c.project=s.project AND c.issue_number=s.number ORDER BY s.doc_id, c.source_order",
        [str(DATASET / "issue_comment.parquet")],
    ).fetchall()
    comments_by_doc: dict[str, list[tuple[str, str]]] = {}
    for doc_id, author, body in source_comments:
        comments_by_doc.setdefault(doc_id, []).append((author, body or ""))
    for doc_id, snapshot in snapshot_map.items():
        exported = [(x["author"], x["body"]) for x in snapshot["available_comments"]]
        if exported != comments_by_doc.get(doc_id, []):
            errors.append(f"{doc_id}: comments missing, duplicated, reordered, or modified")
    con.close()

    evidence_ids = [x["evidence_id"] for x in evidence]
    if len(evidence_ids) != len(set(evidence_ids)) or any(not x.get("provenance") for x in evidence):
        errors.append("evidence ID uniqueness/provenance invariant failed")
    flame_text = (EXPORT / "phase2_11E_flame_priority_review.md").read_text(encoding="utf-8")
    flame_pairs = {
        ("flame-engine/flame#1003", "flame-engine/tiled.dart#58"),
        ("flame-engine/flame#1003", "flame-engine/tiled.dart#81"),
        ("flame-engine/flame#1003", "flame-engine/tiled.dart#56"),
        ("flame-engine/flame#1264", "flame-engine/tiled.dart#81"),
        ("flame-engine/flame#1264", "flame-engine/tiled.dart#58"),
    }
    if any(target not in flame_text or candidate not in flame_text for target, candidate in flame_pairs):
        errors.append("Flame priority coverage incomplete")

    required_names = sorted(path.name for path in EXPORT.iterdir() if path.is_file())
    with zipfile.ZipFile(ARCHIVE) as archive:
        if archive.testzip():
            errors.append("ZIP CRC failure")
        archive_names = sorted(Path(name).name for name in archive.namelist())
        if archive_names != required_names:
            errors.append("ZIP contents differ from export directory")
        for name in archive.namelist():
            if archive.read(name) != (EXPORT / Path(name).name).read_bytes():
                errors.append(f"ZIP bytes differ: {name}")
    if validation["status"] != "PASS_EVIDENCE_EXPORT" or not validation["phase2_11D_sources_byte_identical"]:
        errors.append("embedded validation/source-integrity status failed")
    result = {
        "status": "PASS_EVIDENCE_EXPORT" if not errors else "BLOCKED_EVIDENCE_EXPORT",
        "targets": len(targets), "B_references": sum(x["group"] == "B" for x in pairs),
        "C_references": sum(x["group"] == "C" for x in pairs), "unique_documents": len(snapshots),
        "comments_exactly_reproduced": len(source_comments), "evidence_records": len(evidence),
        "zip_entries": len(required_names), "zip_sha256": sha(ARCHIVE),
        "official_model_calls": 0, "errors": errors,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
