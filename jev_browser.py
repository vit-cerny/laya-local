import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from jev_config import BROWSER_FORKS, CHROMIUM_HINTS, DEBUG_PORT, PROFILE_HOME


def find_browser():
    override = os.environ.get("JEV_CHROME")
    if override:
        return os.path.expandvars(override)
    for candidate in BROWSER_FORKS:
        path = os.path.expandvars(candidate)
        if Path(path).exists():
            return path
    return os.path.expandvars(BROWSER_FORKS[0])


def profile_for(browser, sandbox=None):
    """One profile per browser, because a Chromium fork older than the one that created a
    profile refuses to open it - sharing a directory would break the setup on switch.
    Chrome keeps the original path so an already-consented profile survives.

    sandbox=True (or JEV_SANDBOX=1) moves to a separate profile that never sees the normal
    browsing session. That is profile isolation, not an OS sandbox: wipe it with
    --wipe-sandbox, or run the browser in a container and point JEV_CHROME at it."""
    override = os.environ.get("JEV_CHROME_PROFILE")
    if override:
        return Path(override)
    if sandbox is None:
        sandbox = os.environ.get("JEV_SANDBOX") == "1"
    suffix = "-sandbox" if sandbox else ""
    return PROFILE_HOME / f"jev-{Path(browser).stem.lower()}{suffix}-profile"


def sandbox_profiles():
    return sorted(PROFILE_HOME.glob("jev-*-sandbox-profile"))


# Applied on top of the base flags when JEV_SANDBOX=1: no extensions, no account sync,
# no silent updater, no background telemetry. Profile isolation, not an OS sandbox -
# wipe it with --wipe-sandbox, or run the browser in a container and point JEV_CHROME at it.
SANDBOX_FLAGS = (
    "--disable-extensions",
    "--disable-sync",
    "--disable-component-update",
    "--disable-background-networking",
    "--disable-default-apps",
    "--disable-breakpad",
    "--disable-session-crashed-bubble",
    "--disable-infobars",
    "--no-pings",
    "--disable-features=Translate,MediaRouter,OptimizationHints",
)


def sandboxed():
    return os.environ.get("JEV_SANDBOX") == "1"


def _debug_pid():
    """PID of the process LISTENING on DEBUG_PORT, or None."""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return None
    for line in out.splitlines():
        if f":{DEBUG_PORT}" in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                return int(parts[-1])
    return None


def active_profile():
    """--user-data-dir of the process holding DEBUG_PORT, or None."""
    pid = _debug_pid()
    if not pid:
        return None
    try:
        cmd = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return None
    match = re.search(r"--user-data-dir=(\S+)", cmd)
    return match.group(1) if match else None


def ensure_sandbox_chrome():
    """Restart the debug browser as the hardened sandbox profile: if a non-sandbox Chrome
    already holds DEBUG_PORT, stop just that process first, then launch the sandboxed one."""
    os.environ["JEV_SANDBOX"] = "1"
    holder = _debug_pid()
    if holder:
        active = active_profile()
        if active and "sandbox" not in str(active).lower():
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {holder} -Force"],
                capture_output=True, timeout=10,
            )
            for _ in range(10):
                if _debug_pid() is None:
                    break
                time.sleep(0.5)
    return ensure_chrome()


def debug_alive():
    """Confirm the debug port is served by a Chromium browser, not just any local process
    that happens to answer on it."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json/version", timeout=3) as response:
            info = json.load(response)
    except (OSError, ValueError):
        return False
    browser = str(info.get("Browser", "")).lower()
    return any(hint in browser for hint in CHROMIUM_HINTS)


def ensure_chrome():
    """Chrome 136+ ignores --remote-debugging-port on the default profile, so a
    dedicated profile is required. Unlike the chrome://inspect toggle, it needs no
    manual click and survives restarts, which is what makes unattended calls work."""
    browser = find_browser()
    if debug_alive():
        return browser, "already-running"
    if not Path(browser).exists():
        raise RuntimeError(f"no Chromium-based browser at {browser}; set JEV_CHROME to override")
    profile = profile_for(browser)
    profile.mkdir(parents=True, exist_ok=True)
    flags = [
        browser,
        f"--remote-debugging-port={DEBUG_PORT}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if sandboxed():
        flags.extend(SANDBOX_FLAGS)
    flags.append("about:blank")
    subprocess.Popen(
        flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        time.sleep(1)
        if debug_alive():
            return browser, "launched"
    raise RuntimeError(f"{Path(browser).name} debug endpoint on port {DEBUG_PORT} never came up")