import json
import os
from collections import Counter

from jev_config import LEDGER

TYPESAFE_USD_PER_MTOK_INPUT = 0.042  # published rate; TypeSafe output tokens are free


def _price(name, default):
    raw = os.environ.get(name)
    if raw in (None, ""):
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def price_config():
    return {
        "typesafe_per_mtok_input": _price("JEV_TYPESAFE_PRICE_PER_MTOK_INPUT", TYPESAFE_USD_PER_MTOK_INPUT),
        "text_prompt_per_m": _price("JEV_TEXT_PRICE_PROMPT_PER_M", 0.0),
        "text_completion_per_m": _price("JEV_TEXT_PRICE_COMPLETION_PER_M", 0.0),
    }


def estimate_cost(usage):
    """TypeSafe bills input tokens only (output free) at the published $0.042/MTok.
    The text helper runs on an OpenCode Go subscription, so its marginal rate defaults
    to 0 until JEV_TEXT_PRICE_*_PER_M is set."""
    prices = price_config()
    usd = (
        prices["typesafe_per_mtok_input"] * usage.get("typesafe_input_tokens", 0) / 1_000_000
        + prices["text_prompt_per_m"] * usage.get("prompt_tokens", 0) / 1_000_000
        + prices["text_completion_per_m"] * usage.get("completion_tokens", 0) / 1_000_000
    )
    note = (
        f"TypeSafe input ${prices['typesafe_per_mtok_input']}/MTok (output free); "
        f"text ${prices['text_prompt_per_m']}/MTok prompt, "
        f"${prices['text_completion_per_m']}/MTok completion."
    )
    return round(usd, 8), note


def record_usage(entry):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_ledger():
    if not LEDGER.exists():
        return []
    rows = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def aggregate(rows):
    searches = len(rows)
    total_ms = sum(r.get("elapsed_ms") or 0 for r in rows)
    decisions = sum(r.get("decisions") or 0 for r in rows)
    ts_input = sum(r.get("typesafe_input_tokens") or 0 for r in rows)
    prompt = sum(r.get("prompt_tokens") or 0 for r in rows)
    completion = sum(r.get("completion_tokens") or 0 for r in rows)
    costs = [r["est_cost_usd"] for r in rows if r.get("est_cost_usd") is not None]
    _, cost_note = estimate_cost(
        {"typesafe_input_tokens": ts_input, "prompt_tokens": prompt, "completion_tokens": completion}
    )
    totals = {
        "searches": searches,
        "total_elapsed_ms": total_ms,
        "total_elapsed_s": round(total_ms / 1000, 1),
        "avg_elapsed_ms": round(total_ms / searches) if searches else 0,
        "total_decisions": decisions,
        "total_typesafe_input_tokens": ts_input,
        "total_browser_actions": sum(r.get("browser_actions") or 0 for r in rows),
        "total_text_calls": sum(r.get("text_calls") or 0 for r in rows),
        "total_prompt_tokens": prompt,
        "total_completion_tokens": completion,
        "total_tokens": prompt + completion,
        "total_cost_usd": round(sum(costs), 8) if costs else None,
        "cost_note": cost_note,
        "statuses": dict(Counter(r.get("status") for r in rows)),
    }
    return totals


def counter_summary(rows):
    """Cumulative time and price counter shown when a search finishes."""
    totals = aggregate(rows)
    return {
        "searches": totals["searches"],
        "total_elapsed_s": totals["total_elapsed_s"],
        "avg_elapsed_ms": totals["avg_elapsed_ms"],
        "total_typesafe_input_tokens": totals["total_typesafe_input_tokens"],
        "total_cost_usd": totals["total_cost_usd"],
    }


def format_stats(totals, recent=None):
    lines = [
        f"Jev usage: {totals['searches']} searches, {totals['total_elapsed_s']}s total "
        f"(avg {totals['avg_elapsed_ms']}ms)",
        f"  decisions={totals['total_decisions']}  browser_actions={totals['total_browser_actions']}  "
        f"text_calls={totals['total_text_calls']}",
        f"  typesafe input tokens={totals['total_typesafe_input_tokens']}",
        f"  text tokens: prompt={totals['total_prompt_tokens']} completion={totals['total_completion_tokens']}",
        "  total cost: "
        + (f"${totals['total_cost_usd']:.6f}" if totals["total_cost_usd"] is not None else "unknown")
        + f"  ({totals['cost_note']})",
        f"  statuses: {totals['statuses']}",
    ]
    if recent:
        lines.append("  recent:")
        for r in recent:
            lines.append(
                f"    {r.get('ts','')[:19]}  {r.get('status'):<8} {r.get('elapsed_ms')}ms  "
                f"{str(r.get('goal'))[:52]}"
            )
    return "\n".join(lines)