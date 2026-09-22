"""Route one natural-language task to the cheapest tool that can do it.

Three routes, cheapest first:
  cli     - a URL is opened, a file is downloaded, or a shell command does the job. No clicking.
  browser - a site must be searched or navigated, so the browser agent drives it.
  desktop - a native app must be driven, so the computer-use loop clicks it.

"Download an image from a website" is a cli task: fetching HTML and writing bytes is
deterministic and instant, while clicking through a page is slow and fragile. The router only
falls back to GUI automation when nothing cheaper can do the work.
"""

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from jev_ultrafast.model import llm_json

ROUTER_SYSTEM = """You route ONE Windows task to the cheapest tool that can complete it.
Return JSON with exactly these keys:
{"route": "cli|browser|desktop", "action": "open|download|search|gui", "url": "", "dest": ""}

The words "download", "save", and "fetch" ALWAYS mean route cli, action download. Never route a
download through the browser: fetching bytes over HTTP is instant and reliable, while clicking
through a page is slow and fragile. Use the browser only when a page must be searched or
navigated and no URL in the task points straight at the content.

Examples:
"open example.com" -> {"route":"cli","action":"open","url":"https://example.com","dest":""}
"open web browser and go to https://news.ycombinator.com" -> {"route":"cli","action":"open","url":"https://news.ycombinator.com","dest":""}
"download an image from https://en.wikipedia.org/wiki/Snake" -> {"route":"cli","action":"download","url":"https://en.wikipedia.org/wiki/Snake","dest":""}
"save the picture from https://example.com/gallery to C:\\pics" -> {"route":"cli","action":"download","url":"https://example.com/gallery","dest":"C:\\pics"}
"find me a cheap flight to London" -> {"route":"browser","action":"search","url":"","dest":""}
"search wikipedia for the Godel article" -> {"route":"browser","action":"search","url":"","dest":""}
"open notepad and type hello" -> {"route":"desktop","action":"gui","url":"","dest":""}
"open steam friends list" -> {"route":"cli","action":"open","url":"steam://friends/","dest":""}

Steam panels MUST use Steam's own URL scheme, never desktop/gui: Steam renders through CEF, so
its UI has no accessibility tree and cannot be clicked. The friends list is steam://friends/,
downloads is steam://downloads/, the store is steam://store/.

Set "url" to the exact absolute URL the task names, else "". Never invent a URL.
Set "dest" to a folder path the task names, else "".

Never route a task that needs a password, a payment, or a one-time code - the caller
refuses those before you run, so routing one wastes a call. Never invent a URL, a file
path, or a destination the task did not name. If the task names several steps, route the
ONE step that reaches the goal; the caller re-runs you for the rest."""

IMAGE_CONTENT_PREFIX = "image/"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MIN_DOWNLOAD_BYTES = 100
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jev-ultrafast/0.1.0"

# --- Safety and rate limits -------------------------------------------------
# The computer-use loop already refuses destructive UI *elements* (jev_cu.SENSITIVE),
# but only once it is one click away from them. These catch the intent earlier and cap
# how much the router will do unattended.
BLOCKED_GOAL_TOKENS = (
    "delete", "remove all", "uninstall", "format", "wipe", "erase",
    "shutdown", "restart", "reboot", "transfer money", "send money",
    "make a payment", "purchase", "buy now", "kill process",
)
CREDENTIAL_GOAL_TOKENS = ("password", "passwd", "credit card", "cvv", "2fa", "otp")

DEFAULT_LIMITS = {"maxPerDay": 50, "maxDownloadCandidates": 5, "downloadDelaySeconds": 1.0}
LIMITS_STATE = Path(__file__).resolve().parent.parent / "artifacts" / "task_limits.json"


def unsafe_goal(goal):
    """(kind, token) when the goal itself is destructive or handles a secret.

    Matching is on the goal's own words, so a destructive intent is refused before any
    tool opens - not when the loop is already one click away from the button.
    """
    lowered = f" {str(goal).lower()} "
    for token in BLOCKED_GOAL_TOKENS:
        if token in lowered:
            return "destructive", token
    for token in CREDENTIAL_GOAL_TOKENS:
        if token in lowered:
            return "credential", token
    return None, None


