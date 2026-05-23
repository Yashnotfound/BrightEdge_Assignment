"""Download HTML fixtures for the classifier accuracy eval set.

Idempotent: skips files that already exist. Run from repo root:
    .local/venv/bin/python scripts/download_eval_fixtures.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "tests" / "fixtures"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Stable URLs across product / news / blog / reference / recipe / programming /
# multilingual categories. amazon_toaster, cnn_tech, rei_outdoors are already
# in tests/fixtures/ — we only download the new 17.
URLS: list[tuple[str, str]] = [
    ("wikipedia_espresso_machine", "https://en.wikipedia.org/wiki/Espresso_machine"),
    ("wikipedia_python_lang", "https://en.wikipedia.org/wiki/Python_(programming_language)"),
    ("wikipedia_climate_change", "https://en.wikipedia.org/wiki/Climate_change"),
    ("wikipedia_photosynthesis", "https://en.wikipedia.org/wiki/Photosynthesis"),
    ("wikipedia_neural_network", "https://en.wikipedia.org/wiki/Neural_network_(machine_learning)"),
    ("wikipedia_de_einstein", "https://de.wikipedia.org/wiki/Albert_Einstein"),
    ("wikipedia_es_messi", "https://es.wikipedia.org/wiki/Lionel_Messi"),
    ("wikipedia_fr_paris", "https://fr.wikipedia.org/wiki/Paris"),
    ("allrecipes_chocolate_cake", "https://www.allrecipes.com/recipe/17981/one-bowl-chocolate-cake-iii/"),
    ("bbcgoodfood_easy_chocolate_cake", "https://www.bbcgoodfood.com/recipes/easy-chocolate-cake"),
    ("bbcgoodfood_chicken_curry", "https://www.bbcgoodfood.com/recipes/easy-chicken-curry"),
    ("stackoverflow_python_ternary", "https://stackoverflow.com/questions/394809/does-python-have-a-ternary-conditional-operator"),
    ("github_python_cpython", "https://github.com/python/cpython"),
    ("github_torvalds_linux", "https://github.com/torvalds/linux"),
    ("mdn_javascript", "https://developer.mozilla.org/en-US/docs/Web/JavaScript"),
    ("arxiv_clip", "https://arxiv.org/abs/2103.00020"),
]


async def _fetch(client: httpx.AsyncClient, name: str, url: str) -> tuple[str, str, int, int]:
    dest = FIXTURES_DIR / f"{name}.html"
    if dest.exists():
        return name, "skipped (already exists)", 0, dest.stat().st_size
    try:
        r = await client.get(url, follow_redirects=True, timeout=30)
        if r.status_code == 200 and r.text:
            dest.write_text(r.text, encoding="utf-8")
            return name, "ok", r.status_code, len(r.text)
        return name, f"http {r.status_code}", r.status_code, 0
    except Exception as exc:
        return name, f"error: {exc!r}", 0, 0


async def main() -> int:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(headers={"User-Agent": UA}) as client:
        results = await asyncio.gather(*(_fetch(client, n, u) for n, u in URLS))
    failures = 0
    total_bytes = 0
    for name, status, code, size in results:
        print(f"{name:35} {status:30}  http={code}  bytes={size}")
        if status not in ("ok", "skipped (already exists)"):
            failures += 1
        total_bytes += size
    print(f"\n{len(results)} URLs, {failures} failures, {total_bytes / 1024:.1f} KB total")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
