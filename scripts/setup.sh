#!/usr/bin/env bash
# Local-dev bootstrap. Creates .venv, installs deps editable, runs tests.
#
# Works around a macOS quirk: `python -m venv .venv` creates a dotted
# directory that gets the UF_HIDDEN filesystem flag, which causes Python's
# site.py to silently skip the editable-install .pth file. We `chflags`
# the venv to clear that flag. No-op on Linux.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v python3.12 >/dev/null 2>&1; then
  echo "ERROR: python3.12 not found on PATH." >&2
  echo "Install with: brew install python@3.12 (macOS) or your distro's equivalent." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "==> Creating .venv with python3.12"
  python3.12 -m venv .venv
fi

# macOS only: strip UF_HIDDEN flag so .pth files in .venv are honored
if [ "$(uname)" = "Darwin" ]; then
  echo "==> Clearing macOS hidden flag on .venv (workaround for site.py skipping hidden .pth)"
  chflags -R nohidden .venv
fi

echo "==> Installing project + dev deps (editable)"
.venv/bin/pip install --upgrade pip >/dev/null
.venv/bin/pip install -e ".[dev]"

# Re-strip hidden flag after install (pip may write new files that inherit it)
if [ "$(uname)" = "Darwin" ]; then
  chflags -R nohidden .venv
fi

echo "==> Verifying import works without PYTHONPATH"
.venv/bin/python -c "from crawler.api.main import app; print('  app ok:', app.title)"

echo "==> Running tests"
.venv/bin/pytest -q

echo
echo "Setup complete. To run the server locally:"
echo "  source .venv/bin/activate"
echo "  uvicorn crawler.api.main:app --reload --port 8000"
echo "Then open http://localhost:8000/"
