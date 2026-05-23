#!/usr/bin/env python3
"""PreToolUse hook: validate that `git commit` messages describe what changed.

Reads the tool-call payload from stdin, extracts the commit message, and
emits a JSON `permissionDecision` of allow or deny. Only fires for
`git commit` invocations; everything else passes through.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

ALLOWED_TYPES = (
    "feat", "fix", "docs", "style", "refactor", "perf",
    "test", "chore", "build", "ci", "revert", "infra",
)

GENERIC_WORDS = {
    "wip", "tmp", "temp", "asdf", "stuff", "things", "misc",
    "update", "updates", "fix", "fixes", "change", "changes",
    "edit", "edits", "tweak", "tweaks", "minor", "various",
}

MIN_SUBJECT_WORDS = 4
MAX_SUBJECT_LEN = 72


def emit(decision: str, reason: str) -> None:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(payload))
    sys.exit(0)


def extract_message(cmd: str) -> str | None:
    """Return the commit subject, or None if not a `git commit` we can read."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None

    if not tokens or tokens[0] != "git":
        return None
    if "commit" not in tokens:
        return None

    i = tokens.index("commit") + 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-m", "--message"):
            if i + 1 < len(tokens):
                return tokens[i + 1].splitlines()[0].strip()
            return None
        if tok.startswith("--message="):
            return tok.split("=", 1)[1].splitlines()[0].strip()
        if tok in ("-F", "--file"):
            if i + 1 < len(tokens):
                path = Path(tokens[i + 1])
                if path.is_file():
                    return path.read_text().splitlines()[0].strip()
            return None
        if tok == "--amend" or tok == "-C" or tok == "--reuse-message":
            return ""
        i += 1
    return None


def validate(subject: str) -> tuple[bool, str]:
    if not subject:
        return False, "commit subject is empty"

    if len(subject) > MAX_SUBJECT_LEN:
        return False, f"subject is {len(subject)} chars (max {MAX_SUBJECT_LEN})"

    m = re.match(
        r"^(?P<type>[a-z]+)(\([\w\-./]+\))?!?:\s*(?P<desc>.+)$",
        subject,
    )
    if not m:
        return False, (
            "subject must follow conventional-commits format: "
            "`<type>(<scope>): <description>` "
            f"(allowed types: {', '.join(ALLOWED_TYPES)})"
        )

    ctype = m.group("type")
    desc = m.group("desc").strip()

    if ctype not in ALLOWED_TYPES:
        return False, (
            f"unknown commit type '{ctype}'. "
            f"allowed: {', '.join(ALLOWED_TYPES)}"
        )

    words = [w for w in re.findall(r"[A-Za-z0-9]+", desc) if len(w) > 1]
    if len(words) < MIN_SUBJECT_WORDS:
        return False, (
            f"description has {len(words)} meaningful words "
            f"(need >= {MIN_SUBJECT_WORDS}); say what changed and why"
        )

    desc_lower = desc.lower().strip(" .")
    if desc_lower in GENERIC_WORDS:
        return False, f"description '{desc}' is too generic"
    first_word = words[0].lower() if words else ""
    if first_word in GENERIC_WORDS and len(words) <= MIN_SUBJECT_WORDS:
        return False, (
            f"description starts with generic word '{first_word}' "
            "and lacks specifics about what changed"
        )

    return True, "ok"


def main() -> None:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        emit("allow", "hook: unparseable input, passing through")

    if data.get("tool_name") != "Bash":
        emit("allow", "hook: not a Bash call")

    cmd = (data.get("tool_input") or {}).get("command", "")
    subject = extract_message(cmd)
    if subject is None:
        emit("allow", "hook: not a git commit with inline message")

    ok, reason = validate(subject)
    if ok:
        emit("allow", f"commit message accepted: '{subject}'")
    emit(
        "deny",
        f"commit message rejected — {reason}.\n"
        f"got: '{subject}'\n"
        "use `<type>(<scope>): <what changed and why>`, e.g. "
        "'fix(fetcher): retry on 503 to bypass transient CDN errors'",
    )


if __name__ == "__main__":
    main()
