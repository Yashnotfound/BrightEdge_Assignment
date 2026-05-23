#!/usr/bin/env python3
"""Stop hook: if a module under src/crawler/ changed, nudge the agent to
update docs/modules/<module>.md before the turn ends.

Mechanism: when this hook returns {"decision": "block", "reason": "..."},
Claude Code re-enters the turn with the reason as a system message. On the
re-entry the input has `stop_hook_active: true`, which we use to break the
loop and exit silently.

Only triggers on changes under src/crawler/<module>/. Other edits are
ignored so the hook stays out of the way during conversational work.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SRC_ROOT = REPO_ROOT / "src" / "crawler"
DOCS_ROOT = REPO_ROOT / "docs" / "modules"


def changed_files() -> set[str]:
    """Files modified vs HEAD (staged + unstaged + untracked)."""
    files: set[str] = set()
    try:
        diff = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "diff", "HEAD", "--name-only"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        files.update(line.strip() for line in diff.splitlines() if line.strip())
    except subprocess.CalledProcessError:
        pass
    try:
        status = subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in status.splitlines():
            if len(line) > 3:
                files.add(line[3:].strip())
    except subprocess.CalledProcessError:
        pass
    return files


def stale_modules(files: set[str]) -> list[str]:
    """Return modules whose docs file is older than any changed source file."""
    touched: dict[str, float] = {}
    for f in files:
        parts = Path(f).parts
        if len(parts) < 3 or parts[0] != "src" or parts[1] != "crawler":
            continue
        mod = parts[2]
        mod_dir = SRC_ROOT / mod
        if not mod_dir.is_dir():
            continue
        src_path = REPO_ROOT / f
        try:
            mtime = src_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime > touched.get(mod, 0.0):
            touched[mod] = mtime

    stale: list[str] = []
    for mod, src_mtime in sorted(touched.items()):
        doc = DOCS_ROOT / f"{mod}.md"
        if not doc.exists():
            stale.append(mod)
            continue
        if src_mtime > doc.stat().st_mtime:
            stale.append(mod)
    return stale


def main() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        sys.exit(0)

    if data.get("stop_hook_active"):
        sys.exit(0)

    files = changed_files()
    if not files:
        sys.exit(0)

    stale = stale_modules(files)
    if not stale:
        sys.exit(0)

    reason_lines = [
        "Source changed under src/crawler/ but the module docs are stale.",
        "Update the following so future agents can navigate the codebase:",
        "",
    ]
    for mod in stale:
        reason_lines.append(f"  - docs/modules/{mod}.md")
    reason_lines.extend([
        "",
        "Each module doc should contain: Purpose, Files (one line per file),",
        "Public API (functions/classes meant to be imported), Dependencies",
        "(other crawler modules used), and Tests (path to test files).",
        "Keep it concise — this is a navigation aid, not full documentation.",
        "After updating, run no other tasks; this turn will end normally.",
    ])

    print(json.dumps({"decision": "block", "reason": "\n".join(reason_lines)}))


if __name__ == "__main__":
    main()
