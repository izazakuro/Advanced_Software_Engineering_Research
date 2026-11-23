#!/usr/bin/env python3
"""Reproducible Phase-1 audit and bug-candidate selection for OSSGameBench.

The script reads the source Parquet files in place.  It does not modify or copy
the dataset, and it never projects the large commit ``diff`` column.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError as exc:
    raise SystemExit(
        "DuckDB is required. Run from research/: "
        "uv run --with duckdb==2.0.0 python scripts/audit_dataset.py"
    ) from exc


TOP_TABLES = (
    "commit_metadata.parquet",
    "issue.parquet",
    "issue_comment.parquet",
    "issue_pr.parquet",
    "pr.parquet",
    "pr_comment.parquet",
    "pr_commit.parquet",
    "project.parquet",
)

# Labels discovered from the dataset vocabulary. These are deliberately more
# conservative than a title/body search and exclude labels such as "not a bug",
# "upstream bug", "driver bug", and "dependency bug".
BUG_LABELS = {
    "bug", "type: bug", "type:bug", "type-bug", "type_bug", "t: bug",
    "t:bug", "t - bug", "kind: bug", "kind:bug", "category:bug",
    "category: bug", "issue: bug", "status: bug", "defect", "type-defect",
    "type:defect", "crash", "game crash", "crash or hang", "regression",
    "type: regression", "category: regression", "t:regression",
    "error report", "minor bug", "major bug", "bug report", "bug-report",
    "confirmed-bug", "bug: confirmed", "bug confirmed", "bug: unconfirmed",
    "unconfirmed bug", "engine bug", "map bug", "trx bug", "og bug",
    "native bug", "bug fix", "bug / bugfix", "🐞 bug", "🕷 bug", "🐛 bug",
    "🐛bug", "bug 🐛", "💥crash", "category: crash", "type: crash",
    "crash (player triggered)", "crash (spontaneous)",
}

SELECTED: dict[str, dict[str, Any]] = {
    "CorsixTH/CorsixTH#1799": {
        "category": "User interface",
        "triggers": [
            "Load the attached save, accept the Golf Stones emergency, and wait for the patients to enter the hospital.",
            "Select an emergency patient whose disease has not been discovered and open the Case Book from the patient window.",
        ],
        "expected": "The Case Book should open for the patient's disease, or the undiscovered-disease emergency should not occur.",
        "rationale": "Seven source-provided steps, save-game evidence, observable softlock, and an explicitly closing merged PR.",
        "uncertainties": ["The issue gives two alternative acceptable behaviours."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "freeorion/freeorion#1897": {
        "category": "Crash or exception",
        "triggers": ["Run a second or later game in hostless mode until effects processing occurs."],
        "expected": "A later hostless game should continue without a segmentation fault.",
        "rationale": "Specific session-state trigger, stack trace, merged PR explicitly fixing the issue, and a linked repair commit.",
        "uncertainties": ["The exact turn and minimal deterministic reproduction sequence are not documented."],
        "score_override": {"description": 2, "trigger": 1, "repair": 2, "testability": 1},
    },
    "gameservermanagers/linuxgsm#4059": {
        "category": "Compatibility",
        "triggers": ["On the documented Ubuntu host/container setup, run the LinuxGSM Counter-Strike 1.6 server installation command."],
        "expected": "The HLDS/Counter-Strike server files should download and installation should complete.",
        "rationale": "Concrete environment, commands, repeated failure log, explicit Fixes reference, and linked commits.",
        "uncertainties": ["The failure depends partly on SteamCMD/depot behaviour external to LinuxGSM."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "vcmi/vcmi#5462": {
        "category": "Crash or exception",
        "triggers": ["On macOS 13.4.1, create a new multiplayer lobby room."],
        "expected": "A newly created multiplayer room should open without the client crashing.",
        "rationale": "Single clear action, platform/crash report, PR explicitly fixing the issue, and a linked commit.",
        "uncertainties": ["The expected result is implicit rather than in a dedicated issue field."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "warzone2100/warzone2100#4125": {
        "category": "Gameplay logic",
        "triggers": ["Run a Linux headless multiplayer game with --autohost and have multiple players leave or time out after play begins."],
        "expected": "Disconnected players should not be repeatedly auto-kicked in a loop.",
        "rationale": "Mode, version, event log, observable loop, explicit Fixes reference, and one linked repair commit.",
        "uncertainties": ["The reporter says occurrence was intermittent and the original steps are not deterministic."],
        "score_override": {"description": 2, "trigger": 1, "repair": 2, "testability": 1},
    },
    "wolvenkit/wolvenkit#2279": {
        "category": "Crash or exception",
        "triggers": ["Enable mesh export with materials in appsettings.json and export the specified .mesh through WolvenKit.CLI."],
        "expected": "Mesh export should complete without recursive MaterialExtractor calls causing a stack overflow.",
        "rationale": "Configuration, asset, command, stack trace, explicit Fixes reference, and linked commits.",
        "uncertainties": ["The issue's 'Expected behavior' section mistakenly contains the observed stack trace; successful export is an interpretation."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 1},
    },
    "colobot/colobot#1787": {
        "category": "Save/load or persistence",
        "triggers": ["Load the reported custom-level save containing the mod path value shown in the log."],
        "expected": "The custom-level save should load without CLevelParserExceptionBadParam.",
        "rationale": "Named loading state, precise exception/log, explicitly fixing PR, and one linked commit.",
        "uncertainties": ["The issue body lacks a numbered reproduction procedure and states the expected result only implicitly."],
        "score_override": {"description": 2, "trigger": 1, "repair": 2, "testability": 1},
    },
    "lichess-org/lila#12907": {
        "category": "Gameplay logic",
        "triggers": ["Call /api/stream/game/{id} for an in-progress game that already has played moves."],
        "expected": "Each NDJSON packet should contain a FEN with the correct fullmove number.",
        "rationale": "Executable curl reproduction, exact incorrect field/value, explicit expected output, merged Fixes PR, and linked commits.",
        "uncertainties": ["The example game ID is time-dependent; a controlled in-progress fixture is needed."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "gemrb/gemrb#1878": {
        "category": "Crash or exception",
        "triggers": ["In Baldur's Gate II, travel to Waukeen's Promenade."],
        "expected": "Entering Waukeen's Promenade should not dereference an uninitialized killer or segfault.",
        "rationale": "Concrete location/action, debugger trace, PR naming the fix and issue, and linked commits.",
        "uncertainties": ["The reporter could not reproduce the crash every time."],
        "score_override": {"description": 2, "trigger": 1, "repair": 2, "testability": 1},
    },
    "tmodloader/tmodloader#2644": {
        "category": "Compatibility",
        "triggers": ["Build the supplied Ubuntu 20.04 Docker image as a non-root user and start the tModLoader server."],
        "expected": "The server should start without a permission-denied Win32Exception/null-reference failure.",
        "rationale": "Complete container recipe, platform/version, logs, a PR titled Fix #2644, and linked commits.",
        "uncertainties": ["The reproduction depends on external package and download availability."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "longturn/freeciv21#1189": {
        "category": "Crash or exception",
        "triggers": ["Build/run the master version with AddressSanitizer and execute autogame."],
        "expected": "Autogame should finish without an AddressSanitizer segmentation fault during nation destruction.",
        "rationale": "Compact executable condition, full ASan trace, explicit Fixes PR, and one linked commit.",
        "uncertainties": ["Ruleset and autogame parameters are not documented."],
        "score_override": {"description": 2, "trigger": 1, "repair": 2, "testability": 2},
    },
    "rptools/maptool#2393": {
        "category": "User interface",
        "triggers": ["On macOS, install MapTool 1.8.3 from DMG, launch it, accept the update prompt, install the downloaded package, and relaunch."],
        "expected": "The current release should not repeatedly offer the same 1.8.3 update.",
        "rationale": "Nine explicit steps, version/environment, observable prompt loop, exact Fixes reference, and linked commits.",
        "uncertainties": ["The historical GitHub release API response may be difficult to reproduce unchanged."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "flame-engine/flame#1264": {
        "category": "Rendering or graphics",
        "triggers": ["Clone the linked reproduction, run flame_tiled, and compare its layered TMX rendering with the same map opened in Tiled."],
        "expected": "Tiles from higher layers should remain visible and match the official Tiled editor rendering.",
        "rationale": "Runnable reproduction repository, layer combinations, screenshots, expected rendering, and linked repair commits.",
        "uncertainties": ["The external reproduction repository is not embedded in OSSGameBench."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "freeorion/freeorion#2365": {
        "category": "Save/load or persistence",
        "triggers": ["Run the specified FreeOrion revision under AddressSanitizer and load an XML-format savegame."],
        "expected": "The loader should detect XML format and load or reject it without an allocator abort.",
        "rationale": "Format/build/environment condition, ASan trace, explicit Fixes PR, and a linked commit.",
        "uncertainties": ["The exact save fixture is not embedded in the issue body."],
        "score_override": {"description": 2, "trigger": 1, "repair": 2, "testability": 1},
    },
    "CorsixTH/CorsixTH#2881": {
        "category": "Crash or exception",
        "triggers": ["Using the attached stable-version save in revision 712f2a9, start a new level and hire a receptionist."],
        "expected": "Staff recruitment/placement should proceed without a nil profile exception.",
        "rationale": "Save, revision, action, stack trace, explicit Fixes PR, and one linked commit.",
        "uncertainties": ["The issue's Expected Behaviour field is malformed and does not state the desired result."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 1},
    },
    "gameservermanagers/linuxgsm#4251": {
        "category": "Compatibility",
        "triggers": ["On AlmaLinux 8, install EPEL and dpkg, then run the Rust server installation workflow."],
        "expected": "Dependency detection should use the RHEL package manager and not suggest apt or report installed dependencies missing.",
        "rationale": "Five explicit steps, positive/negative comparison, exact Fixes reference, and one linked commit.",
        "uncertainties": ["Package-repository state may change over time."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "rptools/maptool#2416": {
        "category": "Input handling",
        "triggers": ["Open a hex-grid map saved by an earlier MapTool version and use Set Facing or Shift+mouse-wheel before opening Map Properties."],
        "expected": "Token facing should change without a repeating Java exception.",
        "rationale": "Precise map state, two input paths and control comparison, expected behaviour, exact Fixes reference, and linked commits.",
        "uncertainties": ["A specific old map fixture is not embedded in the dataset."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
    "flame-engine/flame#1003": {
        "category": "User interface",
        "triggers": ["Place GameWidget under the supplied StatefulWidget and call setState so the widget tree rebuilds."],
        "expected": "The game instance should remain available after rebuild and gameRef should stay valid.",
        "rationale": "Runnable code-level reproduction, exact exception and expected lifecycle, explicit Fixes PR, and linked commits.",
        "uncertainties": ["The linked base reproduction repository is external to the dataset."],
        "score_override": {"description": 2, "trigger": 2, "repair": 2, "testability": 2},
    },
}


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    default_root = here.parents[2] / "OSSGameBench"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=default_root)
    parser.add_argument("--output-root", type=Path, default=here.parents[1])
    parser.add_argument("--shortlist-size", type=int, default=60)
    return parser.parse_args()


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def normalize_labels(labels: Any) -> list[str]:
    return [str(x).strip().lower() for x in (labels or []) if x is not None]


def is_bug_issue(labels: Any) -> bool:
    return bool(set(normalize_labels(labels)) & BUG_LABELS)


def parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def has_repair_words(text: str) -> bool:
    return bool(re.search(r"\b(fix(?:e[sd])?|clos(?:e[sd]?|ing)|resolv(?:e[sd]?|ing)|bugfix)\b", text, re.I))


def strong_issue_reference(project: str, issue_number: int, title: str, body: str) -> bool:
    text = f"{title or ''}\n{body or ''}"
    number = re.escape(str(issue_number))
    project_ref = re.escape(project) + r"#" + number
    hash_ref = r"#" + number + r"(?!\d)"
    patterns = (
        rf"\b(?:fix(?:e[sd])?|clos(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))\b.{{0,100}}(?:{project_ref}|{hash_ref})",
        rf"(?:{project_ref}|{hash_ref}).{{0,100}}\b(?:fix(?:e[sd])?|clos(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))\b",
    )
    return any(re.search(p, text, re.I | re.S) for p in patterns)


def heuristic_scores(issue_body: str, pr_title: str, pr_body: str, strong_ref: bool, commit_count: int) -> dict[str, int]:
    body = issue_body or ""
    low = body.lower()
    observed = bool(re.search(r"actual|instead|what happened|error|exception|crash|freeze|does not|doesn't|fails?", low))
    expected = bool(re.search(r"expected|should|without (?:an? )?(?:error|crash)|should not", low))
    steps = bool(re.search(r"steps? to reproduce|to reproduce|reproduction|\n\s*1[.)]", low))
    condition = bool(re.search(r"\b(when|after|before|while|using|with|on|load|open|click|run|start|enter|create)\b", low))
    description = 2 if len(body.strip()) >= 200 and observed else (1 if len(body.strip()) >= 80 else 0)
    trigger = 2 if steps and condition else (1 if condition and len(body.strip()) >= 120 else 0)
    repair = 2 if strong_ref and commit_count > 0 else (1 if has_repair_words(f"{pr_title} {pr_body}") else 0)
    testability = 2 if expected and observed and condition else (1 if observed and condition else 0)
    return {"description": description, "trigger": trigger, "repair": repair, "testability": testability}


def inspect_schema(con: duckdb.DuckDBPyConnection, dataset: Path) -> list[dict[str, Any]]:
    results = []
    for name in TOP_TABLES:
        path = dataset / name
        desc = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{sql_path(path)}')").fetchall()
        columns = [(row[0], row[1]) for row in desc]
        exprs = ["count(*) AS row_count"]
        for col, dtype in columns:
            quoted = '"' + col.replace('"', '""') + '"'
            exprs.append(f"count(*) FILTER (WHERE {quoted} IS NULL) AS null_{len(exprs)}")
            if dtype == "VARCHAR":
                exprs.append(f"count(*) FILTER (WHERE {quoted} IS NOT NULL AND length(trim({quoted}))=0) AS blank_{len(exprs)}")
            elif dtype.endswith("[]"):
                exprs.append(f"count(*) FILTER (WHERE {quoted} IS NOT NULL AND len({quoted})=0) AS empty_{len(exprs)}")
        values = con.execute(f"SELECT {', '.join(exprs)} FROM read_parquet('{sql_path(path)}')").fetchone()
        row_count = values[0]
        rates: dict[str, dict[str, float | int]] = {}
        idx = 1
        for col, dtype in columns:
            nulls = values[idx]; idx += 1
            blanks = 0
            if dtype == "VARCHAR" or dtype.endswith("[]"):
                blanks = values[idx]; idx += 1
            rates[col] = {
                "null_count": nulls,
                "blank_or_empty_count": blanks,
                "missing_rate_pct": round(100.0 * (nulls + blanks) / row_count, 3) if row_count else 0.0,
            }
        results.append({"file": name, "row_count": row_count, "columns": columns, "missing": rates})
    return results


def relation_audit(con: duckdb.DuckDBPyConnection, dataset: Path) -> list[dict[str, Any]]:
    p = lambda name: sql_path(dataset / name)
    checks = [
        ("Projects → Issues", f"SELECT count(*) total, count(*) FILTER (WHERE p.project IS NOT NULL) matched FROM read_parquet('{p('issue.parquet')}') i LEFT JOIN read_parquet('{p('project.parquet')}') p USING(project)"),
        ("Issues → Issue Comments", f"SELECT count(*) total, count(*) FILTER (WHERE i.number IS NOT NULL) matched FROM read_parquet('{p('issue_comment.parquet')}') c LEFT JOIN read_parquet('{p('issue.parquet')}') i ON c.project=i.project AND c.issue_number=i.number"),
        ("Issue links → Issues", f"SELECT count(*) total, count(*) FILTER (WHERE i.number IS NOT NULL) matched FROM read_parquet('{p('issue_pr.parquet')}') l LEFT JOIN read_parquet('{p('issue.parquet')}') i ON l.project=i.project AND l.issue_number=i.number"),
        ("Issue links → Pull Requests", f"SELECT count(*) total, count(*) FILTER (WHERE r.number IS NOT NULL) matched FROM read_parquet('{p('issue_pr.parquet')}') l LEFT JOIN read_parquet('{p('pr.parquet')}') r ON l.project=r.project AND l.pr_number=r.number"),
        ("Pull Requests → PR Comments", f"SELECT count(*) total, count(*) FILTER (WHERE r.number IS NOT NULL) matched FROM read_parquet('{p('pr_comment.parquet')}') c LEFT JOIN read_parquet('{p('pr.parquet')}') r ON c.project=r.project AND c.pr_number=r.number"),
        ("Pull Requests → PR Commits", f"SELECT count(*) total, count(*) FILTER (WHERE r.number IS NOT NULL) matched FROM read_parquet('{p('pr_commit.parquet')}') c LEFT JOIN read_parquet('{p('pr.parquet')}') r ON c.project=r.project AND c.pr_number=r.number"),
    ]
    out = []
    for label, query in checks:
        total, matched = con.execute(query).fetchone()
        out.append({"relationship": label, "total": total, "matched": matched, "match_pct": round(100 * matched / total, 3) if total else 0})
    return out


def candidate_rows(con: duckdb.DuckDBPyConnection, dataset: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    p = lambda name: sql_path(dataset / name)
    issue_rows = con.execute(
        f"SELECT project,number,contributor,title,body,labels,state,created_at,closed_at FROM read_parquet('{p('issue.parquet')}')"
    ).fetchall()
    issue_cols = [d[0] for d in con.description]
    issues = [dict(zip(issue_cols, row)) for row in issue_rows]
    bug_issues = {(x["project"], x["number"]): x for x in issues if is_bug_issue(x["labels"])}
    del issue_rows, issues

    links = con.execute(
        f"""SELECT DISTINCT l.project,l.issue_number,l.pr_number,p.title,p.body,p.state,p.created_at,p.closed_at,p.merged_at
            FROM read_parquet('{p('issue_pr.parquet')}') l
            JOIN read_parquet('{p('pr.parquet')}') p
              ON l.project=p.project AND l.pr_number=p.number"""
    ).fetchall()
    commit_rows = con.execute(
        f"SELECT project,pr_number,commit_sha FROM read_parquet('{p('pr_commit.parquet')}')"
    ).fetchall()
    commits: dict[tuple[str, int], list[str]] = defaultdict(list)
    for project, pr_number, sha in commit_rows:
        if sha:
            commits[(project, pr_number)].append(sha)
    del commit_rows

    by_issue: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for project, issue_number, pr_number, title, body, state, created, closed, merged in links:
        key = (project, issue_number)
        if key not in bug_issues:
            continue
        strong = strong_issue_reference(project, issue_number, title or "", body or "")
        by_issue[key].append({
            "pr_number": pr_number, "pr_title": title or "", "pr_body": body or "",
            "pr_state": state, "pr_created_at": created, "pr_closed_at": closed,
            "pr_merged_at": merged, "strong_reference": strong,
            "commit_shas": sorted(set(commits.get((project, pr_number), []))),
        })

    repair_history: dict[str, list[datetime]] = defaultdict(list)
    for key, prs in by_issue.items():
        issue = bug_issues[key]
        issue_created = parse_ts(issue["created_at"])
        for pr in prs:
            merged = parse_ts(pr["pr_merged_at"])
            if issue_created and merged and pr["strong_reference"] and pr["commit_shas"]:
                # A repaired historical record is conservatively considered
                # available only after the fixing PR was merged, not merely
                # when its issue was opened.
                repair_history[key[0]].append(merged)
                break

    candidates = []
    linked_count = 0
    repair_count = 0
    for key, issue in bug_issues.items():
        prs = by_issue.get(key, [])
        if prs:
            linked_count += 1
        eligible_prs = [p for p in prs if p["pr_merged_at"]]
        if not eligible_prs:
            continue
        eligible_prs.sort(key=lambda x: (
            not (x["strong_reference"] and x["commit_shas"]),
            not x["strong_reference"],
            str(x["pr_merged_at"]), x["pr_number"],
        ))
        pr = eligible_prs[0]
        strong_evidence = pr["strong_reference"] and bool(pr["commit_shas"])
        if strong_evidence:
            repair_count += 1
        scores = heuristic_scores(issue["body"] or "", pr["pr_title"], pr["pr_body"], pr["strong_reference"], len(pr["commit_shas"]))
        bug_id = f"{key[0]}#{key[1]}"
        if bug_id in SELECTED:
            scores.update(SELECTED[bug_id]["score_override"])
        target_time = parse_ts(issue["created_at"])
        prior = sum(1 for t in repair_history.get(key[0], []) if target_time and t < target_time)
        scores["retrieval"] = 2 if prior >= 10 else (1 if prior >= 1 else 0)
        total = sum(scores.values())
        exclusion = ""
        if not strong_evidence:
            exclusion = "No merged linked PR with an exact closing reference and linked commit."
        elif scores["trigger"] == 0:
            exclusion = "No identifiable trigger condition in the issue body."
        elif scores["testability"] == 0:
            exclusion = "No sufficiently observable pass/fail outcome."
        candidates.append({
            "project_identifier": key[0], "project_name": key[0].split("/", 1)[-1],
            "issue_identifier": bug_id, "issue_number": key[1], "issue_title": issue["title"] or "",
            "issue_body": issue["body"] or "", "issue_creation_timestamp": issue["created_at"] or "",
            "issue_labels": ";".join(issue["labels"] or []), "issue_state": issue["state"] or "",
            "issue_closed_at": issue["closed_at"] or "", "linked_pr_identifier": f"{key[0]}#{pr['pr_number']}",
            "linked_pr_number": pr["pr_number"], "linked_pr_title": pr["pr_title"],
            "linked_pr_body": pr["pr_body"], "pr_creation_timestamp": pr["pr_created_at"] or "",
            "pr_merge_timestamp": pr["pr_merged_at"] or "", "commit_shas": pr["commit_shas"],
            "evidence_availability": (
                f"merged_linked_pr; exact_closing_reference={str(pr['strong_reference']).lower()}; "
                f"linked_commits={len(pr['commit_shas'])}"
            ),
            "description_quality_score": scores["description"], "trigger_clarity_score": scores["trigger"],
            "repair_evidence_score": scores["repair"], "testability_score": scores["testability"],
            "retrieval_feasibility_score": scores["retrieval"], "total_quality_score": total,
            "eligible_historical_records": prior, "strong_repair_evidence": strong_evidence,
            "exclusion_reason": exclusion,
        })

    stats = {
        "total_issues": len(bug_issues) + (con.execute(f"SELECT count(*) FROM read_parquet('{p('issue.parquet')}')").fetchone()[0] - len(bug_issues)),
        "bug_issues": len(bug_issues),
        "closed_bug_issues": sum(1 for x in bug_issues.values() if str(x["state"]).upper() == "CLOSED"),
        "bug_with_body": sum(1 for x in bug_issues.values() if (x["body"] or "").strip()),
        "bug_with_linked_pr": linked_count,
        "bug_with_repair_evidence": repair_count,
        "bug_by_project": Counter(key[0] for key in bug_issues),
    }
    return candidates, stats


def inspect_selected_commits(con: duckdb.DuckDBPyConnection, dataset: Path, selected_rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    wanted: dict[str, set[str]] = defaultdict(set)
    for row in selected_rows:
        wanted[row["project_identifier"]].update(row["commit_shas"])
    found: dict[str, dict[str, Any]] = {}
    for project, shas in wanted.items():
        candidates = sorted((dataset / "commits").glob(f"commits|{project.replace('/', '|')}*.parquet"))
        if not candidates:
            continue
        values = ",".join("'" + sha.replace("'", "''") + "'" for sha in sorted(shas))
        for path in candidates:
            # Deliberately excludes diff; only selected projects and selected SHA values are inspected.
            query = f"""SELECT split_part(sha,' ',1) clean_sha,created_at,message,files
                        FROM read_parquet('{sql_path(path)}')
                        WHERE split_part(sha,' ',1) IN ({values})"""
            for sha, created, message, files in con.execute(query).fetchall():
                found[sha] = {
                    "created_at": created, "message": message or "", "modified_files": files or [],
                    "source_file": path.name,
                }
    return found


def primary_key_audit(con: duckdb.DuckDBPyConnection, dataset: Path) -> dict[str, tuple[int, int]]:
    specs = {
        "project.parquet": "project",
        "commit_metadata.parquet": "project",
        "issue.parquet": "project,number",
        "pr.parquet": "project,number",
        "issue_pr.parquet": "project,issue_number,pr_number",
        "pr_commit.parquet": "project,pr_number,commit_sha",
    }
    out = {}
    for name, keys in specs.items():
        path = sql_path(dataset / name)
        total, distinct_count = con.execute(
            f"SELECT count(*), count(*) FROM (SELECT {keys} FROM read_parquet('{path}') GROUP BY {keys})"
        ).fetchone()
        raw_total = con.execute(f"SELECT count(*) FROM read_parquet('{path}')").fetchone()[0]
        out[name] = (raw_total, distinct_count)
    return out


def write_candidates(path: Path, rows: list[dict[str, Any]], shortlist_size: int) -> list[dict[str, Any]]:
    eligible = [r for r in rows if not r["exclusion_reason"]]
    eligible.sort(key=lambda r: (-r["total_quality_score"], r["project_identifier"].lower(), r["issue_number"]))
    per_project: Counter[str] = Counter()
    automatic = []
    for row in eligible:
        project = row["project_identifier"]
        if per_project[project] >= 5:
            continue
        automatic.append(row)
        per_project[project] += 1
        if len(automatic) >= shortlist_size:
            break
    shortlist_ids = {r["issue_identifier"] for r in automatic} | set(SELECTED)
    output = [r for r in rows if r["issue_identifier"] in shortlist_ids]
    output.sort(key=lambda r: (-r["total_quality_score"], r["project_identifier"].lower(), r["issue_number"]))
    for rank, row in enumerate(output, 1):
        row["candidate_rank"] = rank
        row["candidate_selection_status"] = "selected" if row["issue_identifier"] in SELECTED else "shortlist"
    fields = [
        "candidate_rank", "project_identifier", "project_name", "issue_identifier", "issue_number",
        "issue_title", "issue_creation_timestamp", "issue_labels", "issue_state", "linked_pr_identifier",
        "pr_merge_timestamp", "evidence_availability", "description_quality_score", "trigger_clarity_score",
        "repair_evidence_score", "testability_score", "retrieval_feasibility_score", "total_quality_score",
        "eligible_historical_records", "candidate_selection_status", "exclusion_reason",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(output)
    return output


def write_selected(path: Path, rows: list[dict[str, Any]], commit_info: dict[str, dict[str, Any]]) -> None:
    by_id = {r["issue_identifier"]: r for r in rows}
    with path.open("w", encoding="utf-8") as handle:
        for bug_id in sorted(SELECTED):
            row = by_id[bug_id]
            note = SELECTED[bug_id]
            commits = []
            for sha in row["commit_shas"]:
                info = commit_info.get(sha, {})
                commits.append({
                    "sha": sha, "created_at": info.get("created_at"), "message": info.get("message"),
                    "modified_files": info.get("modified_files", []),
                })
            unresolved_commits = sum(1 for commit in commits if not commit["created_at"])
            leakage_warnings = [
                "Exclude this target's PR, commits, PR text, and all post-creation discussion from retrieval.",
                "Issue bodies have no edit history; the stored body may contain edits made after creation.",
                "Issue and PR comment tables have no timestamps, so comments cannot be safely admitted to a temporal corpus.",
            ]
            if unresolved_commits:
                leakage_warnings.append(
                    f"{unresolved_commits} pr_commit SHA(s) have no matching row in the project commit Parquet; their commit timestamps are unavailable."
                )
            record = {
                "stable_bug_id": bug_id, "project_identifier": row["project_identifier"],
                "original_issue_identifier": bug_id, "original_issue_number": row["issue_number"],
                "original_issue_title": row["issue_title"], "original_issue_body": row["issue_body"],
                "issue_creation_timestamp": row["issue_creation_timestamp"],
                "linked_repair_evidence": {
                    "pr_identifier": row["linked_pr_identifier"], "pr_title": row["linked_pr_title"],
                    "pr_creation_timestamp": row["pr_creation_timestamp"],
                    "pr_merge_timestamp": row["pr_merge_timestamp"], "commits": commits,
                    "interpretation": "Reserved evaluation evidence; the merged PR explicitly references this issue and has linked commits.",
                },
                "preliminary_bug_category": note["category"],
                "trigger_conditions": {"source_supported": note["triggers"], "preliminary_interpretation": []},
                "expected_behavior": {"source_supported_or_directly_implied": note["expected"]},
                "selection_rationale": note["rationale"], "known_uncertainties": note["uncertainties"],
                "quality_scores": {
                    "description": row["description_quality_score"], "trigger": row["trigger_clarity_score"],
                    "repair": row["repair_evidence_score"], "testability": row["testability_score"],
                    "retrieval": row["retrieval_feasibility_score"], "total": row["total_quality_score"],
                },
                "eligible_historical_records_before_cutoff": row["eligible_historical_records"],
                "historical_retrieval_cutoff": row["issue_creation_timestamp"],
                "leakage_warnings": leakage_warnings,
                "annotation_status": "Preliminary Phase-1 annotation; not a finalized Gold Reference.",
            }
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def markdown_table(headers: list[str], rows: list[list[Any]]) -> str:
    def clean(x: Any) -> str:
        return str(x).replace("|", "\\|").replace("\n", " ")
    return "\n".join([
        "| " + " | ".join(map(clean, headers)) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *["| " + " | ".join(map(clean, row)) + " |" for row in rows],
    ])


def write_sampling_protocol(path: Path) -> None:
    path.write_text("""# OSSGameBench Phase-1 Sampling Protocol

