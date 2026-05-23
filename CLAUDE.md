# Agent guide

Before touching any code under `src/crawler/`, read
[`docs/modules/INDEX.md`](docs/modules/INDEX.md) for a map of the
package layout, the call graph, and links to one doc per module.

## How to navigate

| You need to change… | Read this first |
|---|---|
| Routes, request/response schemas, or the Lambda handler | [`docs/modules/api.md`](docs/modules/api.md) |
| Topic ranking, YAKE, or weighting | [`docs/modules/classifier.md`](docs/modules/classifier.md) |
| HTML parsing (meta, OG, JSON-LD, body, language) | [`docs/modules/extractor.md`](docs/modules/extractor.md) |
| HTTP fetching, confidence scoring, robots.txt, UA | [`docs/modules/fetcher.md`](docs/modules/fetcher.md) |
| DynamoDB, S3, URL hashing | [`docs/modules/storage.md`](docs/modules/storage.md) |
| SQS / Playwright Lambda handlers | [`docs/modules/workers.md`](docs/modules/workers.md) |
| Pipeline orchestration, config, fixtures | [`docs/modules/INDEX.md`](docs/modules/INDEX.md) (top-level sections) |

## Hooks active in this repo

This project ships two committed hooks in `.claude/settings.json`:

1. **Commit-message validator** (`PreToolUse` on `Bash(git commit *)`).
   Blocks the commit unless the subject follows conventional-commits
   format (`<type>(<scope>): <description>`) and the description has
   ≥ 4 meaningful words. Allowed types: `feat, fix, docs, style,
   refactor, perf, test, chore, build, ci, revert, infra`.
   Subject ≤ 72 chars. Generic words (`wip`, `tmp`, `update`, …) are
   rejected as the only description.

2. **Module-docs nudge** (`Stop`). After your turn, if files changed
   under `src/crawler/<module>/`, the hook re-enters your turn with a
   reminder to update `docs/modules/<module>.md`. Single re-entry —
   `stop_hook_active` breaks the loop on the second pass.

Both hooks are pure Python scripts under `.claude/hooks/`. If you need
to disable temporarily, comment the entry out of `.claude/settings.json`
rather than deleting it.

## Coding norms

- Python 3.12; type hints; `from __future__ import annotations` at top of every module.
- Pure functions in `extractor/` and `classifier/` — no I/O, no global state.
- `boto3` clients are created lazily inside methods, never at module load.
- All env vars are read through `crawler.config.load_settings()`, not `os.getenv` ad-hoc.
- Tests live under `tests/` and are discovered by `pytest`.
