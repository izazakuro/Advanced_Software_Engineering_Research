#!/usr/bin/env python3
"""Build a generation-safe projection without exposing Gold/evaluation fields."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "outputs/phase2_11F_3/gold_references_temporal_corrected.jsonl"
TARGETS = ROOT / "data/phase2_11E_evidence/phase2_11E_target_issues.jsonl"
OUTPUT = ROOT / "outputs/phase2_11F_3/generation_input_safe_projection.jsonl"

# The prompt may receive only target/context Issue title and body. Gold is an
# evaluation artifact and is never serialized into model-visible content.
TARGET_PROMPT_ALLOWLIST = ("title", "body")
TARGET_CONTROL_ALLOWLIST = ("doc_id", "original_content_sha256", "created_at")
CONTEXT_PROMPT_ALLOWLIST = ("title", "body")
CONTEXT_CONTROL_ALLOWLIST = ("doc_id", "content_sha256", "created_at")
GOLD_PROMPT_ALLOWLIST: tuple[str, ...] = ()
POLICY_VERSION = "2.11F-3-explicit-allowlist-v1"
SAFE_CONTEXT_KEYS = {"doc_id", "title", "body", "content_sha256"}


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def content_sha(title: str, body: str) -> str:
    return hashlib.sha256((title + "\n" + body).encode("utf-8")).hexdigest()


def build_safe_projection(gold_path: Path = GOLD, targets_path: Path = TARGETS) -> list[dict]:
    gold = jsonl(gold_path)
    targets = {item["doc_id"]: item for item in jsonl(targets_path)}
    rows: list[dict] = []
    for record in gold:
        bug_id = record["bug_id"]
        target = targets[bug_id]
        title = target["title"] or ""
        body = target["body"] or ""
        digest = content_sha(title, body)
        if digest != target["original_content_sha256"]:
            raise ValueError(f"{bug_id}: target Issue content hash mismatch")
        rows.append({
            "bug_id": bug_id,
            "target_issue": {
                "doc_id": target["doc_id"],
                "title": title,
                "body": body,
                "original_content_sha256": digest,
                "created_at": target["created_at"],
            },
            "gold_fields_in_model_visible_content": [],
            "model_visible_sources": ["target_issue.title", "target_issue.body"],
            "generation_input_policy_version": POLICY_VERSION,
        })
    return rows


def validate_projection(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    allowed_top = {"bug_id", "target_issue", "gold_fields_in_model_visible_content", "model_visible_sources", "generation_input_policy_version"}
    allowed_target = set(TARGET_PROMPT_ALLOWLIST + TARGET_CONTROL_ALLOWLIST)
    forbidden_tokens = {
        "evidence", "repair", "commit", "diff", "oracle", "trigger_conditions",
        "trigger_paths", "review", "audit", "machine_suggestion", "root_cause",
    }
    seen: set[str] = set()
    for row in rows:
        bug_id = row.get("bug_id", "<missing>")
        if set(row) != allowed_top:
            errors.append(f"{bug_id}: projection top-level keys violate allowlist")
        if set(row.get("target_issue", {})) != allowed_target:
            errors.append(f"{bug_id}: target Issue keys violate allowlist")
        if row.get("gold_fields_in_model_visible_content") != []:
            errors.append(f"{bug_id}: Gold field exposed to generation")
        if row.get("generation_input_policy_version") != POLICY_VERSION:
            errors.append(f"{bug_id}: wrong generation input policy version")
        target = row.get("target_issue", {})
        if target.get("doc_id") != bug_id:
            errors.append(f"{bug_id}: target doc_id mismatch")
        if bug_id in seen:
            errors.append(f"{bug_id}: duplicate safe projection record")
        seen.add(bug_id)
        if isinstance(target.get("title"), str) and isinstance(target.get("body"), str):
            if content_sha(target["title"], target["body"]) != target.get("original_content_sha256"):
                errors.append(f"{bug_id}: safe projection content hash mismatch")
        if any(any(token in key.casefold() for token in forbidden_tokens) for key in row):
            errors.append(f"{bug_id}: forbidden historical/evaluation key exposed")
    return errors


def load_safe_projection(path: Path = OUTPUT) -> dict[str, dict]:
    """Load the only approved target source; absence/invalidity is fatal."""
    if not path.is_file():
        raise FileNotFoundError(f"approved generation safe projection is missing: {path}")
    rows = jsonl(path)
    errors = validate_projection(rows)
    if errors:
        raise ValueError("invalid approved generation safe projection: " + "; ".join(errors))
    if len(rows) != 18:
        raise ValueError(f"approved generation safe projection must contain 18 records, found {len(rows)}")
    return {row["bug_id"]: row for row in rows}


def serialize_context_issue(raw: dict) -> dict:
    """Reduce one corpus row to the strict context allowlist; reject extras."""
    required = {"doc_id", "title", "body"}
    if set(raw) != required:
        raise ValueError(f"context source keys must be exactly {sorted(required)}, found {sorted(raw)}")
    if not all(isinstance(raw[key], str) for key in required):
        raise ValueError("context doc_id/title/body must all be strings")
    return {"doc_id": raw["doc_id"], "title": raw["title"], "body": raw["body"],
            "content_sha256": content_sha(raw["title"], raw["body"])}


def validate_safe_context(context: dict) -> None:
    if set(context) != SAFE_CONTEXT_KEYS:
        raise ValueError(f"safe context keys violate allowlist: {sorted(context)}")
    if not all(isinstance(context[key], str) for key in SAFE_CONTEXT_KEYS):
        raise ValueError("safe context values must all be strings")
    if content_sha(context["title"], context["body"]) != context["content_sha256"]:
        raise ValueError(f"{context['doc_id']}: safe context content hash mismatch")


def render_abc_prompt(template: str, target_projection: dict, safe_contexts: list[dict]) -> str:
    """The sole approved ABC prompt-rendering boundary."""
    projection_errors = validate_projection([target_projection])
    if projection_errors:
        raise ValueError("invalid target safe projection: " + "; ".join(projection_errors))
    for context in safe_contexts:
        validate_safe_context(context)
    target = target_projection["target_issue"]
    if safe_contexts:
        blocks = [f"Context {index} ({context['doc_id']})\nTitle: {context['title']}\nBody:\n{context['body']}"
                  for index, context in enumerate(safe_contexts, 1)]
        extra = "\n\n".join(blocks)
    else:
        extra = "None."
    rendered = (template.replace("{{TARGET_TITLE}}", target["title"])
                .replace("{{TARGET_BODY}}", target["body"])
                .replace("{{ADDITIONAL_CONTEXT_OR_NONE}}", extra))
    placeholders = ("{{TARGET_TITLE}}", "{{TARGET_BODY}}", "{{ADDITIONAL_CONTEXT_OR_NONE}}")
    if any(marker in rendered for marker in placeholders):
        raise ValueError("unresolved approved prompt placeholder")
    return rendered


def write_projection(path: Path = OUTPUT) -> None:
    rows = build_safe_projection()
    errors = validate_projection(rows)
    if errors:
        raise SystemExit("\n".join(errors))
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


if __name__ == "__main__":
    write_projection()
    print(json.dumps({"status": "PASS_SAFE_PROJECTION", "records": 18, "gold_prompt_allowlist": [], "official_model_calls": 0}, indent=2))