## Scope and unit of analysis

The unit is a GitHub issue identified by the stable composite key `(project, issue number)`. The procedure uses only the eight aggregate Parquet tables for broad filtering. Project commit files are read only for the 13 projects represented in the final selection, with projection limited to SHA, timestamp, message, and modified-file names; the `diff` column is never scanned.

## Inclusion criteria

1. At least one normalized label is in the documented explicit bug-label vocabulary in `audit_dataset.py`.
2. The issue has a non-empty concrete malfunction description.
3. A same-project linked PR is merged, explicitly closes/fixes/resolves the exact issue number, and has at least one `pr_commit` record.
4. At least one trigger/precondition is identifiable without using the repair.
5. The failure and expected outcome are observable enough to express a pass/fail scenario.

Text containing the word *bug* is not sufficient. Labels that explicitly negate or externalize the defect (for example `not a bug`, `upstream bug`, `driver bug`, and `dependency bug`) are excluded from the positive vocabulary.

## Exclusion criteria

- No exact source issue–PR relation, unmerged PR, or no linked commit.
- PR merely mentions the issue without an exact closing/fixing/resolving expression.
- Feature requests, support questions, upstream-only defects, duplicates without independent repair evidence.
- Missing or placeholder-only trigger, or no observable result.
- Evidence would require inventing an undocumented state/action.

