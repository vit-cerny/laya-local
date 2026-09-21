"""Local Computer Use with Laya: one goal, text-only UI snapshots, typed decisions, Win32 execution.

Mirrors the browser agent loop (page -> element table -> decision -> execute) but against the
desktop: pywinauto reads the foreground window's UI Automation tree as text, Laya picks the
operation and element locally, and Win32 input executes it. No screenshots, no cloud.

Safety model (Jev-cu pattern):
- Dry-run by default: decide and print, execute nothing. Pass --go to act.
- Destructive or account-affecting targets (delete, send, pay, confirm, install, system
  settings, ...) stop the run unless --allow-sensitive is passed.
- Only the chosen window is touched, and only through its observed elements.

Usage:
    uv run --env-file .env python jev_cu.py --goal "Open the Calendar app and switch to the previous month"
    uv run --env-file .env python jev_cu.py --goal "..." --go --max-steps 15 --window "Notepad"

Programmatic (also used by the opencode MCP tool and the laya-console GUI):
    from jev_cu import run_goal
    result = run_goal("type hello into Notepad", window="Notepad", execute=True, log=print)
"""

import argparse
import hashlib
import json
import time

from jev_config import load_env
from jev_ultrafast.model import choose_laya, field_context, field_text

load_env()

try:
    import pywinauto
    from pywinauto import keyboard
except ImportError:
    pywinauto = None

CLICKABLE = {
    "Button", "ListItem", "MenuItem", "CheckBox", "RadioButton", "TabItem",
    "Hyperlink", "TreeItem", "ComboBox", "Spinner", "SplitButton", "Custom",
}
EDITABLE = {"Edit", "Document"}
SENSITIVE = (
    "delete", "remove", "uninstall", "format", "overwrite", "send", "submit",
    "pay", "purchase", "confirm", "install", "logout", "sign out", "close",
    "exit", "restart", "shutdown", "reset", "wipe",
)
MAX_ELEMENTS = 40
MAX_TREE_WALK = MAX_ELEMENTS * 5


def foreground_window(window_substring=None):
    import ctypes

    from pywinauto import Desktop

    desktop = Desktop(backend="uia")
    if not window_substring:
        return desktop.window(handle=ctypes.windll.user32.GetForegroundWindow())
    fg = desktop.window(handle=ctypes.windll.user32.GetForegroundWindow())
    if window_substring.lower() in fg.window_text().lower():
        return fg
    matches = [w for w in desktop.windows() if window_substring.lower() in w.window_text().lower()]
    if not matches:
        raise SystemExit(f"No window whose title contains {window_substring!r} among {len(desktop.windows())} windows.")
    # Prefer the largest visible match - most likely the window the user is looking at.
    def area(w):
        try:
            r = w.rectangle()
            return r.width() * r.height()
        except Exception:
            return 0

    return max(matches, key=area)


def collect_actions(window):
    """Walk the UIA tree; return the browser-style actions list and the visible text."""
    actions = []
    texts = []
    index = 0
    wrect = window.rectangle()
    tree = window.descendants()[:MAX_TREE_WALK]
    for element in tree:
        if index >= MAX_ELEMENTS * 2:
            break
        try:
            role = element.element_info.control_type
            if role not in CLICKABLE and role not in EDITABLE:
                continue
            label = (element.window_text() or "").strip()
            rect = element.rectangle()
            if not rect.width() or not rect.height():
                continue
            inside = (
                rect.left >= wrect.left - 4
                and rect.top >= wrect.top - 4
                and rect.right <= wrect.right + 4
                and rect.bottom <= wrect.bottom + 4
            )
            overlap = not (
                rect.right <= wrect.left
                or rect.left >= wrect.right
                or rect.bottom <= wrect.top
                or rect.top >= wrect.bottom
            )
            if not (inside or overlap):
                continue
            if not element.is_visible() or not element.is_enabled():
                continue
        except Exception:
            continue
        if label:
            texts.append(f"[{index + 1}] {role}: {label}")
        kind = "fill" if role in EDITABLE else "click"
        value = ""
        if role in EDITABLE:
            try:
                value = element.get_value() or ""
            except Exception:
                value = ""
        actions.append(
            {
                "kind": kind,
                "id": f"e{index + 1}",
                "node": index + 1,
                "role": role,
                "label": label or f"{role} {index + 1}",
                "value": value,
                "current_value": value,
                "element": element,
            }
        )
        index += 1
    if actions:
        actions.append(
            {"kind": "wait", "id": "WAIT", "node": None, "label": "Wait for the UI to settle", "role": ""}
        )
        actions.append(
            {
                "kind": "scroll",
                "id": "SCROLL_DOWN",
                "node": None,
                "label": "Scroll down the window",
                "role": "",
                "delta": -400,
            }
        )
        actions.append(
            {
                "kind": "scroll",
                "id": "SCROLL_UP",
                "node": None,
                "label": "Scroll up the window",
                "role": "",
                "delta": 400,
            }
        )
    return actions, "\n".join(texts)


def state_from(window, actions, text):
    content = json.dumps([(a["kind"], a["label"], a.get("value", "")) for a in actions], ensure_ascii=False)
    return {
        "url": f"app://{window.window_text()}",
        "title": window.window_text(),
        "text": text,
        "actions": actions,
        "fingerprint": hashlib.sha256(content.encode()).hexdigest(),
    }


