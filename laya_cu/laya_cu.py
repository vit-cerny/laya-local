"""Laya computer use from the command line: open any program, then drive it with local decisions.

    laya-cu apps                     list launchable programs
    laya-cu launch notepad           open a program
    laya-cu windows                  list open windows
    laya-cu run "type hello" --app notepad --go
    laya-cu doctor                   check pywinauto, the checkpoint, and free VRAM

Dry-run by default: `run` decides and prints but executes nothing until --go. Destructive or
account-affecting targets (delete, send, pay, install, close, ...) stop the run unless
--allow-sensitive is passed. Only the chosen window is touched, and only through its elements.

Decisions are local, made by the Laya checkpoint. It answers a single yes/no question well but
cannot rank options, so each element and control is scored on its own. On dense windows the scores
compress and the run stops instead of acting on a guess.
"""

import argparse
import json
import os
import sys
from pathlib import Path

from laya_cu.jev_cu import launch_app, list_apps, list_windows, run_goal


def cmd_apps(_args):
    apps = list_apps()
    for name in sorted(apps):
        print(name)
    print(f"{len(apps)} launchable programs", file=sys.stderr)
    return 0


def cmd_launch(args):
    launch_app(args.name)
    return 0


def cmd_windows(_args):
    for title, size in list_windows():
        print(f"{size:>12}  {title}")
    return 0


def cmd_run(args):
    result = run_goal(
        args.goal,
        window=args.window or args.app,
        launch=args.app,
        execute=args.go,
        max_steps=args.max_steps,
        allow_sensitive=args.allow_sensitive,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "reason": result.get("reason"),
                "error": result.get("error"),
                "steps": result["steps"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if result["status"] not in {"blocked", "error"} else 2


def cmd_doctor(_args):
    rows = []
    try:
        import pywinauto

        rows.append(("pywinauto", True, getattr(pywinauto, "__version__", "installed")))
    except ImportError:
        rows.append(("pywinauto", False, "missing: uv add pywinauto"))

    checkpoint = os.environ.get("JEV_LAYA_MODEL") or str(
        Path(__file__).resolve().parent.parent / "models" / "laya-typed-decisions"
    )
    rows.append(("laya checkpoint", Path(checkpoint, "model.safetensors").exists(), checkpoint))
    rows.append(("JEV_DECISION", True, os.environ.get("JEV_DECISION", "laya (code default)")))

    try:
        import torch

        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            rows.append(("gpu", free >= 1500 * 1024 * 1024, f"{free // 1048576} MB free of {total // 1048576} MB"))
        else:
            rows.append(("gpu", False, "no CUDA device; Laya will run on CPU (10-15x slower)"))
    except ImportError:
        rows.append(("gpu", False, "torch not installed"))

    failed = False
    for name, ok, detail in rows:
        failed = failed or not ok
        print(f"[{'ok ' if ok else 'FAIL'}] {name:<16} {detail}")
    return 1 if failed else 0


def main():
    # Window titles and Start Menu names carry characters cp1252 cannot encode, which crashes
    # print() on a default Windows console.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("apps", help="list launchable programs").set_defaults(func=cmd_apps)

    launch = subparsers.add_parser("launch", help="open a program by name")
    launch.add_argument("name")
    launch.set_defaults(func=cmd_launch)

    subparsers.add_parser("windows", help="list open windows").set_defaults(func=cmd_windows)

    run = subparsers.add_parser("run", help="drive one window toward a goal")
    run.add_argument("goal", help="one natural-language goal, in English")
    run.add_argument("--app", help="launch this program first and target it")
    run.add_argument("--window", help="window title substring; default: the launched app or foreground")
    run.add_argument("--go", action="store_true", help="actually execute; default is dry-run")
    run.add_argument("--max-steps", type=int, default=15)
    run.add_argument("--allow-sensitive", action="store_true")
    run.set_defaults(func=cmd_run)

    subparsers.add_parser("doctor", help="check pywinauto, the checkpoint, and free VRAM").set_defaults(func=cmd_doctor)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