## Scoring rules (selection heuristic, not a validated scale)

Each dimension is 0–2. Description: 0 absent/placeholder, 1 concrete malfunction but incomplete, 2 concrete observed behaviour with substantial diagnostics/context. Trigger: 0 none, 1 identifiable but incomplete/intermittent, 2 explicit reproducible actions and state/environment. Repair: 0 none/unverified, 1 merged link with generic repair semantics, 2 exact closing reference plus one or more linked commits. Testability: 0 no oracle, 1 observable failure but implicit/incomplete expected result, 2 actions and explicit expected/incorrect outcomes. Retrieval: 0 no earlier same-project repaired bug, 1 one to nine, 2 ten or more.

Automated scores are deterministic lexical triage. The 18 final candidates have documented manual score overrides in the script after reading the complete issue and PR evidence. No candidate is selected with repair score below 2 or trigger score 0.

## Ranking and diversity

Eligible records are ordered by total score descending, then project identifier and issue number. The CSV contains 60 automatically ranked eligible records, capped at five per project, plus every manually verified final selection. Final selection favours varied projects and bug classes; no project contributes more than two bugs.

## Temporal cutoff policy

The prediction timestamp and retrieval cutoff are the issue `created_at`. Only records whose own creation timestamp precedes that instant are potentially eligible. For the conservative retrieval-feasibility count, a historical repaired bug is counted only when its fixing PR was merged before the target cutoff. The target issue's fixing PR, its commits, PR text, later discussions, and target-specific duplicates are evaluation-only.

