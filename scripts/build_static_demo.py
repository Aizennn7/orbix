#!/usr/bin/env python3
"""Build the GitHub Pages snapshot in docs/.

GitHub Pages serves static files only, so it cannot run the FastAPI backend.
This script captures real API responses from a running Orbix instance and
publishes the real dashboard UI plus those captured responses.

Everything is labelled with the capture time and the data source, so a snapshot
is never mistaken for a live system. Nothing is fabricated: if the running
instance is in synthetic demo mode, the snapshot says so.

Usage:
    python scripts/build_static_demo.py --base-url http://127.0.0.1:8000

Requires the server to be reachable. Output goes to docs/ and is safe to commit.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
DOCS = ROOT / "docs"

# (API path with query, output filename). Keep in sync with the reads the
# dashboard performs on load and when switching pages.
CAPTURES: list[tuple[str, str]] = [
    ("/api/auth/status", "auth-status.json"),
    ("/api/health", "health.json"),
    ("/api/preflight", "preflight.json"),
    ("/api/automation/status", "automation-status.json"),
    ("/api/providers", "providers.json"),
    ("/api/storage", "storage.json"),
    ("/api/strategies", "strategies.json"),
    ("/api/signals", "signals.json"),
    ("/api/model?pair=EUR%2FUSD", "model.json"),
    ("/api/learning", "learning.json"),
    ("/api/trader?days=7", "trader.json"),
    ("/api/broker", "broker.json"),
    ("/api/future/plan?count=6", "future-plan.json"),
    ("/api/telegram/status", "telegram-status.json"),
    ("/api/abstentions?days=7&limit=10", "abstentions.json"),
    ("/api/market?pair=EUR%2FUSD&limit=180", "market.json"),
]

BANNER = (
    '<div class="static-banner" role="status">'
    "<strong>STATIC SNAPSHOT</strong>"
    "<span>{stamp} · source: {source} · captured from a real Orbix run</span>"
    "<span>Read-only: no live feed, no scheduler, no orders. "
    "Data-changing buttons are disabled.</span>"
    "</div>"
)

BANNER_CSS = """
.static-banner {
  position: sticky; top: 0; z-index: 200;
  display: flex; flex-wrap: wrap; align-items: baseline; gap: 6px 16px;
  margin: 0; padding: 10px 18px;
  border-bottom: 1px solid var(--line-bright);
  background: rgba(18,18,18,.97); backdrop-filter: blur(16px);
  color: var(--muted); font-size: 9px; letter-spacing: .09em; line-height: 1.6;
}
.static-banner strong { color: var(--ink); letter-spacing: .18em; font-size: 9px; }
.static-banner span { min-width: 0; }
"""


def fetch(base_url: str, path: str, timeout: float = 20.0) -> dict | list:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        headers={"Accept": "application/json", "User-Agent": "Orbix-Pages-Build/1.0"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def build_index(source: str) -> str:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/static/styles.css?v=24">',
                        '<link rel="stylesheet" href="styles.css?v=24">')
    html = html.replace('<link rel="stylesheet" href="/static/styles.css?v=1">',
                        '<link rel="stylesheet" href="styles.css?v=1">')
    html = html.replace('<script src="/static/app.js?v=24" defer></script>',
                        '<script src="app.js?v=24" defer></script>')
    html = html.replace('<script src="/static/app.js?v=1" defer></script>',
                        '<script src="app.js?v=1" defer></script>')
    if "/static/" in html:
        raise SystemExit("index.html still references /static/; update build_static_demo.py")

    html = html.replace(
        "</head>",
        '  <link rel="stylesheet" href="static-demo.css">\n</head>',
        1,
    )
    html = html.replace(
        "<body>",
        f"<body>\n{BANNER.format(stamp=STAMP_TEXT, source=SOURCE_TEXT)}",
        1,
    )
    # The flag must be plain (not deferred) so it is set before app.js runs.
    html = html.replace(
        '<script src="app.js',
        "<script>window.ORBIX_STATIC = true;</script>\n  <script src=\"app.js",
        1,
    )
    html = html.replace(
        "<title>Dashboard — Orbix Signal Lab</title>",
        "<title>Orbix Signal Lab — static snapshot</title>",
        1,
    )
    return html


STAMP_TEXT = ""
SOURCE_TEXT = ""


def main() -> int:
    global STAMP_TEXT, SOURCE_TEXT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--stamp",
        default="",
        help="Override the capture timestamp shown in the banner (ISO 8601 UTC).",
    )
    args = parser.parse_args()

    stamp = args.stamp or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    STAMP_TEXT = stamp

    print(f"Capturing from {args.base_url}")
    payloads: dict[str, object] = {}
    try:
        health = fetch(args.base_url, "/api/health")
        source = str(health.get("data_source", "UNKNOWN"))  # type: ignore[union-attr]
        SOURCE_TEXT = source
        for path, filename in CAPTURES:
            try:
                payloads[filename] = fetch(args.base_url, path)
                print(f"  ok   {path} -> {filename}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                print(f"  skip {path}: {exc}", file=sys.stderr)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SystemExit(
            f"Cannot reach the server at {args.base_url}: {exc}\n"
            "Start it first: uvicorn orbix.api:app --port 8000"
        ) from exc

    if "preflight.json" not in payloads:
        raise SystemExit("Capture failed: /api/preflight is required.")

    if DOCS.exists():
        shutil.rmtree(DOCS)
    (DOCS / "api-data").mkdir(parents=True)

    for filename, payload in payloads.items():
        (DOCS / "api-data" / filename).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    shutil.copy2(WEB / "styles.css", DOCS / "styles.css")
    shutil.copy2(WEB / "app.js", DOCS / "app.js")
    (DOCS / "static-demo.css").write_text(BANNER_CSS.strip() + "\n", encoding="utf-8")
    (DOCS / "index.html").write_text(build_index(SOURCE_TEXT), encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")

    manifest = {
        "generated_at": stamp,
        "source": SOURCE_TEXT,
        "base_url": args.base_url,
        "endpoints": {name: path for path, name in CAPTURES if name in payloads},
        "note": (
            "Static snapshot for GitHub Pages. Read-only: no live feed, no scheduler, "
            "no broker orders. Generated by scripts/build_static_demo.py."
        ),
    }
    (DOCS / "snapshot.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"\nWrote {len(payloads)} snapshots to {DOCS}")
    print(f"Source: {SOURCE_TEXT} · {stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