def apply_decision(window, decision, actions, goal, history, allow_sensitive, dry_run, log=print):
    choice = decision["choice"]
    if choice in {"DONE", "BLOCKED"}:
        return choice
    if choice in {"WAIT", "SCROLL_UP", "SCROLL_DOWN"}:
        if dry_run:
            log(f"  would {choice.lower()}")
            return "dry"
        if choice == "WAIT":
            time.sleep(0.5)
        else:
            delta = next(a["delta"] for a in actions if a["id"] == choice)
            rect = window.rectangle()
            x, y = rect.mid_point().x, rect.mid_point().y
            import ctypes

            ctypes.windll.user32.SetCursorPos(x, y)
            ctypes.windll.user32.mouse_event(0x0800, 0, 0, delta, 0)  # MOUSEEVENTF_WHEEL
        history.append({"action": choice.lower(), "kind": "wait", "text": None, "page_changed": None})
        return "executed"

    action = next(a for a in actions if a["id"] == choice)
    label = action["label"].lower()
    if any(token in label for token in SENSITIVE) and not allow_sensitive:
        log(f"  BLOCKED: {action['label']} matches a sensitive-action token. Pass --allow-sensitive to allow.")
        return "blocked"
    if dry_run:
        log(f"  would {action['kind']} {action['label']!r}")
        return "dry"

    element = action["element"]
    window.set_focus()
    element.set_focus()
    if action["kind"] == "click":
        element.click_input()
    else:
        text, _helper = field_text(
            field_context(goal, action, {"title": window.window_text(), "text": ""}, history)
        )
        element.set_focus()
        keyboard.send_keys("^a")
        element.type_keys(text, with_spaces=True, set_foreground=False)
    history.append(
        {
            "action": action["label"],
            "kind": action["kind"],
            "text": text if action["kind"] == "fill" else None,
            "page_changed": None,
        }
    )
    return "executed"


def run_goal(goal, window=None, execute=False, max_steps=15, allow_sensitive=False, log=print):
    """Run the Laya computer-use loop against one window. Mutates the desktop only if execute."""
    if pywinauto is None:
        raise RuntimeError("pywinauto not installed; run: uv add pywinauto")
    try:
        win = foreground_window(window)
    except SystemExit as exc:
        return {"status": "error", "error": str(exc), "steps": []}
    log(f"window: {win.window_text()!r}")
    mode_note = (
        "execution ON. Cursor will move and keys will be typed."
        if execute
        else "dry-run: decisions only, nothing executed."
    )
    log(mode_note)
    history = []
    steps = []
    last_fingerprints = []
    for step in range(1, max_steps + 1):
        try:
            actions, text = collect_actions(win)
        except Exception as exc:
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "steps": steps}
        if not actions:
            log("no actionable elements found in this window")
            return {"status": "blocked", "reason": "no actionable elements", "steps": steps}
        state = state_from(win, actions, text)
        try:
            decision = choose_laya(state, goal, history)
        except Exception as exc:
            log(f"error: {type(exc).__name__}: {exc}")
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "steps": steps}
        log(f"step {step}: operation={decision['operation']} target={decision['target']} "
            f"conf={decision['confidence']:.2f} ({decision['latency_ms']} ms)")
        log(f"  probs: {json.dumps({k: round(v, 2) for k, v in decision['operation_probabilities'].items()})}")
        try:
            outcome = apply_decision(
                win, decision, actions, goal, history, allow_sensitive, dry_run=not execute, log=log
            )
        except Exception as exc:
            log(f"error: {type(exc).__name__}: {exc} (nothing executed)")
            steps.append({"step": step, "operation": decision["operation"], "target": decision["target"],
                          "choice": decision["choice"], "outcome": "error"})
            return {"status": "error", "error": f"{type(exc).__name__}: {exc}", "steps": steps}
        steps.append(
            {
                "step": step,
                "operation": decision["operation"],
                "target": decision["target"],
                "choice": decision["choice"],
                "confidence": round(decision["confidence"], 3),
                "latency_ms": decision["latency_ms"],
                "outcome": outcome,
            }
        )
        if outcome == "blocked":
            log("stopped by the safety gate")
            return {"status": "blocked", "reason": "safety gate", "steps": steps}
        if outcome == "done":
            log("done: the model judged the goal satisfied.")
            return {"status": "done", "steps": steps}
        if outcome == "dry":
            log(f"would-execute: {decision['choice']}")
            return {"status": "planned", "steps": steps}
        last_fingerprints.append(state["fingerprint"])
        if len(last_fingerprints) >= 3 and len(set(last_fingerprints[-3:])) == 1:
            log("stopped: 3 actions with no visible UI change.")
            return {"status": "blocked", "reason": "no progress", "steps": steps}
        time.sleep(0.3)
    log(f"reached max-steps {max_steps}")
    return {"status": "limit", "reason": f"reached {max_steps} steps", "steps": steps}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--goal", required=True, help="one natural-language goal, in English")
    parser.add_argument("--go", action="store_true", help="actually execute; default is dry-run")
    parser.add_argument("--max-steps", type=int, default=15)
    parser.add_argument("--window", help="window title substring; default: foreground window")
    parser.add_argument("--allow-sensitive", action="store_true")
    args = parser.parse_args()
    result = run_goal(
        args.goal,
        window=args.window,
        execute=args.go,
        max_steps=args.max_steps,
        allow_sensitive=args.allow_sensitive,
    )
    print(json.dumps({"status": result["status"], "reason": result.get("reason"), "steps": result["steps"]},
                     indent=2, ensure_ascii=False))
    return 0 if result["status"] not in {"blocked", "error"} else 2


if __name__ == "__main__":
    raise SystemExit(main())