Comments are excluded from the retrieval-feasibility count because both comment tables lack timestamps. The stored issue body has no revision history, so it cannot be proven to equal the originally submitted body.

## Bias and reproducibility limitations

Label-based sampling favours projects with disciplined English-language triage and detailed templates. Requiring a merged linked PR and commits favours successfully repaired defects. Long-lived and large projects have more retrieval history and therefore score better. GitHub cross-reference links do not encode semantic relation type; exact closing language is required to reduce, not eliminate, false links. External attachments and reproduction repositories were not downloaded. Results reproduce against this local snapshot and DuckDB 2.0.0; they may change with another snapshot or label vocabulary.
""", encoding="utf-8")


def write_report(path: Path, schema: list[dict[str, Any]], relations: list[dict[str, Any]], pk: dict[str, tuple[int, int]], stats: dict[str, Any], all_candidates: list[dict[str, Any]], csv_rows: list[dict[str, Any]], selected_rows: list[dict[str, Any]], commit_info: dict[str, dict[str, Any]]) -> None:
    key_notes = {
        "project.parquet": "PK candidate: project",
        "commit_metadata.parquet": "PK/FK candidate: project → project.project",
        "issue.parquet": "PK candidate: (project, number); project FK",
        "issue_comment.parquet": "No stable comment ID; FK (project, issue_number)",
        "issue_pr.parquet": "Bridge key candidate: (project, issue_number, pr_number)",
        "pr.parquet": "PK candidate: (project, number); project FK",
        "pr_comment.parquet": "No stable comment ID; FK (project, pr_number)",
        "pr_commit.parquet": "Bridge key candidate: (project, pr_number, commit_sha)",
    }
    schema_rows = []
    for table in schema:
        important = [c for c, _ in table["columns"] if c != "__index_level_0__"]
        missing = ", ".join(f"{c}={table['missing'][c]['missing_rate_pct']}%" for c in important)
        schema_rows.append([table["file"], table["row_count"], ", ".join(f"{c}:{t}" for c, t in table["columns"]), key_notes[table["file"]], missing])
    distribution = Counter(r["total_quality_score"] for r in all_candidates if r["strong_repair_evidence"])
    categories = Counter(SELECTED[r["issue_identifier"]]["category"] for r in selected_rows)
    high_quality = sum(1 for r in all_candidates if not r["exclusion_reason"] and r["total_quality_score"] >= 8)
    commit_found = sum(1 for r in selected_rows for sha in r["commit_shas"] if sha in commit_info)
    commit_expected = sum(len(r["commit_shas"]) for r in selected_rows)
    top_projects = stats["bug_by_project"].most_common(20)
    report = f"""# OSSGameBench Dataset Audit and Bug Candidate Selection