def limits_config(limits=None):
    """DEFAULT_LIMITS with the env override applied, then any explicit dict."""
    cfg = dict(DEFAULT_LIMITS)
    env_cap = os.environ.get("JEV_TASK_MAX_PER_DAY", "")
    if env_cap.isdigit():
        cfg["maxPerDay"] = int(env_cap)
    cfg.update(limits or {})
    return cfg


def _limits_path():
    return Path(os.environ.get("JEV_TASK_LIMITS_STATE", str(LIMITS_STATE)))


def load_limits_state():
    try:
        return json.loads(_limits_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def check_daily_cap(limits=None, now=None):
    """(ok, reason). Refuses once today's task count reaches the cap."""
    cfg = limits_config(limits)
    today = (now or datetime.now()).strftime("%Y-%m-%d")
    used = int(load_limits_state().get("days", {}).get(today, 0))
    if used >= cfg["maxPerDay"]:
        return False, f"daily task cap reached ({used}/{cfg['maxPerDay']})"
    return True, ""


def record_task(now=None):
    """Count this task against today's cap, keeping 30 days of counters."""
    now = now or datetime.now()
    path = _limits_path()
    state = load_limits_state()
    days = state.get("days", {})
    today = now.strftime("%Y-%m-%d")
    days[today] = int(days.get(today, 0)) + 1
    state["days"] = dict(sorted(days.items())[-30:])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return days[today]


def http_get(url, timeout=20):
    """Fetch a URL, refusing anything larger than MAX_DOWNLOAD_BYTES."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"file is {int(length)} bytes, over the {MAX_DOWNLOAD_BYTES} byte cap")
        body = response.read(MAX_DOWNLOAD_BYTES + 1)
        if len(body) > MAX_DOWNLOAD_BYTES:
            raise ValueError(f"body exceeded the {MAX_DOWNLOAD_BYTES} byte cap")
        return body, response.headers.get("Content-Type", "")


def image_urls(page_url):
    """Absolute image URLs found in the page HTML, in document order."""
    body, content_type = http_get(page_url)
    if content_type.startswith(IMAGE_CONTENT_PREFIX):
        return [page_url]
    html = body.decode("utf-8", errors="replace")
    found = []
    for match in re.finditer(r"""<img[^>]+src\s*=\s*["']([^"']+)["']""", html, re.IGNORECASE):
        absolute = urllib.parse.urljoin(page_url, match.group(1))
        if absolute.startswith(("http://", "https://")):
            found.append(absolute)
    return found


def unique_name(dest_dir, url):
    """A filename derived from the URL path, sanitised and never overwriting an existing file."""
    name = os.path.basename(urllib.parse.urlparse(url).path) or "download"
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]
    if not os.path.splitext(name)[1]:
        name += ".jpg"
    stem, suffix = os.path.splitext(name)
    candidate, counter = name, 1
    while (dest_dir / candidate).exists():
        candidate = f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def download(url, dest_dir, log=print):
    """Save an image at url into dest_dir. Returns the saved path, or None if it is not an image."""
    dest_dir = Path(dest_dir).expanduser()
    dest_dir.mkdir(parents=True, exist_ok=True)
    body, content_type = http_get(url)
    if not content_type.startswith(IMAGE_CONTENT_PREFIX) or len(body) < MIN_DOWNLOAD_BYTES:
        return None
    target = dest_dir / unique_name(dest_dir, url)
    target.write_bytes(body)
    log(f"downloaded {url} -> {target} ({len(body)} bytes)")
    return target


def open_in_browser(url, log=print):
    """Open a URL with the shell. Steam panels also need their tray windows restored, because
    Steam renders through CEF (invisible to UI Automation) and stays hidden in the tray."""
    if not urllib.parse.urlparse(url).scheme:
        url = "https://" + url
    os.startfile(url)
    if url.startswith("steam://"):
        restore_steam_windows(log=log)
    log(f"opened {url} in the default browser")
    return url


def restore_steam_windows(min_width=400, min_height=300, log=print):
    """Show Steam's hidden top-level windows. Returns how many were restored."""
    import ctypes
    import time
    from ctypes import wintypes

    import psutil

    user32 = ctypes.windll.user32
    names = {process.pid: (process.info["name"] or "") for process in psutil.process_iter(["name"])}
    restored = []

    def visit(hwnd, _lparam):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if not names.get(owner.value, "").lower().startswith("steam"):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        if rect.right - rect.left < min_width or rect.bottom - rect.top < min_height:
            return True
        if user32.IsWindowVisible(hwnd):
            return True
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        restored.append(hwnd)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(visit), 0)
    if restored:
        time.sleep(1)
        log(f"restored {len(restored)} Steam window(s) from the tray")
    return len(restored)


