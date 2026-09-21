"""Laya Terminal: prompt Laya with typed goals - computer use, browser use, typed decisions.

Commands:
  cu <goal>             computer use (executes; safety gate always on)
  plan <goal>           computer use dry-run (plans only, executes nothing)
  web <goal> [url]      browser use via a real Chrome (jev_search)
  ask <text> --preset <triage|email|guard|moderation|router>
  ask <text> --question '<json>'
  win <substring>       target window for cu (default: foreground window)
  voice                 start the offline voice loop (see laya_voice.py)
  status                model, GPU, target window
  help | exit
"""

import argparse
import json
import os
import shlex
import sys

from jev_config import ROOT, load_env

load_env()

_TARGET_WINDOW = [None]


def _print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))


def cmd_status():
    import torch

    print(f"torch {torch.__version__} | cuda {torch.cuda.is_available()} | "
          f"{torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}")
    print(f"JEV_DECISION={os.environ.get('JEV_DECISION', 'typesafe')}  "
          f"JEV_LAYA_MODEL={os.environ.get('JEV_LAYA_MODEL', '(default)')}")
    print(f"target window: {_TARGET_WINDOW[0] or '(foreground)'}")

def cmd_cu(goal, execute):
    from jev_cu import run_goal

    lines = []
    result = run_goal(goal, window=_TARGET_WINDOW[0], execute=execute, max_steps=8, log=lines.append)
    print("\n".join(lines))
    _print_json({"status": result.get("status"), "reason": result.get("reason"),
                 "steps": result.get("steps", [])[-3:]})


def cmd_web(goal, url):
    from jev_search import run_search

    result = run_search(goal, url or None)
    _print_json({k: result.get(k) for k in ("status", "final_url", "title", "content", "visited", "error")})


def cmd_ask(text, preset, question):
    from laya_ask import ask, load_questions

    state = {preset: text} if preset else text
    questions = load_questions(preset) if preset else load_questions(question)
    _print_json(ask(state, questions))


def cmd_voice():
    proc = __import__("subprocess").Popen(
        [sys.executable, str(ROOT / "laya_voice.py")],
        cwd=ROOT,
    )
    proc.wait()


HELP = __doc__


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("-c", "--command", help="run one command and exit")
    args = parser.parse_args()

    if args.command:
        _execute(args.command)
        return 0

    print("Laya Terminal - local computer + browser control. Type 'help' for commands.")
    while True:
        try:
            line = input("laya> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.lower() in {"exit", "quit"}:
            break
        if _execute(line) == "exit":
            break
    return 0


def _execute(line):
    parts = shlex.split(line)
    if not parts:
        return None
    cmd = parts[0].lower()
    rest = " ".join(parts[1:])
    try:
        if cmd in {"help", "?"}:
            print(HELP)
        elif cmd in {"exit", "quit"}:
            return "exit"
        elif cmd == "status":
            cmd_status()
        elif cmd == "win":
            _TARGET_WINDOW[0] = rest or None
            print(f"target window: {_TARGET_WINDOW[0] or '(foreground)'}")
        elif cmd in {"cu", "plan"}:
            if not rest:
                print("usage: cu <goal>")
                return None
            cmd_cu(rest, execute=cmd == "cu")
        elif cmd in {"web", "browser"}:
            goal, _, url = rest.partition(" ")
            if not goal:
                print("usage: web <goal> [url]")
                return None
            cmd_web(goal, url.strip() or None)
        elif cmd == "ask":
            _cmd_ask(rest)
        elif cmd == "voice":
            cmd_voice()
        else:
            print(f"unknown command {cmd!r}; type 'help'")
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}")


def _cmd_ask(rest):
    preset = None
    question = None
    if "--preset" in rest:
        head, _, tail = rest.partition("--preset")
        preset = tail.strip().split()[0]
        text = head.strip()
    elif "--question" in rest:
        head, _, tail = rest.partition("--question")
        question = tail.strip()
        text = head.strip()
    else:
        print("usage: ask <text> --preset <name> | ask <text> --question '<json>'")
        return None
    if not text:
        print("no text to evaluate")
        return None
    cmd_ask(text, preset, question)


if __name__ == "__main__":
    raise SystemExit(main())