Generated deterministically by `scripts/audit_dataset.py` against the local snapshot. This is Phase 1 only; no RAG corpus, LLM call, experiment, or Gold Reference was created.

## Executive summary

- Total issues analysed: **{stats['total_issues']:,}**
- Explicit-label bug issues: **{stats['bug_issues']:,}**
- Closed bug issues: **{stats['closed_bug_issues']:,}**
- Bug issues with non-empty body: **{stats['bug_with_body']:,}**
- Bug issues with at least one dataset issue–PR link: **{stats['bug_with_linked_pr']:,}**
- Bug issues with strong repair evidence (merged exact-closing PR + linked commit): **{stats['bug_with_repair_evidence']:,}**
- Eligible candidates scoring at least 8/10: **{high_quality:,}**
- Ranked shortlist written: **{len(csv_rows)}**; recommended bugs: **{len(selected_rows)}** across **{len(set(r['project_identifier'] for r in selected_rows))}** projects
- Selected PR commits resolved in project commit files without reading diffs: **{commit_found}/{commit_expected}**

The experiment is **feasible with safeguards**: there are enough traceable, testable defects and substantial earlier same-project history. The main adjustment before Phase 2 is to build the retrieval corpus from timestamped issues/PRs only, exclude comments unless timestamps can be recovered, and manually freeze the exact prediction-time target text.

