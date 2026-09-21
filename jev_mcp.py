#!/usr/bin/env python
"""Jev as an MCP tool: any LLM harness can call a real browser agent and get the result back.

Speaks MCP over stdio, so the same server works in opencode, Codex, DeepSeek Harness
and anything else that supports MCP.

CLI (for testing or terminal use):
    python jev_mcp.py --search "Find the cheapest flight from Prague to Barcelona"
    python jev_mcp.py --search "..." --url "https://www.google.com/imghp" --log
    python jev_mcp.py --stats
    python jev_mcp.py --serve          # live stats dashboard at http://127.0.0.1:8767
    python jev_mcp.py --configure      # guided setup: API keys + browser choice
    python jev_mcp.py --browsers       # list detected Chromium forks
    python jev_mcp.py --wipe-sandbox   # delete isolated sandbox profiles

Every search saves its full result (untruncated page text included) to
artifacts/jev_outputs/<timestamp>-<goal>.json and reports the path as output_file.
"""

import json
import os
import shutil
import sys
from pathlib import Path

from jev_browser import (  # noqa: F401
    debug_alive,
    ensure_chrome,
    find_browser,
    profile_for,
    sandbox_profiles,
)
from jev_config import (  # noqa: F401
    ALLOWED_SCHEMES,
    BROWSER_FORKS,
    CHROMIUM_HINTS,
    CONTENT_CHAR_CAP,
    DEBUG_PORT,
    DEFAULT_URL,
    LEDGER,
    OUTPUT_DIR,
    PROFILE_HOME,
    ROOT,
    load_env,
)
from jev_dashboard import DASHBOARD, build_server, serve  # noqa: F401
from jev_search import run_search, save_output  # noqa: F401
from jev_security import SECRET_PATTERNS, hide_url_secrets, redact, safe_url  # noqa: F401
from jev_tools import MCPServer, jev_search, jev_stats, server  # noqa: F401
from jev_usage import (  # noqa: F401
    TYPESAFE_USD_PER_MTOK_INPUT,
    _price,
    aggregate,
    counter_summary,
    estimate_cost,
    format_stats,
    price_config,
    read_ledger,
    record_usage,
)

KEYS = (
    ("TYPESAFE_API_KEY", "TypeSafe key - required only when JEV_DECISION=typesafe"),
    ("TEXT_MODEL_API_KEY", "text-model key - required for typing, e.g. an OpenCode Go key"),
    ("TEXT_MODEL_BASE_URL", "OpenAI-compatible endpoint, e.g. https://opencode.ai/zen/go/v1"),
    ("TEXT_MODEL", "model id, e.g. deepseek-v4-flash"),
    ("JEV_CHROME", "browser path - blank auto-detects a Chromium fork"),
    ("JEV_DECISION", "decision engine: laya (local) or typesafe (API)"),
    ("JEV_LAYA_MODEL", "local checkpoint dir (models/laya-typed-decisions)"),
    ("JEV_LAYA_DEVICE", "cuda or cpu"),
)


def configure():
    load_env()
    path = ROOT / ".env"
    if not path.exists():
        template = ROOT / ".env.example"
        path.write_text(template.read_text(encoding="utf-8") if template.exists() else "", encoding="utf-8")
        print(f"created {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if "=" in line]

    print("Detected Chromium-based browsers:")
    for candidate in BROWSER_FORKS:
        full = os.path.expandvars(candidate)
        if Path(full).exists():
            print(f"  [x] {full}")
    print(f"selected: {find_browser()}\n")

    for name, hint in KEYS:
        current = next((line.partition("=")[2].strip() for line in lines if line.startswith(name + "=")), "")
        print(f"{name}  ({hint})  [{'set' if current else 'empty'}]")
        value = input("  new value, or Enter to keep: ").strip()
        if value:
            lines = [line for line in lines if not line.startswith(name + "=")] + [f"{name}={value}"]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    gitignore = ROOT / ".gitignore"
    ignored = gitignore.exists() and ".env" in gitignore.read_text(encoding="utf-8")
    print(f"\nwrote {path}   git-ignored: {ignored}")
    print(f"browser in use: {find_browser()}")
    print("Restart your LLM harness so the MCP server reloads.")


def main(argv):
    # Windows Python defaults stdout to cp1252, so printing page text containing accented or
    # CJK characters raised UnicodeEncodeError and emitted no output at all.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--wipe-sandbox" in argv:
        profiles = sandbox_profiles()
        for path in profiles:
            try:
                shutil.rmtree(path)
                print(f"removed {path}")
            except OSError as exc:
                print(f"could not remove {path}: {exc}", file=sys.stderr)
        print(f"{len(profiles)} sandbox profile(s) handled")
        return 0
    if "--configure" in argv:
        configure()
        return 0
    if "--browsers" in argv:
        for candidate in BROWSER_FORKS:
            full = os.path.expandvars(candidate)
            print(f"{'[x]' if Path(full).exists() else '[ ]'} {full}")
        browser = find_browser()
        print(f"selected: {browser}")
        print(f"profile : {profile_for(browser)}")
        if os.environ.get("JEV_SANDBOX") == "1":
            print("sandbox : ON (JEV_SANDBOX=1)")
        return 0
    if "--serve" in argv:
        port = int(argv[argv.index("--port") + 1]) if "--port" in argv else 8767
        serve(port)
        return 0
    if "--stats" in argv:
        rows = read_ledger()
        print(format_stats(aggregate(rows), rows[-10:]))
        return 0
    if "--search" in argv:
        idx = argv.index("--search")
        goal = argv[idx + 1] if len(argv) > idx + 1 else None
        if not goal:
            print("usage: --search \"<goal>\" [--url <url>] [--log]")
            return 2
        url = argv[argv.index("--url") + 1] if "--url" in argv else None
        try:
            result = run_search(goal, url, include_log="--log" in argv)
        except Exception as exc:
            result = {"status": "error", "goal": goal, "url": url,
                      "error": redact(f"{type(exc).__name__}: {exc}")}
        print(json.dumps(result, indent=2, ensure_ascii=False))
        if result.get("full_content"):
            print("\n----- full page text -----")
            print(result["full_content"])
            print("----- end full page text -----")
        totals = result.get("totals")
        if totals:
            print(
                f"[jev] {totals['searches']} searches  {totals['total_elapsed_s']}s total  "
                f"${totals['total_cost_usd']} cumulative",
                file=sys.stderr,
            )
        if result.get("output_file"):
            print(f"[jev] full output saved: {result['output_file']}", file=sys.stderr)
        return 0 if result.get("status") != "error" else 1
    if MCPServer is None:
        print("mcp package missing; run: uv add mcp", file=sys.stderr)
        return 2
    if os.environ.get("JEV_LAYA_PREWARM") == "1" and os.environ.get("JEV_DECISION", "laya") == "laya":
        print("prewarming local Laya model (~35s)...", file=sys.stderr, flush=True)
        from laya_ask import agent

        agent()
        print("model warm: first decision is instant.", file=sys.stderr, flush=True)
    server.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))