"""Laya Console: manual prompt GUI for local computer use and browser use.

A tiny local web page (127.0.0.1:8768) where you type a goal, choose computer-use
(desktop) or browser-use (web), and watch the Laya-driven run live. This is the
"type somewhere manually" way to prompt Laya - no LLM harness required.

    uv run --env-file .env python laya_console.py   # then open http://127.0.0.1:8768
"""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

from jev_config import load_env

load_env()

PORT = int(os.environ.get("LAYA_CONSOLE_PORT", "8768"))
RUNS = []
_LOCK = threading.Lock()
_NEXT_ID = [0]

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Laya Console</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:760px;background:#111;color:#ddd}
h1{font-size:1.2rem}
textarea{width:100%;min-height:70px;background:#1c1c1c;color:#eee;border:1px solid #444;
  border-radius:6px;padding:8px}
button{padding:8px 18px;border-radius:6px;border:0;background:#2a6;color:#fff;cursor:pointer;font-weight:600}
button:disabled{background:#555}
label{margin-right:14px}
#log{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;background:#0a0a0a;
  border:1px solid #333;border-radius:6px;padding:10px;min-height:160px;max-height:420px;
  overflow:auto;margin-top:12px}
.bar{display:flex;gap:12px;align-items:center;margin:10px 0}
</style></head><body>
<h1>Laya Console - local computer use / browser use</h1>
<p>Everything runs offline on this machine (Laya decision engine). Computer use drives the
desktop; browser use drives a real Chrome via the jev agent. CEF apps (Steam, games) expose
no UI tree - use browser use for those.</p>
<textarea id="goal" placeholder="e.g. Open Notepad and type hello world"></textarea>
<div class="bar">
  <label><input type="radio" name="mode" value="computer" checked> Computer use</label>
  <label><input type="radio" name="mode" value="browser"> Browser use</label>
  <label><input type="checkbox" id="execute"> Execute (not dry-run)</label>
  <button id="run" onclick="go()">Run</button>
</div>
<div id="log">Ready.</div>
<script>
let since = 0, runId = null;
async function go() {
  const body = {goal: document.getElementById('goal').value,
    mode: document.querySelector('input[name=mode]:checked').value,
    execute: document.getElementById('execute').checked};
  const r = await fetch('/run', {method:'POST', body: JSON.stringify(body)});
  const j = await r.json();
  runId = j.run_id; since = 0;
  document.getElementById('log').textContent = '';
  document.getElementById('run').disabled = true;
  poll();
}
async function poll() {
  if (runId === null) return;
  const r = await fetch('/log?run_id=' + runId + '&since=' + since);
  const j = await r.json();
  if (j.lines) { since = j.next; const el = document.getElementById('log');
    el.textContent += j.lines.join('\\n') + '\\n'; el.scrollTop = el.scrollHeight; }
  if (j.done) { document.getElementById('run').disabled = false; runId = null;
    document.getElementById('log').textContent += '\\n[result] ' + JSON.stringify(j.result) + '\\n'; }
  else setTimeout(poll, 400);
}
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _local_origin(self):
        return self.headers.get("Host") == f"127.0.0.1:{PORT}"

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._local_origin():
            return self._json({"error": "forbidden host"}, 403)
        if self.path.split("?")[0] == "/log":
            q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            run_id = int(q.get("run_id", ["-1"])[0])
            since = int(q.get("since", ["0"])[0])
            with _LOCK:
                run = next((r for r in RUNS if r["id"] == run_id), None)
            if run is None:
                return self._json({"error": "unknown run"})
            with run["lock"]:
                lines = run["lines"][since:]
                done = run["done"]
                result = run["result"] if done else None
            return self._json({"lines": lines, "next": since + len(lines), "done": done, "result": result})
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._local_origin():
            return self._json({"error": "forbidden host"}, 403)
        if self.path != "/run":
            return self._json({"error": "not found"}, 404)
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, KeyError):
            return self._json({"error": "bad json"}, 400)
        goal = (payload.get("goal") or "").strip()
        if not goal:
            return self._json({"error": "empty goal"})
        with _LOCK:
            _NEXT_ID[0] += 1
            run = {"id": _NEXT_ID[0], "lines": [], "done": False, "result": None, "lock": threading.Lock()}
            RUNS.append(run)
        thread = threading.Thread(target=_work, args=(run, goal, payload), daemon=True)
        thread.start()
        return self._json({"run_id": run["id"]})


def _work(run, goal, payload):
    def log(line):
        with run["lock"]:
            run["lines"].append(line)

    mode = payload.get("mode", "computer")
    try:
        if mode == "browser":
            from jev_search import run_search

            log(f"browser use: {goal}")
            result = run_search(goal, include_log=False)
            log(f"status={result.get('status')} final_url={result.get('final_url')}")
            log(f"content: {(result.get('content') or '')[:1500]}")
            out = {k: result.get(k) for k in ("status", "final_url", "title", "content", "visited", "error")}
        else:
            from jev_cu import run_goal

            execute = bool(payload.get("execute"))
            log(f"computer use ({'execute' if execute else 'dry-run'}): {goal}")
            result = run_goal(goal, window=None, execute=execute, max_steps=8, log=log)
            out = {k: result.get(k) for k in ("status", "reason", "steps")}
    except Exception as exc:
        out = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    with run["lock"]:
        run["result"] = out
        run["done"] = True


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Laya Console: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()