## Dataset schema

{markdown_table(['File','Rows','Columns and DuckDB types','Candidate keys','Missing rates (important fields)'], schema_rows)}

All timestamp fields except `project.created_at` are stored as strings. Parsing must therefore be defensive. Issue/PR comment tables contain no timestamp or comment identifier. Commit rows use `(repo, sha)` as a practical key; `sha` can include a space plus ref decoration, so the first token is used when matching `pr_commit.commit_sha`.

Candidate-key uniqueness checks (raw rows / distinct key groups):

{markdown_table(['Table','Raw rows','Distinct candidate keys','Unique'], [[k,v[0],v[1],v[0]==v[1]] for k,v in pk.items()])}

## Relationship verification

{markdown_table(['Relationship','Child rows','Matched parent rows','Match %'], [[r['relationship'],r['total'],r['matched'],r['match_pct']] for r in relations])}

The join keys were verified with actual records, not names alone. One representative chain is `vcmi/vcmi#5462 → PR #5465 → f42d4dc…`; the PR text says it fixes #5462, and the project commit row supplies its timestamp/message/modified files. Equivalent commit records were resolved for {commit_found} selected PR commit links. The remaining {commit_expected - commit_found} `pr_commit` SHAs have no matching row in their project commit Parquet, so their commit timestamps/diffs are unavailable in this snapshot; the PR timestamps and SHA references remain traceable. The broad candidate query does not scan project diffs.

