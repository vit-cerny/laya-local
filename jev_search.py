import json
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

from jev_browser import ensure_chrome
from jev_config import CONTENT_CHAR_CAP, DEFAULT_URL, OUTPUT_DIR, load_env
from jev_security import hide_url_secrets, redact, safe_url
from jev_usage import counter_summary, estimate_cost, read_ledger, record_usage


def save_output(result):
    """Persist the full result - including the untruncated page text - under
    artifacts/jev_outputs/, so a long page is never lost to the caller's context cap.
    Returns the written path. The file is a superset of the tool result."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", str(result.get("goal") or "search").lower()).strip("-")[:48]
    path = OUTPUT_DIR / f"{stamp}-{slug or 'search'}.json"
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def run_search(goal, url=None, max_content_chars=6000, include_log=False):
    """Run one Jev search and return a result the calling LLM can act on."""
    load_env()
    started_iso = datetime.now(timezone.utc).isoformat()
    url_omitted = url is None
    start_url, url_error = safe_url(url)
    if url_error:
        raise ValueError(url_error)
    if start_url is None:
        start_url = DEFAULT_URL + quote_plus(goal)
    max_content_chars = max(200, min(int(max_content_chars), CONTENT_CHAR_CAP))
    browser, chrome_state = ensure_chrome()

    from jev_ultrafast import Agent

    # A run can exhaust the agent's decision budget or fail mid-loop. Capture that and still
    # return and record the outcome: a caller must never get silent empty output, and the cost
    # of a runaway run still belongs in the ledger.
    error = None
    with Agent(start_url, goal) as agent:
        try:
            for _ in agent.run():
                pass
        except Exception as exc:
            error = redact(f"{type(exc).__name__}: {exc}")
        snap = agent.snapshot()

    page = snap["page"]
    history = snap.get("history", [])
    decisions = snap.get("decisions", [])
    text_calls = snap.get("text_calls", [])

    usage = {
        "decisions": len(decisions),
        "typesafe_latency_ms": sum(d.get("latency_ms", 0) or 0 for d in decisions),
        "typesafe_input_tokens": sum((d.get("usage") or {}).get("input_tokens", 0) for d in decisions),
        "typesafe_output_tokens": sum((d.get("usage") or {}).get("output_tokens", 0) for d in decisions),
        "browser_actions": len(history),
        "text_calls": len(text_calls),
        "text_model": text_calls[-1]["model"] if text_calls else None,
        "prompt_tokens": sum((t.get("usage") or {}).get("prompt_tokens", 0) for t in text_calls),
        "completion_tokens": sum((t.get("usage") or {}).get("completion_tokens", 0) for t in text_calls),
        "text_latency_ms": sum(t.get("latency_ms", 0) or 0 for t in text_calls),
    }
    cost, cost_note = estimate_cost(usage)
    usage["est_cost_usd"] = cost
    usage["cost_note"] = cost_note

    content = (page.get("text") or "").strip()
    trimmed = len(content) > max_content_chars
    result = {
        "status": snap.get("status"),
        "error": error,
        "browser": browser,
        "goal": snap.get("goal", goal),
        "start_url": start_url,
        "url_omitted": url_omitted,
        "warning": (
            "url was omitted, so this began as a Google keyword search of the goal text. "
            "Pass url for anything site-specific."
        ) if url_omitted else None,
        "final_url": page.get("url"),
        "title": page.get("title"),
        "content": content[:max_content_chars],
        "content_truncated": trimmed,
        "content_chars": len(content),
        "full_content": content,
        "visited": [
            {"step": h.get("step"), "operation": h.get("operation"), "target": h.get("target"), "text": h.get("text")}
            for h in history
        ],
        "elapsed_ms": snap.get("elapsed_ms"),
        "usage": usage,
        "caveat": (
            "status is the agent's own choice; trust final_url and content as what it actually reached. "
            "BLOCKED usually means no supported control matched, not that the page was empty."
        ),
    }

    record_usage(
        {
            "ts": started_iso,
            "goal": goal,
            "start_url": hide_url_secrets(start_url),
            "final_url": hide_url_secrets(page.get("url")),
            "status": snap.get("status"),
            "error": error,
            "elapsed_ms": snap.get("elapsed_ms"),
            "browser": browser,
            "chrome": chrome_state,
            **usage,
        }
    )

    rows = read_ledger()
    result["totals"] = counter_summary(rows)
    if include_log:
        result["log"] = [
            {
                "ts": row.get("ts"),
                "status": row.get("status"),
                "elapsed_ms": row.get("elapsed_ms"),
                "cost_usd": row.get("est_cost_usd"),
                "goal": row.get("goal"),
            }
            for row in rows[-10:]
        ]
    result["output_file"] = str(save_output(result))
    return result