def default_download_dir():
    return Path(os.environ.get("USERPROFILE", ".")) / "Downloads"


def run_task(goal, dest_dir=None, log=print, execute=True, allow_sensitive=False, limits=None):
    """Classify the goal, run the cheapest route, and verify what actually happened."""
    cfg = limits_config(limits)

    kind, token = unsafe_goal(goal)
    if kind and not allow_sensitive:
        log(f"REFUSED: goal names a {kind} action ('{token}')")
        return {"status": "blocked", "route": None,
                "reason": f"goal names a {kind} action ('{token}'); pass --allow-sensitive to override"}

    ok, reason = check_daily_cap(cfg)
    if not ok:
        log(f"REFUSED: {reason}")
        return {"status": "blocked", "route": None, "reason": reason}
    record_task()

    plan = llm_json(ROUTER_SYSTEM, goal)
    plan = plan if isinstance(plan, dict) else {}
    route = plan.get("route") if plan.get("route") in {"cli", "browser", "desktop"} else "desktop"
    action = plan.get("action") if plan.get("action") in {"open", "download", "search", "gui"} else "gui"
    url = str(plan.get("url") or "")
    dest = dest_dir or plan.get("dest") or str(default_download_dir())
    log(f"route={route} action={action} url={url or '-'} dest={dest}")

    if not execute:
        return {"status": "dry-run", "route": route, "action": action, "url": url, "dest": dest}

    try:
        if route == "cli" and action == "download":
            if not url:
                return {"status": "blocked", "reason": "a download task must name a URL", "route": route}
            saved = download(url, dest, log=log)
            if saved is None:
                # Rate limit: try only the first few images, with a gap between them,
                # so a gallery page cannot turn into a burst of requests.
                for candidate in image_urls(url)[: cfg["maxDownloadCandidates"]]:
                    time.sleep(cfg["downloadDelaySeconds"])
                    saved = download(candidate, dest, log=log)
                    if saved is not None:
                        break
            if saved is None:
                return {"status": "blocked", "reason": f"nothing downloadable at {url}", "route": route}
            return {"status": "done", "route": route, "path": str(saved), "bytes": saved.stat().st_size}

        if route == "cli" and action == "open":
            if not url:
                return {"status": "blocked", "reason": "an open task must name a URL", "route": route}
            open_in_browser(url, log=log)
            return {"status": "done", "route": route, "url": url}
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        return {"status": "blocked", "reason": f"{type(exc).__name__}: {exc}", "route": route}

    if route == "browser":
        from jev_search import run_search

        result = run_search(goal, url or None)
        return {"status": result.get("status"), "route": route,
                "final_url": result.get("final_url"), "engine": result.get("engine")}

    from laya_cu.jev_cu import run_goal

    result = run_goal(goal, window=None, execute=True, max_steps=10, allow_sensitive=allow_sensitive, log=log)
    return {"status": result.get("status"), "route": "desktop", "reason": result.get("reason")}


def main():  # pragma: no cover - thin CLI shim, exercised through laya-cu
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("goal")
    parser.add_argument("--dest", help="download folder; default is ~/Downloads")
    parser.add_argument("--dry-run", action="store_true", help="route the goal but perform no action")
    parser.add_argument("--allow-sensitive", action="store_true", help="override the destructive/credential guard")
    args = parser.parse_args()
    print(json.dumps(
        run_task(args.goal, dest_dir=args.dest, execute=not args.dry_run, allow_sensitive=args.allow_sensitive),
        indent=2, ensure_ascii=False,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