The bridge tables are not perfectly referential: {relations[2]['total'] - relations[2]['matched']:,} of {relations[2]['total']:,} issue-link rows do not match `(project, issue number)`, {relations[3]['total'] - relations[3]['matched']:,} do not match a PR, and `issue_pr` contains duplicate triples ({pk['issue_pr.parquet'][1]:,} distinct of {pk['issue_pr.parquet'][0]:,} rows). Candidate construction uses inner joins and exact-key deduplication, so orphan links are excluded rather than inferred.

## Explicit bug-label definition and statistics

The label vocabulary was discovered by unnesting and normalizing actual `issue.labels`. Dominant terms include `bug`, `type: bug`, `t: bug`, `crash`, `regression`, `type-defect`, and several emoji variants. The positive allow-list is recorded in the script. Negative/external labels are not positive evidence. Titles/bodies containing “bug” are not classified automatically.

Top projects by explicit-label bug count:

{markdown_table(['Project','Bug issues'], [[p,n] for p,n in top_projects])}

“Available repair evidence” is deliberately conservative: a merged linked PR must contain an exact target issue reference near `fix/close/resolve`, and `pr_commit` must contain at least one commit. A link alone is not treated as proof.

## Candidate scoring and ranking

The reproducible 0–2 criteria are defined in `sampling_protocol.md`. The score is a triage heuristic, not a validated measurement. Automatic lexical scores rank the pool; the 18 recommended records were then read in full and their documented score overrides and uncertainty notes were fixed in the script. Final candidates all have credible repair evidence and an identifiable trigger.

