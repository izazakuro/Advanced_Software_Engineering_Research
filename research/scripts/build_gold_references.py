#!/usr/bin/env python3
"""Build Phase-2 draft Gold References for the frozen 18-bug sample.

Dataset extraction is deterministic. The ANNOTATIONS mapping is an explicit,
human-authored interpretation layer and must be reviewed; generating JSON does
not make those semantic judgments automatically valid.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import duckdb
except ImportError as exc:
    raise SystemExit(
        "DuckDB is required. From research/: uv run --with duckdb==2.0.0 "
        "python scripts/build_gold_references.py"
    ) from exc


def C(cid: str, description: str, kind: str, evidence: list[str], status: str = "explicit", confidence: str = "high") -> dict[str, Any]:
    return {"condition_id": cid, "description": description, "type": kind, "evidence_keys": evidence, "status": status, "confidence": confidence}


def P(pid: str, required: list[str], ordered: list[str], evidence: list[str], relationship: str = "supported", completeness: str = "complete") -> dict[str, Any]:
    return {"path_id": pid, "required_condition_ids": required, "ordered_condition_ids": ordered, "evidence_keys": evidence, "relationship_status": relationship, "completeness": completeness}


def O(oid: str, path: str, expected: str, incorrect: str, method: str, evidence: list[str], confidence: str = "high", completeness: str = "complete", status: str = "explicit") -> dict[str, Any]:
    return {"oracle_id": oid, "trigger_path_id": path, "expected_result": expected, "incorrect_result": incorrect, "verification_method": method, "evidence_keys": evidence, "confidence": confidence, "completeness": completeness, "status": status}


# Anchors must occur verbatim (case-insensitive) in the frozen Issue snapshot.
# `complete=False` means material preconditions/fixtures/timing remain unknown.
ANNOTATIONS: dict[str, dict[str, Any]] = {
    "CorsixTH/CorsixTH#1799": {
        "category": "User interface", "secondary": "Gameplay logic", "component": "Patient detail window and disease Case Book",
        "observed": "Opening the Case Book for an emergency patient with an undiscovered disease softlocks mouse-button handling.",
        "expected": "The Case Book opens pre-set to the patient's disease.",
        "anchors": {"trigger": "Load save game attached to this ticket", "observed": "this softlocks the game", "expected": "Case Book opening", "environment": "CorsixTH Version"},
        "conditions": [C("T1", "Load the attached save and wait for the Golf Stones emergency.", "initial_state", ["trigger"]), C("T2", "Accept the emergency and wait for its patients to enter the hospital.", "sequence", ["trigger"]), C("T3", "Select an affected patient and open the Case Book from the patient detail window.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger"])],
        "oracles": [O("O1", "P1", "The Case Book opens pre-set to the patient's disease.", "The onMouseUp handler remains locked and the game requires Alt+F4.", "UI state and input-response inspection", ["observed", "expected"])],
        "limitations": ["The report gives two alternative acceptable correct behaviours."], "review_priority": "standard", "suggested": "Approve",
    },
    "freeorion/freeorion#1897": {
        "category": "Crash or exception", "secondary": "Gameplay logic", "component": "Hostless server object/effects processing across game sessions",
        "observed": "A second or later hostless game can segfault while evaluating species conditions.", "expected": "Subsequent hostless games continue without a segmentation fault.",
        "anchors": {"trigger": "second or later game", "observed": "happens segmentation fault", "environment": "Gentoo Linux AMD64"},
        "conditions": [C("T1", "Run FreeOrion in hostless mode.", "configuration", ["title"], "explicit", "medium"), C("T2", "Start a second or later game in the same server process.", "application_state", ["trigger"]), C("T3", "Reach the effects-processing state where the reported species condition is evaluated.", "game_state", ["observed"], "uncertain", "low")],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "observed"], "uncertain", "partial")],
        "oracles": [O("O1", "P1", "The later hostless session continues without SIGSEGV.", "The server receives SIGSEGV in SpeciesSimpleMatch/effects processing.", "Process exit/signal and stack-trace observation", ["observed", "pr_title"], "medium", "incomplete", "inferred")],
        "limitations": ["The exact turn, game content, and deterministic path to the crash are not documented."], "review_priority": "substantial", "suggested": "Revise",
    },
    "gameservermanagers/linuxgsm#4059": {
        "category": "Compatibility", "secondary": None, "component": "LinuxGSM SteamCMD installation for HLDS/Counter-Strike 1.6",
        "observed": "The Counter-Strike 1.6 server installation repeatedly fails while downloading App 90/depot content.", "expected": "The server files download and installation completes.",
        "anchors": {"trigger": "./csserver install", "observed": "SteamCMD: Did not complete the download", "expected": "I want to install Counter strike", "environment": "Docker Container: Ubuntu 16.04.7"},
        "conditions": [C("T1", "Use the documented Ubuntu 22.04 host and Ubuntu 16.04.7 container setup.", "platform_environment", ["environment"]), C("T2", "Configure LinuxGSM for Counter-Strike 1.6/HLDS.", "configuration", ["trigger"]), C("T3", "Run ./csserver install.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "SteamCMD finishes the HLDS download and LinuxGSM installation completes.", "App 90 ends in state 0x6 and installation stops after too many retries.", "Installer exit status and log inspection", ["observed", "expected", "pr_title"])],
        "limitations": ["Steam depot/network state is external and may affect historical reproducibility."], "review_priority": "standard", "suggested": "Approve",
    },
    "vcmi/vcmi#5462": {
        "category": "Crash or exception", "secondary": None, "component": "Multiplayer lobby room creation and local server startup",
        "observed": "The client crashes when a new multiplayer room is created.", "expected": "The newly created multiplayer room opens without a client crash.",
        "anchors": {"trigger": "create a new multiplayer room", "observed": "Game crashes when I try", "environment": "macOS 13.4.1"},
        "conditions": [C("T1", "Run the reported VCMI client on macOS 13.4.1.", "platform_environment", ["environment"], "explicit", "medium"), C("T2", "Attempt to create a new multiplayer lobby room.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "Room creation completes and the client remains running.", "The client terminates with EXC_BAD_ACCESS/SIGSEGV.", "Process survival and crash-report inspection", ["observed", "pr_title"], "high", "complete", "inferred")],
        "limitations": ["The Issue does not state whether the macOS version is necessary or merely the observed environment."], "review_priority": "standard", "suggested": "Approve",
    },
    "warzone2100/warzone2100#4125": {
        "category": "Gameplay logic", "secondary": None, "component": "Headless autohost player connection and lag-kick state",
        "observed": "After players leave, the autohost repeatedly kicks already disconnected players and becomes stuck.", "expected": "Disconnected players are not repeatedly kicked and the host does not enter a kick loop.",
        "anchors": {"trigger": "Run game in autohost mode", "observed": "stuck in a loop of auto kicking", "expected": "not be stuck in kick loop", "environment": "Linux Ubuntu 24.04"},
        "conditions": [C("T1", "Run a headless game with --autohost.", "configuration", ["trigger"]), C("T2", "Begin multiplayer play and have multiple players leave or time out.", "game_state", ["observed"], "inferred", "medium"), C("T3", "Allow lag/data checks to continue after those disconnections.", "timing_concurrency", ["observed"], "inferred", "medium")],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "observed"], "uncertain", "partial")],
        "oracles": [O("O1", "P1", "Each disconnected player is removed once and the host remains responsive.", "The same departed player is repeatedly scheduled and processed for kicking.", "Host log sequence and responsiveness inspection", ["observed", "expected", "pr_title"], "medium", "incomplete")],
        "limitations": ["The occurrence was reported once and the timing needed to reproduce it is unknown."], "review_priority": "substantial", "suggested": "Revise",
    },
    "wolvenkit/wolvenkit#2279": {
        "category": "Crash or exception", "secondary": None, "component": "WolvenKit CLI mesh material exporter",
        "observed": "Export recursively calls MergeMaterialChain until a stack overflow occurs.", "expected": "Mesh export finishes without stack overflow when material export is enabled and archives are correctly loaded.",
        "anchors": {"trigger": "WolvenKit.CLI.exe export", "observed": "a stack overflow error occurs", "environment": "WithMaterials\": true"},
        "conditions": [C("T1", "Enable MeshExportArgs.WithMaterials and configure the material repository.", "configuration", ["environment"]), C("T2", "Export the reported .mesh asset with WolvenKit.CLI.", "input", ["trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "Export returns without unbounded MergeMaterialChain recursion or stack overflow.", "The process reports Stack overflow with repeated MergeMaterialChain frames.", "Process result and stack-trace inspection", ["observed", "pr_title"], "medium", "incomplete", "inferred")],
        "limitations": ["The Issue's Expected behavior section contains the observed error rather than a stated correct result."], "review_priority": "substantial", "suggested": "Revise",
    },
    "colobot/colobot#1787": {
        "category": "Save/load or persistence", "secondary": "Crash or exception", "component": "Custom-level save parser for filesystem paths",
        "observed": "Loading the custom-level save raises CLevelParserExceptionBadParam while parsing the mod directory value.", "expected": "The custom-level save loads without the parser exception.",
        "anchors": {"trigger": "Last started level was: category=custom", "observed": "CLevelParserExceptionBadParam", "environment": "savegame/Player"},
        "conditions": [C("T1", "Use the reported custom-level save containing the recorded mod directory/path value.", "input", ["trigger", "environment"], "inferred", "medium"), C("T2", "Load that save.", "user_action", ["title"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["title", "trigger"], "uncertain", "partial")],
        "oracles": [O("O1", "P1", "The save parser accepts the stored path and loading continues.", "Loading stops with CLevelParserExceptionBadParam: Unable to parse 'hello' as string.", "Exception and load-state inspection", ["observed", "pr_title"], "medium", "incomplete", "inferred")],
        "limitations": ["The Issue does not provide a numbered reproduction procedure or an explicit expected result."], "review_priority": "substantial", "suggested": "Revise",
    },
    "lichess-org/lila#12907": {
        "category": "Gameplay logic", "secondary": None, "component": "Streaming game API FEN serialization",
        "observed": "Already-played NDJSON move packets report fullmove number 1 instead of their actual move numbers.", "expected": "Every streamed packet contains a correct FEN, including its fullmove number.",
        "anchors": {"trigger": "curl \"https://lichess.org/api/stream/game/ehDtcvBz\"", "observed": "fullmove number (the last component of the FEN) is `1`", "expected": "FEN of each line/packet of ndjson output should be correct", "environment": "use the ID of a game in progress"},
        "conditions": [C("T1", "Have an in-progress game with one or more already-played moves.", "game_state", ["environment"]), C("T2", "Request /api/stream/game/{id} for that game.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "For each played-move packet, the final FEN field equals the correct fullmove number for that position.", "The final FEN field remains 1 through the already-played move packets.", "NDJSON/FEN field-by-field output comparison", ["observed", "expected", "pr_title"])],
        "limitations": ["The example live game ID is time-dependent; a controlled fixture is needed."], "review_priority": "standard", "suggested": "Approve",
    },
    "gemrb/gemrb#1878": {
        "category": "Crash or exception", "secondary": "Gameplay logic", "component": "Actor death handling while entering Waukeen's Promenade",
        "observed": "Traveling to Waukeen's Promenade intermittently produces a segmentation fault.", "expected": "The area transition completes without a segmentation fault.",
        "anchors": {"trigger": "Traveling to Waukeen's Promenade", "observed": "gives me a Segmentation Fault", "environment": "reproduce it 100%"},
        "conditions": [C("T1", "Run Baldur's Gate II content in GemRB.", "configuration", ["title"], "explicit", "medium"), C("T2", "Travel to Waukeen's Promenade.", "user_action", ["trigger"]), C("T3", "Encounter the intermittent actor-death state with a missing killer.", "game_state", ["observed"], "uncertain", "low")],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "observed", "environment"], "uncertain", "partial")],
        "oracles": [O("O1", "P1", "The destination area loads and the process remains alive.", "The process segfaults in Actor::Die when dereferencing killer.", "Area-transition and process-signal inspection", ["observed", "pr_title"], "medium", "incomplete", "inferred")],
        "limitations": ["The reporter explicitly states the crash is not reproducible on every attempt."], "review_priority": "substantial", "suggested": "Revise",
    },
    "tmodloader/tmodloader#2644": {
        "category": "Compatibility", "secondary": "Crash or exception", "component": "Linux container tModLoader server launcher and file permissions",
        "observed": "Starting the supplied non-root Docker setup fails with a permission-denied Win32Exception.", "expected": "The tModLoader server launcher starts successfully as the configured container user.",
        "anchors": {"trigger": "Create Dockerfile like below", "observed": "System.ComponentModel.Win32Exception: Permission denied", "environment": "Latest 1.4-stable"},
        "conditions": [C("T1", "Build the supplied Ubuntu 20.04 Docker configuration with its non-root user and downloaded tModLoader files.", "configuration", ["trigger", "environment"]), C("T2", "Start the tModLoader server using the configured launcher command.", "user_action", ["trigger"], "explicit", "medium")],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "The server launcher executes and the server starts without a permission exception.", "Startup stops with System.ComponentModel.Win32Exception: Permission denied.", "Container exit status and server-log inspection", ["observed", "pr_title"], "high", "complete", "inferred")],
        "limitations": ["The Docker recipe depends on external packages and downloads."], "review_priority": "standard", "suggested": "Approve",
    },
    "longturn/freeciv21#1189": {
        "category": "Crash or exception", "secondary": None, "component": "Nation object initialization/destruction during server autogame",
        "observed": "AddressSanitizer reports a segmentation fault during nation_type destruction after autogame startup.", "expected": "Autogame completes without AddressSanitizer errors.",
        "anchors": {"trigger": "Run autogame with ASan", "observed": "SEGV on unknown address", "expected": "No errors", "environment": "master"},
        "conditions": [C("T1", "Build/run the master revision with AddressSanitizer enabled.", "configuration", ["environment", "trigger"]), C("T2", "Run autogame.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"], "uncertain", "partial")],
        "oracles": [O("O1", "P1", "Autogame exits or continues with no ASan error.", "ASan reports SEGV during nation_type destruction.", "AddressSanitizer output and process-signal inspection", ["observed", "expected", "pr_title"], "medium", "complete")],
        "limitations": ["Ruleset, seed, and autogame parameters are not documented."], "review_priority": "substantial", "suggested": "Revise",
    },
    "rptools/maptool#2393": {
        "category": "User interface", "secondary": "Compatibility", "component": "Application update version checker",
        "observed": "MapTool 1.8.3 repeatedly offers and installs 1.8.3 as a supposedly newer version.", "expected": "After installing the current version, the application no longer prompts for that same update.",
        "anchors": {"trigger": "Go to maptool download page", "observed": "it still says there is a new version available", "expected": "should not be prompted to download _again_", "environment": "MacOS 11.2"},
        "conditions": [C("T1", "Install MapTool 1.8.3 from the macOS DMG.", "initial_state", ["trigger", "environment"]), C("T2", "Launch MapTool and accept its offered update.", "user_action", ["trigger"]), C("T3", "Install the downloaded package and relaunch MapTool.", "sequence", ["trigger"])],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "The relaunched current version does not offer 1.8.3 again.", "The application again reports a new version and offers the same 1.8.3 package.", "Version display and update-prompt inspection", ["observed", "expected", "pr_title"])],
        "limitations": ["The historical GitHub release API response may no longer be reproducible."], "review_priority": "standard", "suggested": "Approve",
    },
    "flame-engine/flame#1264": {
        "category": "Rendering or graphics", "secondary": None, "component": "flame_tiled batched rendering of layered tile maps",
        "observed": "Tiles in higher layers disappear because tilesets/layers conflict during rendering.", "expected": "The rendered map matches the official Tiled editor with higher-layer tiles visible.",
        "anchors": {"trigger": "Clone [reproducible repo]", "observed": "bending machine tile does not rendered", "expected": "Renders map as same as the official map editor", "environment": "Flame version: 1.0.0"},
        "conditions": [C("T1", "Use Flame 1.0.0 and the linked reproduction project containing its layered TMX map.", "configuration", ["trigger", "environment"]), C("T2", "Run the Flame project to render the map.", "user_action", ["trigger"]), C("T3", "Open the same TMX file in Tiled for comparison.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "The Flame rendering preserves visible tiles by layer and visually matches Tiled.", "Furniture/wall tiles disappear or are overwritten by tiles from another layer.", "Rendered-image comparison", ["observed", "expected", "pr_title"])],
        "limitations": ["The reproduction repository is external and was not downloaded in Phase 2."], "review_priority": "standard", "suggested": "Approve",
    },
    "freeorion/freeorion#2365": {
        "category": "Save/load or persistence", "secondary": "Compatibility", "component": "Save-format detection under strict AddressSanitizer allocation",
        "observed": "Loading an XML save under AddressSanitizer is treated as binary and causes an allocator abort.", "expected": "The loader detects XML format and loads or rejects the save without attempting a huge allocation.",
        "anchors": {"trigger": "use address sanitizer", "observed": "sanitizer aborts the server", "environment": "file starts from '<?xml'"},
        "conditions": [C("T1", "Run the reported FreeOrion revision with AddressSanitizer.", "configuration", ["trigger"]), C("T2", "Attempt to load an XML-format savegame.", "input", ["environment", "trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"], "uncertain", "partial")],
        "oracles": [O("O1", "P1", "Format detection avoids the invalid binary allocation and the server does not abort.", "AddressSanitizer terminates the server after an enormous allocation request.", "Server process and AddressSanitizer log inspection", ["observed", "pr_title"], "medium", "incomplete", "inferred")],
        "limitations": ["The exact XML save fixture is not embedded in the Issue body."], "review_priority": "substantial", "suggested": "Revise",
    },
    "CorsixTH/CorsixTH#2881": {
        "category": "Crash or exception", "secondary": "User interface", "component": "Staff recruitment/placement dialog profile state",
        "observed": "Hiring/placing a receptionist from the supplied save produces repeated nil-profile exceptions and disconnects handlers.", "expected": "Staff recruitment and placement proceed without nil-profile exceptions.",
        "anchors": {"trigger": "began a new level and hire a hostess", "observed": "attempt to index a nil value (field 'profile')", "environment": "I used a save file from Corsix-TH stable"},
        "conditions": [C("T1", "Load the attached save originating from the stable CorsixTH version.", "initial_state", ["environment"], "explicit", "medium"), C("T2", "Begin a new level and hire a receptionist/hostess.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "The staff dialog closes/places staff without an exception and input handlers remain active.", "place_staff.lua indexes a nil profile and frame/button/key handlers disconnect.", "UI response and error-log inspection", ["observed", "pr_title"], "medium", "incomplete", "inferred")],
        "limitations": ["The Issue's Expected Behaviour field is malformed and does not state the desired result."], "review_priority": "substantial", "suggested": "Revise",
    },
    "gameservermanagers/linuxgsm#4251": {
        "category": "Compatibility", "secondary": None, "component": "Linux distribution/package-manager dependency detection",
        "observed": "On RHEL-like systems with dpkg installed, dependency checking selects dpkg/apt logic and falsely reports dependencies missing.", "expected": "Dependency detection follows the detected RHEL package manager even if dpkg is present.",
        "anchors": {"trigger": "Install fresh AlmaLinux 8.x", "observed": "installer will suggest using `apt`", "expected": "dependency checker works properly", "environment": "RedHat 8"},
        "conditions": [C("T1", "Use AlmaLinux/RHEL 8 with EPEL enabled.", "platform_environment", ["trigger", "environment"]), C("T2", "Install the dpkg package on that system.", "configuration", ["trigger"]), C("T3", "Run the Rust server installation workflow.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2", "T3"], ["T1", "T2", "T3"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "The checker uses dnf/rpm, recognizes installed dependencies, and does not recommend apt.", "The checker invokes dpkg-query, marks dependencies missing, and prints an apt command.", "Dependency-checker trace/output inspection", ["observed", "expected", "pr_title"])],
        "limitations": ["Current package-repository contents may differ from the historical environment."], "review_priority": "standard", "suggested": "Approve",
    },
    "rptools/maptool#2416": {
        "category": "Input handling", "secondary": "User interface", "component": "Token Facing tool initialization on legacy hex-grid maps",
        "observed": "Set Facing produces a repeating NullPointerException while the pointer remains over the map.", "expected": "Facing changes without an exception.",
        "anchors": {"trigger": "Only occurs on Hex Grid Maps saved in previous versions", "observed": "java error. This error returns constantly", "expected": "change Facing, without errors", "environment": "Version: 1.8.3"},
        "conditions": [C("T1", "Open a hex-grid map saved by an earlier MapTool version before opening Map Properties.", "initial_state", ["trigger"]), C("T2A", "Invoke Set Facing from the token menu.", "user_action", ["trigger"]), C("T2B", "Alternatively, use Shift+mouse-wheel to change facing.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2A"], ["T1", "T2A"], ["trigger"]), P("P2", ["T1", "T2B"], ["T1", "T2B"], ["trigger"])],
        "oracles": [O("O1", "P1", "Facing changes and no repeating exception appears.", "A NullPointerException repeats while the pointer remains over the map.", "Token orientation and exception-log inspection", ["observed", "expected", "pr_title"]), O("O2", "P2", "Facing changes and no repeating exception appears.", "A NullPointerException repeats while the pointer remains over the map.", "Token orientation and exception-log inspection", ["observed", "expected", "pr_title"])],
        "limitations": ["A concrete legacy map fixture is not embedded in the dataset."], "review_priority": "standard", "suggested": "Approve",
    },
    "flame-engine/flame#1003": {
        "category": "User interface", "secondary": None, "component": "GameWidget lifecycle under Flutter StatefulWidget rebuilds",
        "observed": "After setState rebuilds the widget tree, gameRef cannot find the game instance and throws Bad state.", "expected": "The game instance remains available after the StatefulWidget rebuild.",
        "anchors": {"trigger": "every time the setState() is called", "observed": "Bad state: Cannot find reference MyGame", "expected": "game instance should exist even the widget is rebuilt", "environment": "StatefulWidget"},
        "conditions": [C("T1", "Place a persistent Game instance in the supplied StatefulWidget/GameWidget structure.", "configuration", ["environment", "trigger"]), C("T2", "Call setState so the containing widget rebuilds.", "user_action", ["trigger"])],
        "paths": [P("P1", ["T1", "T2"], ["T1", "T2"], ["trigger", "environment"])],
        "oracles": [O("O1", "P1", "After rebuild, gameRef resolves the same game instance and no exception is thrown.", "gameRef is invalid and throws Bad state: Cannot find reference MyGame.", "Exception observation and game-instance identity check", ["observed", "expected", "pr_title"])],
        "limitations": ["The base reproduction repository is external and was not downloaded."], "review_priority": "standard", "suggested": "Approve",
    },
}


def parse_args() -> argparse.Namespace:
    here = Path(__file__).resolve()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-root", type=Path, default=here.parents[1])
    parser.add_argument("--dataset-root", type=Path, default=here.parents[2] / "OSSGameBench")
    return parser.parse_args()


def sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def excerpt(text: str, anchor: str, max_chars: int = 620) -> str:
    pos = text.casefold().find(anchor.casefold())
    if pos < 0:
        raise ValueError(f"Evidence anchor not found: {anchor!r}")
    start = max(text.rfind("\n\n", 0, pos), text.rfind("\r\n\r\n", 0, pos))
    start = 0 if start < 0 else start + 2
    end_candidates = [x for x in (text.find("\n\n", pos), text.find("\r\n\r\n", pos)) if x >= 0]
    end = min(end_candidates) if end_candidates else len(text)
    value = text[start:end].strip()
    if len(value) > max_chars:
        left = max(0, pos - start - max_chars // 3)
        value = value[left:left + max_chars].strip()
    return value


def add_evidence(items: list[dict[str, Any]], eid: str, source_type: str, record_id: str, source_file: str, field: str, value: str, evidence_type: str, temporal_role: str, temporally_verified: bool, timestamp_available: bool = True) -> None:
    items.append({
        "evidence_id": eid, "source_type": source_type, "source_record_id": record_id,
        "source_file": source_file, "source_field": field, "excerpt": value,
        "evidence_type": evidence_type, "evidence_availability": "available",
        "temporally_verified": temporally_verified, "timestamp_available": timestamp_available,
        "temporal_role": temporal_role,
    })


def find_pr_link_excerpt(project: str, issue_number: int, title: str, body: str) -> tuple[str, str]:
    number = re.escape(str(issue_number))
    ref = rf"(?:{re.escape(project)}#)?#{number}(?!\d)|{re.escape(project)}#{number}(?!\d)"
    repair = r"(?:fix(?:e[sd])?|clos(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))"
    for field, text in (("title", title), ("body", body)):
        for pattern in (rf"{repair}.{{0,120}}{ref}", rf"{ref}.{{0,120}}{repair}"):
            match = re.search(pattern, text, re.I | re.S)
            if match:
                start = max(0, match.start() - 160); end = min(len(text), match.end() + 260)
                return field, text[start:end].strip()
    raise ValueError(f"PR does not explicitly fix/close {project}#{issue_number}")


def load_frozen(path: Path) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(rows) != 18 or len({r["stable_bug_id"] for r in rows}) != 18:
        raise ValueError("Phase-1 selected_bugs.jsonl must contain exactly 18 unique records")
    if set(ANNOTATIONS) != {r["stable_bug_id"] for r in rows}:
        raise ValueError("Annotation IDs do not exactly match the frozen Phase-1 sample")
    return rows


def load_source_records(con: duckdb.DuckDBPyConnection, dataset: Path, frozen: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out = {}
    for item in frozen:
        bug_id = item["stable_bug_id"]; project = item["project_identifier"]; number = item["original_issue_number"]
        issue = con.execute(
            f"SELECT project,number,title,body,labels,state,created_at,closed_at FROM read_parquet('{sql_path(dataset/'issue.parquet')}') WHERE project=? AND number=?",
            [project, number],
        ).fetchone()
        if not issue:
            raise ValueError(f"Missing frozen Issue {bug_id}")
        issue_data = dict(zip([d[0] for d in con.description], issue))
        if issue_data["title"] != item["original_issue_title"] or issue_data["body"] != item["original_issue_body"] or issue_data["created_at"] != item["issue_creation_timestamp"]:
            raise ValueError(f"Frozen Issue snapshot drift detected for {bug_id}")
        pr_id = item["linked_repair_evidence"]["pr_identifier"]
        pr_number = int(pr_id.rsplit("#", 1)[1])
        pr = con.execute(
            f"SELECT project,number,title,body,state,created_at,closed_at,merged_at FROM read_parquet('{sql_path(dataset/'pr.parquet')}') WHERE project=? AND number=?",
            [project, pr_number],
        ).fetchone()
        if not pr:
            raise ValueError(f"Missing fixing PR {pr_id}")
        pr_data = dict(zip([d[0] for d in con.description], pr))
        link = con.execute(
            f"SELECT count(*) FROM read_parquet('{sql_path(dataset/'issue_pr.parquet')}') WHERE project=? AND issue_number=? AND pr_number=?",
            [project, number, pr_number],
        ).fetchone()[0]
        if not link or not pr_data["merged_at"]:
            raise ValueError(f"Fixing PR relation/merge unavailable for {bug_id}")
        link_field, link_excerpt = find_pr_link_excerpt(project, number, pr_data["title"] or "", pr_data["body"] or "")
        out[bug_id] = {"issue": issue_data, "pr": pr_data, "pr_id": pr_id, "pr_link_field": link_field, "pr_link_excerpt": link_excerpt, "frozen": item}
    return out


def load_commit_diffs(con: duckdb.DuckDBPyConnection, dataset: Path, records: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    wanted: dict[str, set[str]] = defaultdict(set)
    for record in records.values():
        for commit in record["frozen"]["linked_repair_evidence"]["commits"]:
            if commit.get("created_at"):
                wanted[record["issue"]["project"]].add(commit["sha"])
    found: dict[str, dict[str, Any]] = {}
    for project, shas in wanted.items():
        values = ",".join("'" + s.replace("'", "''") + "'" for s in sorted(shas))
        paths = sorted((dataset / "commits").glob(f"commits|{project.replace('/', '|')}*.parquet"))
        for path in paths:
            rows = con.execute(
                f"SELECT split_part(sha,' ',1),created_at,message,files,diff FROM read_parquet('{sql_path(path)}') WHERE split_part(sha,' ',1) IN ({values})"
            ).fetchall()
            for sha, created, message, files, diffs in rows:
                patch = None; patch_file = None
                for change in diffs or []:
                    if change.get("patch"):
                        patch = change["patch"][:1000]; patch_file = change.get("file"); break
                found[sha] = {"created_at": created, "message": message or "", "files": files or [], "patch": patch, "patch_file": patch_file, "source_file": path.name}
    return found


def evidence_key_map(evidence: list[dict[str, Any]]) -> dict[str, str]:
    return {item.pop("semantic_key"): item["evidence_id"] for item in evidence}


def fact(value: Any, status: str, confidence: str, keys: list[str], keymap: dict[str, str]) -> dict[str, Any] | None:
    if value is None:
        return None
    return {"value": value, "status": status, "confidence": confidence, "evidence_ids": [keymap[k] for k in keys]}


def build_record(bug_id: str, source: dict[str, Any], commit_diffs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cfg = ANNOTATIONS[bug_id]; issue = source["issue"]; pr = source["pr"]
    evidence: list[dict[str, Any]] = []

    def ev(key: str, eid: str, stype: str, rid: str, sfile: str, field: str, value: str, etype: str, role: str, verified: bool, timestamp: bool = True) -> None:
        item: dict[str, Any] = {"semantic_key": key}
        add_evidence(evidence, eid, stype, rid, sfile, field, value, etype, role, verified, timestamp)
        evidence[-1]["semantic_key"] = key

    ev("title", "E1", "issue", bug_id, "issue.parquet", "title", issue["title"], "target_report", "target_snapshot", False)
    next_id = 2
    for key in ("trigger", "observed", "expected", "environment"):
        anchor = cfg["anchors"].get(key)
        if anchor:
            ev(key, f"E{next_id}", "issue", bug_id, "issue.parquet", "body", excerpt(issue["body"] or "", anchor), f"issue_{key}", "target_snapshot", False)
            next_id += 1
    ev("pr_title", f"E{next_id}", "pr", source["pr_id"], "pr.parquet", "title", pr["title"] or "", "repair_summary", "evaluation_only_future", False); next_id += 1
    ev("pr_link", f"E{next_id}", "pr", source["pr_id"], "pr.parquet", source["pr_link_field"], source["pr_link_excerpt"], "explicit_fix_link", "evaluation_only_future", False); next_id += 1

    first_commit_key = None; first_diff_key = None
    for commit in source["frozen"]["linked_repair_evidence"]["commits"]:
        sha = commit["sha"]; info = commit_diffs.get(sha)
        if not info:
            continue
        if info["message"] and first_commit_key is None:
            first_commit_key = "commit_message"
            ev(first_commit_key, f"E{next_id}", "commit", sha, f"commits/{info['source_file']}", "message", info["message"].strip(), "repair_commit", "evaluation_only_future", True); next_id += 1
        if info["patch"] and first_diff_key is None:
            first_diff_key = "commit_diff"
            ev(first_diff_key, f"E{next_id}", "commit", sha, f"commits/{info['source_file']}", f"diff.patch ({info['patch_file']})", info["patch"], "repair_diff", "evaluation_only_future", True); next_id += 1

    keymap = evidence_key_map(evidence)
    conditions = []
    for condition in cfg["conditions"]:
        item = {k: v for k, v in condition.items() if k != "evidence_keys"}
        item["evidence_ids"] = [keymap[k] for k in condition["evidence_keys"]]
        conditions.append(item)
    paths = []
    for path in cfg["paths"]:
        item = {k: v for k, v in path.items() if k != "evidence_keys"}
        item["evidence_ids"] = [keymap[k] for k in path["evidence_keys"]]
        item["expression"] = " AND ".join(path["required_condition_ids"])
        paths.append(item)
    oracles = []
    for oracle in cfg["oracles"]:
        item = {k: v for k, v in oracle.items() if k != "evidence_keys"}
        item["evidence_ids"] = [keymap[k] for k in oracle["evidence_keys"]]
        oracles.append(item)

    expected_key = "expected" if "expected" in keymap else "observed"
    expected_status = "explicit" if "expected" in keymap else "inferred"
    expected_confidence = "high" if "expected" in keymap else "medium"
    commit_rows = source["frozen"]["linked_repair_evidence"]["commits"]
    return {
        "bug_id": bug_id, "project_id": issue["project"], "issue_id": bug_id,
        "issue_number": issue["number"], "issue_title": issue["title"],
        "issue_created_at": issue["created_at"], "issue_labels": issue["labels"] or [],
        "bug_category": fact(cfg["category"], "inferred", "high", ["title", "observed"], keymap),
        "secondary_category": fact(cfg["secondary"], "inferred", "medium", ["title", "observed"], keymap) if cfg["secondary"] else None,
        "affected_component": fact(cfg["component"], "inferred", "high", ["title", "trigger", "pr_title"], keymap),
        "observed_behavior": fact(cfg["observed"], "explicit", "high", ["observed"], keymap),
        "expected_behavior": fact(cfg["expected"], expected_status, expected_confidence, [expected_key], keymap),
        "trigger_conditions": conditions, "trigger_paths": paths, "test_oracles": oracles,
        "evidence": evidence,
        "repair_evidence_summary": {
            "fixing_pr_id": source["pr_id"], "pr_created_at": pr["created_at"], "pr_merged_at": pr["merged_at"],
            "explicit_fix_link_evidence_id": keymap["pr_link"],
            "repair_commit_shas": [c["sha"] for c in commit_rows],
            "commit_records_available": sum(c["sha"] in commit_diffs for c in commit_rows),
            "commit_records_unavailable": sum(c["sha"] not in commit_diffs for c in commit_rows),
            "commit_diff_evidence_ids": [keymap[k] for k in (first_diff_key,) if k],
        },
        "known_limitations": cfg["limitations"] + ["Issue text has no edit history; snapshot-at-creation equivalence is unverified."],
        "annotation_status": "pending_review", "review_priority": cfg["review_priority"],
        "suggested_review_decision": cfg["suggested"], "final_human_decision": None,
        "review_notes": [],
        "semantic_interpretation_notice": "Trigger relationships, category, component, and inferred expectations are human-authored draft judgments, not deterministic extraction results.",
    }


def build_leakage(record: dict[str, Any]) -> dict[str, Any]:
    repair = record["repair_evidence_summary"]
    return {
        "bug_id": record["bug_id"], "retrieval_cutoff_timestamp": record["issue_created_at"],
        "target_issue": {"record_id": record["issue_id"], "eligible_input_fields": ["title", "body snapshot"], "restriction": "Target identity must be excluded from historical retrieval results."},
        "restricted_fixing_prs": [repair["fixing_pr_id"]],
        "restricted_repair_commits": repair["repair_commit_shas"],
        "restricted_source_records": [
            {"record_id": repair["fixing_pr_id"], "reason": "Target-specific future repair/evaluation evidence"},
            *[{"record_id": sha, "reason": "Target-specific repair commit/evaluation evidence"} for sha in repair["repair_commit_shas"]],
            {"record_id": record["bug_id"] + ":gold_reference", "reason": "Gold Reference evaluation artifact"},
        ],
        "post_report_discussions_restricted": True,
        "temporal_uncertainties": [
            "Issue title/body edit history is unavailable; the stored snapshot may include post-creation edits.",
            "Issue and PR comments have no timestamps and are ineligible for retrieval.",
            f"{repair['commit_records_unavailable']} linked repair commit record(s) lack project-level timestamp/diff data.",
            "Near-duplicate or later issues may reveal the target repair and require target-aware filtering.",
        ],
    }


def review_markdown(records: list[dict[str, Any]]) -> str:
    lines = [
        "# Gold Reference Draft Review", "",
        "All 18 records are frozen Phase-1 bugs and remain `pending_review`. Repair PRs, Commits, and diffs shown here are evaluation-only and must not enter future model inputs.", "",
        "Allowed final decisions: **Approve**, **Revise**, **Insufficient Evidence**. The final decision field is intentionally blank.", "",
    ]
    for idx, r in enumerate(records, 1):
        lines += [f"## {idx}. {r['bug_id']} — {r['issue_title']}", ""]
        lines += [f"- Primary category: {r['bug_category']['value']}", f"- Affected component: {r['affected_component']['value']}", f"- Review priority: {r['review_priority']}", f"- Suggested review decision: **{r['suggested_review_decision']}**", "- Final human decision: **________________**", ""]
        lines += ["### Bug summary", "", f"Observed: {r['observed_behavior']['value']}", "", f"Expected: {r['expected_behavior']['value']} ({r['expected_behavior']['status']}, {r['expected_behavior']['confidence']} confidence)", ""]
        lines += ["### Trigger conditions", ""]
        for c in r["trigger_conditions"]:
            lines.append(f"- **{c['condition_id']}** [{c['type']}; {c['status']}; {c['confidence']}]: {c['description']} — evidence {', '.join(c['evidence_ids'])}")
        lines += ["", "### Trigger relationships", ""]
        for p in r["trigger_paths"]:
            order = " → ".join(p["ordered_condition_ids"]) if p["ordered_condition_ids"] else "not established"
            lines.append(f"- **{p['path_id']}**: `{p['expression']}`; order: `{order}`; relationship={p['relationship_status']}; completeness={p['completeness']}")
        lines += ["", "### Test Oracles", ""]
        for o in r["test_oracles"]:
            lines += [f"- **{o['oracle_id']}** for {o['trigger_path_id']} ({o['confidence']}, {o['completeness']}):", f"  - Expected: {o['expected_result']}", f"  - Incorrect: {o['incorrect_result']}", f"  - Verification: {o['verification_method']}", f"  - Evidence: {', '.join(o['evidence_ids'])}"]
        lines += ["", "### Evidence excerpts", ""]
        for e in r["evidence"]:
            compact = e["excerpt"].replace("\r", " ").replace("\n", " ")
            if len(compact) > 360:
                compact = compact[:357] + "…"
            lines.append(f"- **{e['evidence_id']}** `{e['source_file']} / {e['source_record_id']} / {e['source_field']}` ({e['evidence_type']}, {e['temporal_role']}): “{compact}”")
        lines += ["", "### Missing information and potential unsupported assumptions", ""]
        for item in r["known_limitations"]:
            lines.append(f"- {item}")
        if any(p["relationship_status"] == "uncertain" for p in r["trigger_paths"]):
            lines.append("- At least one condition combination/order is not fully established by the source.")
        lines += ["", "---", ""]
    return "\n".join(lines)


def main() -> int:
    args = parse_args(); research = args.research_root.resolve(); dataset = args.dataset_root.resolve()
    frozen_path = research / "data" / "selected_bugs.jsonl"
    if not frozen_path.is_file():
        raise SystemExit(f"Missing frozen Phase-1 input: {frozen_path}")
    frozen = load_frozen(frozen_path)
    with (research / "data" / "bug_candidates.csv").open(encoding="utf-8", newline="") as handle:
        phase1_selected = {r["issue_identifier"] for r in csv.DictReader(handle) if r["candidate_selection_status"] == "selected"}
    if phase1_selected != set(ANNOTATIONS):
        raise SystemExit("Phase-1 CSV selected IDs do not match the frozen JSONL/annotation IDs")
    con = duckdb.connect(); con.execute("SET threads=4")
    source = load_source_records(con, dataset, frozen)
    commit_diffs = load_commit_diffs(con, dataset, source)
    records = [build_record(bug_id, source[bug_id], commit_diffs) for bug_id in sorted(ANNOTATIONS)]
    gold_path = research / "data" / "gold_references_draft.jsonl"
    leak_path = research / "data" / "leakage_manifest.jsonl"
    gold_path.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in records), encoding="utf-8")
    leak_path.write_text("".join(json.dumps(build_leakage(r), ensure_ascii=False, default=str) + "\n" for r in records), encoding="utf-8")
    (research / "reports" / "gold_reference_review.md").write_text(review_markdown(records), encoding="utf-8")
    print(json.dumps({
        "gold_references": len(records), "complete_trigger_paths": sum(all(p["completeness"] == "complete" for p in r["trigger_paths"]) for r in records),
        "usable_oracles": sum(all(o["completeness"] in {"complete", "incomplete"} and o["confidence"] in {"high", "medium"} for o in r["test_oracles"]) for r in records),
        "substantial_review": sum(r["review_priority"] == "substantial" for r in records),
        "commit_diffs_loaded": sum(bool(e["evidence_type"] == "repair_diff") for r in records for e in r["evidence"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
