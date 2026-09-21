"""Ask local Laya any typed question over any text or JSON state. Fully offline, ~100 ms/answer.

Laya is a local System 1 decision engine (Apache-2.0, https://github.com/NandhaKishorM/laya).
Use it for routing, triage, moderation, guardrails, classification and any yes/no or ordinal
judgement, without sending text to the cloud.

Usage:
    uv run python laya_ask.py "My payment failed twice, fix it now" --preset triage
    uv run python laya_ask.py "ticket text" --question '{"type":"choice",
        "criteria":{"billing":"invoices, refunds","technical":"bugs"},"instructions":"Pick"}'
    uv run python laya_ask.py --state-file state.json --question '...' --question '...'
    uv run python laya_ask.py "prompt text" --preset guard

Output is JSON: one answer per question with probabilities and calibrated confidence.
"""

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from jev_config import ROOT, load_env

load_env()

PRESET_STATE_KEYS = {
    "triage": "message",
    "email": "body",
    "guard": "prompt",
    "moderation": "post",
    "router": "request",
}

_AGENT = None
_AGENT_LOCK = threading.Lock()


def _vram_guard(min_free_mb=1500):
    """Fail with an actionable error instead of a CUDA access violation when the GPU is full.

    Loading the model into an exhausted device segfaults natively (0xC0000005), which looks
    like a crash in this code. Checking first turns it into a message the caller can act on.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return
        free, _total = torch.cuda.mem_get_info()
        if free < min_free_mb * 1024 * 1024:
            raise RuntimeError(
                f"only {free // (1024 * 1024)} MB of VRAM free, Laya needs about {min_free_mb} MB. "
                "Close other GPU users (a stray voice listener or dashboard process is the usual cause) and retry."
            )
    except ImportError:
        return


def agent():
    global _AGENT
    if _AGENT is None:
        with _AGENT_LOCK:
            if _AGENT is None:
                _vram_guard()
                from laya import Agent

                default_path = str(ROOT / "models" / "laya-typed-decisions")
                _AGENT = Agent(
                    os.environ.get("JEV_LAYA_MODEL") or default_path,
                    device=os.environ.get("JEV_LAYA_DEVICE") or None,
                )
    return _AGENT


def loaded():
    return _AGENT is not None


def unload():
    global _AGENT
    with _AGENT_LOCK:
        _AGENT = None
    # Dropping the reference is not enough: torch's caching allocator keeps the VRAM
    # reserved until the cache is emptied, so the dashboard's OFF would not free the GPU.
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def ask(state, questions):
    """state: str or dict. questions: dict of question_id -> question def. Returns result dict."""
    started = time.perf_counter()
    result = agent().predict(state, questions)
    result["latency_ms"] = round((time.perf_counter() - started) * 1000)
    return result


def load_questions(spec):
    if spec.startswith("{"):
        return json.loads(spec)
    if spec in PRESET_STATE_KEYS:
        from laya.presets import (
            email_questions,
            guard_questions,
            moderation_questions,
            router_questions,
            triage_questions,
        )

        return {
            "triage": triage_questions,
            "email": email_questions,
            "guard": guard_questions,
            "moderation": moderation_questions,
            "router": router_questions,
        }[spec]()
    raise SystemExit(f"unknown preset {spec!r}; pass JSON or one of: {', '.join(PRESET_STATE_KEYS)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("state_text", nargs="?", help="the state to evaluate (text)")
    parser.add_argument("--state-file", help="read state from a JSON or text file instead")
    parser.add_argument("--question", action="append", default=[], help="question JSON, or a preset name (repeatable)")
    parser.add_argument("--preset", help="preset: triage | email | guard | moderation | router")
    parser.add_argument("--model", help="local model dir override")
    args = parser.parse_args()

    if args.model:
        os.environ["JEV_LAYA_MODEL"] = args.model

    if args.state_text and args.state_file:
        raise SystemExit("give state either as text or --state-file, not both")
    if args.state_file:
        raw = Path(args.state_file).read_text(encoding="utf-8")
        try:
            state = json.loads(raw)
        except ValueError:
            state = raw
    elif args.state_text:
        state = args.state_text
    else:
        state = sys.stdin.read().strip() if not sys.stdin.isatty() else None
        if not state:
            raise SystemExit("no state given: pass text, --state-file, or pipe stdin")

    specs = list(args.question) + ([args.preset] if args.preset else [])
    if not specs:
        raise SystemExit("no question: pass --question '<json>' or --preset <name>")
    questions = {}
    for spec in specs:
        loaded = load_questions(spec)
        if not isinstance(loaded, dict):
            raise SystemExit(f"questions must be a dict of question_id -> definition, got {type(loaded).__name__}")
        questions.update(loaded)

    if args.preset and isinstance(state, str):
        state = {PRESET_STATE_KEYS[args.preset]: state}
    elif args.preset and isinstance(state, dict):
        key = PRESET_STATE_KEYS[args.preset]
        state.setdefault(key, "")

    result = ask(state, questions)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())