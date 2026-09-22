"""Route one natural-language task to the cheapest tool that can do it.

Three routes, cheapest first:
  cli     - a URL is opened, a file is downloaded, or a shell command does the job. No clicking.
  browser - a site must be searched or navigated, so the browser agent drives it.
  desktop - a native app must be driven, so the computer-use loop clicks it.

"Download an image from a website" is a cli task: fetching HTML and writing bytes is
deterministic and instant, while clicking through a page is slow and fragile. The router only
falls back to GUI automation when nothing cheaper can do the work.
"""

import os
import re
import urllib.error
import urllib.parse
import urllib.request
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

Set "url" to the exact absolute URL the task names, else "". Never invent a URL.
Set "dest" to a folder path the task names, else ""."""

IMAGE_CONTENT_PREFIX = "image/"
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MIN_DOWNLOAD_BYTES = 100
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) jev-ultrafast/0.1.0"


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
    if not urllib.parse.urlparse(url).scheme:
        url = "https://" + url
    os.startfile(url)
    log(f"opened {url} in the default browser")
    return url


def default_download_dir():
    return Path(os.environ.get("USERPROFILE", ".")) / "Downloads"


def run_task(goal, dest_dir=None, log=print):
    """Classify the goal, run the cheapest route, and verify what actually happened."""
    plan = llm_json(ROUTER_SYSTEM, goal)
    plan = plan if isinstance(plan, dict) else {}
    route = plan.get("route") if plan.get("route") in {"cli", "browser", "desktop"} else "desktop"
    action = plan.get("action") if plan.get("action") in {"open", "download", "search", "gui"} else "gui"
    url = str(plan.get("url") or "")
    dest = dest_dir or plan.get("dest") or str(default_download_dir())
    log(f"route={route} action={action} url={url or '-'} dest={dest}")

    try:
        if route == "cli" and action == "download":
            if not url:
                return {"status": "blocked", "reason": "a download task must name a URL", "route": route}
            saved = download(url, dest, log=log)
            if saved is None:
                for candidate in image_urls(url):
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

    result = run_goal(goal, window=None, execute=True, max_steps=10, log=log)
    return {"status": result.get("status"), "route": "desktop", "reason": result.get("reason")}


def main():  # pragma: no cover - thin CLI shim, exercised through laya-cu
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("goal")
    parser.add_argument("--dest", help="download folder; default is ~/Downloads")
    args = parser.parse_args()
    print(json.dumps(run_task(args.goal, dest_dir=args.dest), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