Strong-evidence candidate quality distribution:

{markdown_table(['Total score','Count'], [[score,distribution[score]] for score in sorted(distribution, reverse=True)])}

Recommended set:

{markdown_table(['Bug ID','Score','Prior eligible records','Category','Repair PR'], [[r['issue_identifier'],r['total_quality_score'],r['eligible_historical_records'],SELECTED[r['issue_identifier']]['category'],r['linked_pr_identifier']] for r in sorted(selected_rows,key=lambda x:(-x['total_quality_score'],x['issue_identifier']))])}

No project contributes more than two selected records. The complete ranked candidate rows and exclusion fields are in `data/bug_candidates.csv`; full original issue bodies, repair references, preliminary triggers, expected behaviour, uncertainties, and leakage warnings are in `data/selected_bugs.jsonl`.

## Preliminary bug characteristics

{markdown_table(['Category','Selected count'], [[k,v] for k,v in sorted(categories.items())])}

Representative cases include layered-map overwrite (`flame#1264`, rendering), an undiscovered-disease Case Book softlock (`CorsixTH#1799`, UI), XML save loading under ASan (`freeorion#2365`, persistence), old hex-map facing input (`maptool#2416`, input), and RHEL package-manager misdetection (`linuxgsm#4251`, compatibility). Categories are provisional and evidence-based; they are not final Gold labels.

## Temporal leakage assessment

Prediction time is the target issue `created_at`; that same timestamp is the historical retrieval cutoff. The original title/body and genuinely earlier records are prediction-time inputs. Target fixing PRs, target PR commits, repair explanations, and post-report discussion are evaluation-only.

Important risks:

1. Issue bodies have creation timestamps but no revision history or `updated_at`; a stored body may include later edits.
2. Issue and PR comments have neither creation timestamps nor IDs, so temporal filtering is impossible from this snapshot. They should be excluded from retrieval unless timestamps are recovered independently.
3. PR creation/merge timestamps exist, but commit timestamps are strings with mixed offsets and must be normalized. A future PR must never enter retrieval merely because it links to an old issue.
4. `issue_pr` records do not encode whether the relation means fixes, mentions, duplicates, or cross-reference. Exact closing language reduces but cannot completely remove semantic ambiguity.
5. Later duplicates and other issues may reveal the solution. Retrieval construction needs target-aware exclusion by issue/PR/commit identity and should audit near-duplicate text.
6. Only 14 of 52 selected `pr_commit` SHA references occur in the corresponding project commit files. Missing commit timestamps must remain null; they must not be inferred from PR merge time.

Each JSONL record contains issue creation, PR creation/merge, available commit timestamps, cutoff, and warnings. For the baseline, the target body should be manually checked against the historical GitHub version or treated explicitly as “snapshot text,” not asserted to be the original submission.

## Feasibility, limitations, and next steps

The proposed BM25-RAG comparison is feasible for a one-week course project if the final experiment stays narrow (the 18 recommended bugs) and uses manual Gold Reference construction in Phase 2. Historical-record counts measure potentially eligible same-project repaired bugs, not topical relevance; BM25 retrieval quality still needs inspection.

Before Phase 2: (1) freeze the 18 source records, (2) manually validate repair diffs and convert triggers/oracles into Gold References, (3) choose a timestamp-safe document unit, preferably pre-cutoff issues and PRs without comments, (4) remove exact/near duplicates and target identities, and (5) pre-register coverage scoring and unsupported-assumption rules. External attachments were not downloaded and may be unavailable later.

## Reproduction

From `research/`:

```bash
uv run --with duckdb==2.0.0 python scripts/audit_dataset.py
```

The script reads source Parquet in place, uses projected aggregate columns, scans only selected project commit files without `diff`, sorts deterministically, and rewrites the four Phase-1 outputs.
"""
    path.write_text(report, encoding="utf-8")


def main() -> int:
    args = parse_args()
    dataset = args.dataset_root.resolve()
    output = args.output_root.resolve()
    missing = [name for name in TOP_TABLES if not (dataset / name).is_file()]
    if missing:
        raise SystemExit(f"Dataset root {dataset} is missing: {', '.join(missing)}")
    (output / "data").mkdir(parents=True, exist_ok=True)
    (output / "reports").mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=4")
    schema = inspect_schema(con, dataset)
    relations = relation_audit(con, dataset)
    pk = primary_key_audit(con, dataset)
    candidates, stats = candidate_rows(con, dataset)
    csv_rows = write_candidates(output / "data" / "bug_candidates.csv", candidates, args.shortlist_size)
    selected_rows = [r for r in candidates if r["issue_identifier"] in SELECTED]
    missing_selected = set(SELECTED) - {r["issue_identifier"] for r in selected_rows}
    if missing_selected:
        raise SystemExit("Selected source records missing or no merged linked PR: " + ", ".join(sorted(missing_selected)))
    commit_info = inspect_selected_commits(con, dataset, selected_rows)
    write_selected(output / "data" / "selected_bugs.jsonl", selected_rows, commit_info)
    write_sampling_protocol(output / "reports" / "sampling_protocol.md")
    write_report(output / "reports" / "dataset_audit.md", schema, relations, pk, stats, candidates, csv_rows, selected_rows, commit_info)
    print(json.dumps({
        "total_issues": stats["total_issues"], "bug_issues": stats["bug_issues"],
        "repair_evidence": stats["bug_with_repair_evidence"], "shortlist_rows": len(csv_rows),
        "selected": len(selected_rows), "output_root": str(output),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
