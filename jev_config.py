import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEDGER = ROOT / "artifacts" / "jev_usage.jsonl"
OUTPUT_DIR = ROOT / "artifacts" / "jev_outputs"
DEFAULT_URL = "https://www.google.com/search?q="
DEBUG_PORT = int(os.environ.get("JEV_DEBUG_PORT", "9222"))
PROFILE_HOME = Path(os.environ.get("JEV_CHROME_PROFILE_HOME", Path.home() / ".cache"))

# Any Chromium-based browser works: jev needs a real Blink layout engine for
# getBoundingClientRect/elementFromPoint plus a CDP endpoint. Firefox and the
# lightweight non-rendering engines cannot drive it. JEV_CHROME overrides the search.
BROWSER_FORKS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Thorium\Application\thorium.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"%LOCALAPPDATA%\Chromium\Application\chrome.exe",
    r"C:\Program Files\Chromium\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Vivaldi\Application\vivaldi.exe",
    r"%LOCALAPPDATA%\Programs\Opera\opera.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
)


def load_env(path=ROOT / ".env"):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


ALLOWED_SCHEMES = ("http://", "https://")
CONTENT_CHAR_CAP = 100_000
CHROMIUM_HINTS = ("chrome", "chromium", "thorium", "brave", "edg/", "vivaldi", "opr/")