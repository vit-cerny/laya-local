"""Run browser use on the local Laya decision engine, then report how it actually went.

JEV_DECISION=laya keeps operation/target choices on the local checkpoint, so no TypeSafe
key is needed. A small text model still supplies TYPE_TEXT values. Every run is recorded
in the usage ledger and its full output lands under artifacts/jev_outputs/.

Usage:
    uv run python laya_browse.py "<goal>" --url <url>
    uv run python laya_browse.py "<goal>" --url <url> --runs 3
    uv run python laya_browse.py "<goal>" --json
"""

import argparse
import json
import statistics

from jev_search import run_search


def latency_stats(values):
    if not values:
        return None
    return {"n": len(values), "min": min(values), "median": statistics.median(values), "max": max(values)}


def summarize(result):
    usage = result.get("usage") or {}
    return {
        "engine": result.get("engine"),
        "status": result.get("status"),
        "error": result.get("error"),
        "elapsed_ms": result.get("elapsed_ms"),
        "actions": usage.get("browser_actions"),
        "decisions": usage.get("decisions"),
        "text_calls": usage.get("text_calls"),
        "final_url": result.get("final_url"),
        "output_file": result.get("output_file"),
        "decision_latency_ms": [ms for ms in (result.get("decision_latencies_ms") or []) if ms is not None],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("goal", help="one natural-language goal")
    parser.add_argument("--url", help="start URL; omit to begin with a Google search of the goal")
    parser.add_argument("--runs", type=int, default=1, help="repeat the run; run 1 pays the model load")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the line summary")
    args = parser.parse_args()
    if args.runs < 1:
        raise SystemExit("--runs must be at least 1")

    runs = []
    for index in range(args.runs):
        row = summarize(run_search(args.goal, args.url))
        row["run"] = index + 1
        row["model_load_included"] = index == 0
        runs.append(row)
        if not args.json:
            stats = latency_stats(row["decision_latency_ms"]) or {}
            print(
                f"run {row['run']}: engine={row['engine']} status={row['status']} "
                f"elapsed={row['elapsed_ms']}ms actions={row['actions']} decisions={row['decisions']} "
                f"text_calls={row['text_calls']} decision_median={stats.get('median', '-')}ms",
                flush=True,
            )
            if row["error"]:
                print(f"        stopped: {row['error']}", flush=True)
            print(f"        final: {row['final_url']}", flush=True)

    # Run 1 loaded the checkpoint, so it is not comparable to the rest.
    measured = runs[1:] or runs
    payload = {
        "goal": args.goal,
        "url": args.url,
        "engine": runs[0]["engine"],
        "model_load_excluded": bool(runs[1:]),
        "runs": runs,
        "elapsed_ms_median": statistics.median(r["elapsed_ms"] for r in measured),
        "decision_latency_ms": latency_stats([ms for r in measured for ms in r["decision_latency_ms"]]),
        "statuses": [r["status"] for r in runs],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    window = "steady state" if payload["model_load_excluded"] else "includes model load"
    print(
        f"\nengine {payload['engine']} | median elapsed {payload['elapsed_ms_median']:.0f} ms "
        f"over {len(measured)} run(s) ({window})"
    )
    decisions = payload["decision_latency_ms"]
    if decisions:
        print(
            f"decision latency: median {decisions['median']:.0f} ms, min {decisions['min']} ms, "
            f"max {decisions['max']} ms over {decisions['n']} calls"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
