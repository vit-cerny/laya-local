"""Laya Control Center: local web dashboard for the Laya engine, voice, browser and goals.

A tiny local page (127.0.0.1:8769) with ON/OFF toggles for the Laya decision engine, the
voice listener, the debug Chrome and the sandbox profile, plus a goal runner (computer use,
browser use, or a direct Laya ask) and an autonomous retry loop. Everything stays on this
machine; computer use is dry-run unless Execute is checked. The sensitive-action gate in
jev_cu is always on - this dashboard never exposes allow_sensitive.

    uv run --env-file .env python laya_dashboard.py   # then open http://127.0.0.1:8769
"""

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import laya_ask
from jev_config import ROOT, load_env

load_env()

PORT = int(os.environ.get("LAYA_DASHBOARD_PORT", "8769"))
DEFAULT_MODEL = str(ROOT / "models" / "laya-typed-decisions")
LOG_CAP = 3000

_LOCK = threading.Lock()
_STATE = {"voice_pid": None, "autonomous_running": False}
_LOG = []  # (id, text) pairs, ids strictly increasing
_NEXT_LOG_ID = [0]
_RUN = {"active": False, "done": True, "result": None}
_AUTO_STOP = threading.Event()
_VOICE_PROC = {"proc": None}
_ENGINE_LOCK = threading.Lock()
_PROFILE_CACHE = {"ts": 0.0, "value": None}


def _log(line):
    with _LOCK:
        _NEXT_LOG_ID[0] += 1
        _LOG.append((_NEXT_LOG_ID[0], line))
        if len(_LOG) > LOG_CAP:
            del _LOG[: len(_LOG) - LOG_CAP]


def _int(payload, key, default):
    try:
        return int(payload.get(key) or default)
    except (TypeError, ValueError):
        return default


def _engine_loaded():
    if laya_ask.loaded():
        return True
    try:
        import jev_ultrafast.model as jm

        return jm.laya_loaded()
    except Exception:
        return False


def _state_json():
    from jev_browser import active_profile, debug_alive

    browser_up = debug_alive()
    profile = None
    if browser_up:
        now = time.time()
        if now - _PROFILE_CACHE["ts"] > 5:
            try:
                _PROFILE_CACHE["value"] = active_profile()
            except Exception:
                _PROFILE_CACHE["value"] = None
            _PROFILE_CACHE["ts"] = now
        profile = _PROFILE_CACHE["value"]
    # The running profile is the truth; the env only states the intent while no browser is up
    # (sandbox Chrome is normally started by scripts/browser-sandbox.ps1, not by this process).
    if browser_up and profile:
        sandbox_active = "sandbox" in str(profile).lower()
    else:
        sandbox_active = os.environ.get("JEV_SANDBOX") == "1"
    with _LOCK:
        _STATE["engine_loaded"] = _engine_loaded()
        _STATE["browser_up"] = browser_up
        _STATE["sandbox_active"] = sandbox_active
        return {
            "engine_loaded": _STATE["engine_loaded"],
            "voice_pid": _STATE["voice_pid"],
            "browser_up": browser_up,
            "sandbox_active": sandbox_active,
            "autonomous_running": _STATE["autonomous_running"],
            "model_path": os.environ.get("JEV_LAYA_MODEL") or DEFAULT_MODEL,
            "device": os.environ.get("JEV_LAYA_DEVICE"),
            "active_profile": profile,
            "last_status": (_RUN["result"] or {}).get("status"),
        }


def _load_model():
    with _ENGINE_LOCK:
        if laya_ask.loaded():
            _log("model already loaded")
            return
        _log("loading local Laya model (first load ~35s)...")
        started = time.perf_counter()
        try:
            laya_ask.agent()
            _log(f"model loaded in {time.perf_counter() - started:.1f}s")
        except Exception as exc:
            _log(f"model load failed: {type(exc).__name__}: {exc}")


def _unload_model():
    import jev_ultrafast.model as jm

    laya_ask.unload()
    jm.unload_laya()
    _log("model unloaded (GPU freed)")


def _master_on():
    """Whole stack ON: load the local model, then start the voice listener."""
    _load_model()
    _voice_on()


def _master_off():
    """Whole stack OFF: stop the voice listener, then free the model."""
    _voice_off()
    _unload_model()


def _voice_on():
    proc = subprocess.Popen(
        [sys.executable, "laya_voice.py"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    with _LOCK:
        _VOICE_PROC["proc"] = proc
        _STATE["voice_pid"] = proc.pid
    _log(f"voice listener started (pid {proc.pid})")
    time.sleep(2)
    if proc.poll() is not None:
        _log(f"voice listener exited early (code {proc.returncode})")
        with _LOCK:
            _VOICE_PROC["proc"] = None
            _STATE["voice_pid"] = None


def _voice_off():
    with _LOCK:
        pid = _STATE["voice_pid"]
        _VOICE_PROC["proc"] = None
        _STATE["voice_pid"] = None
    if pid:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10)
        _log(f"voice listener stopped (pid {pid})")


def _browser_on():
    from jev_browser import ensure_chrome

    _log("starting Chrome on debug port 9222...")
    try:
        browser, state = ensure_chrome()
        _log(f"browser {state}: {browser}")
    except Exception as exc:
        _log(f"browser start failed: {type(exc).__name__}: {exc}")


def _browser_off():
    from jev_browser import _debug_pid

    pid = _debug_pid()
    if not pid:
        _log("no browser process on debug port")
        return
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {pid} -Force"],
        capture_output=True,
        timeout=10,
    )
    for _ in range(10):
        if _debug_pid() is None:
            break
        time.sleep(0.5)
    _log(f"browser stopped (pid {pid})")


def _sandbox_on():
    from jev_browser import ensure_sandbox_chrome

    _log("switching to sandbox profile...")
    try:
        browser, state = ensure_sandbox_chrome()
        _log(f"sandbox browser {state}: {browser}")
    except Exception as exc:
        _log(f"sandbox switch failed: {type(exc).__name__}: {exc}")


def _sandbox_off():
    from jev_browser import _debug_pid, active_profile, ensure_chrome

    os.environ.pop("JEV_SANDBOX", None)
    holder = _debug_pid()
    if holder:
        active = active_profile()
        if active and "sandbox" in str(active).lower():
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", f"Stop-Process -Id {holder} -Force"],
                capture_output=True,
                timeout=10,
            )
            for _ in range(10):
                if _debug_pid() is None:
                    break
                time.sleep(0.5)
    _log("switching to normal profile...")
    try:
        browser, state = ensure_chrome()
        _log(f"browser {state}: {browser}")
    except Exception as exc:
        _log(f"browser start failed: {type(exc).__name__}: {exc}")


def _run_goal(payload):
    goal = (payload.get("goal") or "").strip()
    mode = payload.get("mode", "computer")
    execute = bool(payload.get("execute"))
    max_steps = _int(payload, "max_steps", 8)
    preset = payload.get("preset") or "triage"
    out = {"status": "error", "error": "run did not complete"}
    try:
        _log(f"--- run: mode={mode} execute={execute} max_steps={max_steps} goal={goal!r}")
        if mode == "browser":
            from jev_search import run_search

            result = run_search(goal)
            _log(f"status={result.get('status')} final_url={result.get('final_url')} "
                 f"title={result.get('title')}")
            _log(f"content: {(result.get('content') or '')[:800]}")
            out = {k: result.get(k) for k in ("status", "final_url", "title", "error")}
        elif mode == "ask":
            questions = laya_ask.load_questions(preset)
            result = laya_ask.ask(goal, questions)
            for qid, ans in (result.get("answers") or {}).items():
                _log(f"  {qid}: {ans.get('choice')} (conf {ans.get('confidence', 0):.2f})")
            _log(f"ask done in {result.get('latency_ms')} ms")
            out = {"status": "done", "answers": result.get("answers")}
        else:
            from jev_cu import run_goal as run_cu

            result = run_cu(goal, window=None, execute=execute, max_steps=max_steps, log=_log)
            _log(f"result: status={result.get('status')} reason={result.get('reason')}")
            out = {k: result.get(k) for k in ("status", "reason", "steps")}
    except Exception as exc:
        _log(f"run error: {type(exc).__name__}: {exc}")
        out = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        with _LOCK:
            _RUN["result"] = out
            _RUN["active"] = False
            _RUN["done"] = True


def _autonomous(payload):
    goal = (payload.get("goal") or "").strip()
    mode = payload.get("mode", "computer")
    execute = bool(payload.get("execute"))
    max_attempts = _int(payload, "max_attempts", 3)
    _AUTO_STOP.clear()
    with _LOCK:
        _STATE["autonomous_running"] = True
    _log(f"--- autonomous: mode={mode} execute={execute} max_attempts={max_attempts} goal={goal!r}")
    try:
        for attempt in range(1, max_attempts + 1):
            if _AUTO_STOP.is_set():
                _log("autonomous stopped by user")
                break
            _log(f"attempt {attempt}/{max_attempts}")
            if mode == "browser":
                from jev_search import run_search

                result = run_search(goal)
                _log(f"  status={result.get('status')} final_url={result.get('final_url')}")
                status = result.get("status")
            else:
                from jev_cu import run_goal as run_cu

                result = run_cu(
                    goal, window=None, execute=execute, max_steps=8,
                    log=lambda line: _log("  " + line),
                )
                _log(f"  status={result.get('status')} reason={result.get('reason')}")
                status = result.get("status")
            if status in {"done", "blocked"}:
                _log(f"autonomous finished: {status}")
                break
    except Exception as exc:
        _log(f"autonomous error: {type(exc).__name__}: {exc}")
    finally:
        with _LOCK:
            _STATE["autonomous_running"] = False
            _RUN["result"] = {"status": "stopped" if _AUTO_STOP.is_set() else "finished"}
            _RUN["active"] = False
            _RUN["done"] = True


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Laya Control Center</title>
<style>
body{font-family:system-ui,sans-serif;margin:2rem auto;max-width:880px;background:#111;color:#ddd}
h1{font-size:1.2rem;margin:0 0 4px}
h2{font-size:.95rem;margin:0 0 10px;color:#9cf}
p{font-size:.85rem;color:#999;margin:4px 0 0}
.panel{background:#181818;border:1px solid #333;border-radius:8px;padding:14px;margin:14px 0}
.toggle{display:inline-flex;align-items:center;gap:8px;margin:0 14px 10px 0;padding:8px 14px;
  border-radius:6px;border:1px solid #444;background:#222;color:#ddd;cursor:pointer;font-weight:600}
.toggle.on{background:#1d4d2b;border-color:#2a6;color:#8f8}
.toggle.off{background:#4d1d1d;border-color:#a22;color:#f88}
.dot{display:inline-block;width:9px;height:9px;border-radius:50%;background:#555}
.dot.on{background:#2a6}
.dot.off{background:#a22}
textarea{width:100%;min-height:64px;background:#1c1c1c;color:#eee;border:1px solid #444;
  border-radius:6px;padding:8px;box-sizing:border-box}
input[type=number]{width:70px;background:#1c1c1c;color:#eee;border:1px solid #444;border-radius:4px;padding:4px}
select{background:#1c1c1c;color:#eee;border:1px solid #444;border-radius:4px;padding:4px}
button.run{padding:8px 18px;border-radius:6px;border:0;background:#2a6;color:#fff;cursor:pointer;font-weight:600}
button.run:disabled{background:#555}
label{margin-right:14px}
#log{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;background:#0a0a0a;
  border:1px solid #333;border-radius:6px;padding:10px;min-height:140px;max-height:360px;
  overflow:auto;margin-top:12px}
.bar{display:flex;gap:12px;align-items:center;margin:10px 0;flex-wrap:wrap}
.meta{font-size:11px;color:#888;margin-top:8px}
</style></head><body>
<h1>Laya Control Center</h1>
<p>Local dashboard: Laya engine, voice, debug Chrome, sandbox profile and goal runner.
Loopback only. Computer use is dry-run unless Execute is checked.</p>

<div class="panel"><h2>Engine &amp; Services</h2>
<button class="toggle" id="master" data-name="Laya" onclick="toggle('master')"></button>
<button class="toggle" id="engine" data-name="Engine" onclick="toggle('engine')"></button>
<button class="toggle" id="voice" data-name="Voice" onclick="toggle('voice')"></button>
<button class="toggle" id="browser" data-name="Browser" onclick="toggle('browser')"></button>
<button class="toggle" id="sandbox" data-name="Sandbox" onclick="toggle('sandbox')"></button>
<div class="meta" id="meta"></div>
</div>

<div class="panel"><h2>Goal Runner</h2>
<textarea id="goal" placeholder="e.g. Open Notepad and type hello world"></textarea>
<div class="bar">
<label><input type="radio" name="mode" value="computer" checked> Computer</label>
<label><input type="radio" name="mode" value="browser"> Browser</label>
<label><input type="radio" name="mode" value="ask"> Ask Laya</label>
<label>Preset <select id="preset">
<option value="triage">triage</option><option value="email">email</option>
<option value="guard">guard</option><option value="moderation">moderation</option>
<option value="router">router</option></select></label>
<label><input type="checkbox" id="execute"> Execute (not dry-run)</label>
<label>Max steps <input type="number" id="maxsteps" value="8" min="1" max="50"></label>
<button class="run" id="run" onclick="runGoal()">Run</button>
</div>
<div id="log">Ready.</div>
</div>

<div class="panel"><h2>Autonomous</h2>
<textarea id="agoal" placeholder="Goal to retry until done or blocked"></textarea>
<div class="bar">
<label><input type="radio" name="amode" value="computer" checked> Computer</label>
<label><input type="radio" name="amode" value="browser"> Browser</label>
<label><input type="checkbox" id="aexecute"> Execute</label>
<label>Max attempts <input type="number" id="attempts" value="3" min="1" max="20"></label>
<button class="run" id="astart" onclick="autoStart()">Start</button>
<button class="run" id="astop" onclick="autoStop()" disabled>Stop</button>
</div>
<div id="alog">(shared log)</div>
</div>

<script>
let since = 0, busy = false;
async function post(body) {
  const r = await fetch('/action', {method:'POST', body: JSON.stringify(body)});
  return r.json();
}
function setToggle(id, on) {
  const el = document.getElementById(id);
  el.classList.toggle('on', on);
  el.classList.toggle('off', !on);
  el.innerHTML = '<span class="dot ' + (on ? 'on' : 'off') + '"></span> ' +
    el.dataset.name + ' ' + (on ? 'ON' : 'OFF');
}
async function toggle(id) {
  const s = await (await fetch('/state')).json();
  const map = {
    master: s.engine_loaded ? 'master_off' : 'master_on',
    engine: s.engine_loaded ? 'unload_model' : 'load_model',
    voice: s.voice_pid ? 'voice_off' : 'voice_on',
    browser: s.browser_up ? 'browser_off' : 'browser_on',
    sandbox: s.sandbox_active ? 'sandbox_off' : 'sandbox_on'
  };
  await post({action: map[id]});
}
async function refresh() {
  const s = await (await fetch('/state')).json();
  setToggle('master', s.engine_loaded && !!s.voice_pid);
  setToggle('engine', s.engine_loaded);
  setToggle('voice', !!s.voice_pid);
  setToggle('browser', s.browser_up);
  setToggle('sandbox', s.sandbox_active);
  document.getElementById('meta').textContent =
    'model: ' + s.model_path + ' | device: ' + (s.device || 'auto') +
    ' | profile: ' + (s.active_profile || '-') + ' | last: ' + (s.last_status || '-');
  document.getElementById('astart').disabled = s.autonomous_running;
  document.getElementById('astop').disabled = !s.autonomous_running;
  document.getElementById('run').disabled = busy;
}
async function runGoal() {
  const body = {action:'run', goal: document.getElementById('goal').value,
    mode: document.querySelector('input[name=mode]:checked').value,
    execute: document.getElementById('execute').checked,
    max_steps: parseInt(document.getElementById('maxsteps').value) || 8,
    preset: document.getElementById('preset').value};
  const j = await post(body);
  if (j.error) { alert(j.error); return; }
  await resetLog();
}
async function autoStart() {
  const body = {action:'autonomous_on', goal: document.getElementById('agoal').value,
    mode: document.querySelector('input[name=amode]:checked').value,
    execute: document.getElementById('aexecute').checked,
    max_attempts: parseInt(document.getElementById('attempts').value) || 3};
  const j = await post(body);
  if (j.error) { alert(j.error); return; }
  await resetLog();
}
async function autoStop() { await post({action:'autonomous_off'}); }
async function resetLog() {
  const j = await (await fetch('/log?since=0')).json();
  since = j.next;
  document.getElementById('log').textContent = '';
  document.getElementById('alog').textContent = '';
  busy = true;
  document.getElementById('run').disabled = true;
  poll();
}
async function poll() {
  const r = await fetch('/log?since=' + since);
  const j = await r.json();
  if (j.lines && j.lines.length) {
    since = j.next;
    const text = j.lines.join('\\n') + '\\n';
    const el = document.getElementById('log');
    el.textContent += text; el.scrollTop = el.scrollHeight;
    const al = document.getElementById('alog');
    al.textContent += text; al.scrollTop = al.scrollHeight;
  }
  if (j.done && busy) {
    busy = false;
    document.getElementById('run').disabled = false;
    if (j.result) {
      document.getElementById('log').textContent +=
        '\\n[result] ' + JSON.stringify(j.result) + '\\n';
    }
  }
  setTimeout(poll, 400);
}
setInterval(refresh, 1000);
refresh();
poll();
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
        path = self.path.split("?")[0]
        if path == "/state":
            return self._json(_state_json())
        if path == "/log":
            q = parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            since = int(q.get("since", ["0"])[0])
            with _LOCK:
                lines = [text for lid, text in _LOG if lid > since]
                next_id = _NEXT_LOG_ID[0]
                done = _RUN["done"]
                result = _RUN["result"] if done else None
            return self._json({"lines": lines, "next": next_id, "done": done, "result": result})
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if not self._local_origin():
            return self._json({"error": "forbidden host"}, 403)
        if self.path != "/action":
            return self._json({"error": "not found"}, 404)
        try:
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        except (ValueError, KeyError):
            return self._json({"error": "bad json"}, 400)
        action = payload.get("action")
        if action in {"run", "autonomous_on"}:
            if not (payload.get("goal") or "").strip():
                return self._json({"error": "empty goal"})
            with _LOCK:
                busy = _RUN["active"] or _STATE["autonomous_running"]
            if busy:
                return self._json({"error": "a run is already active"})
            with _LOCK:
                _RUN["active"] = True
                _RUN["done"] = False
                _RUN["result"] = None
            target = _run_goal if action == "run" else _autonomous
            threading.Thread(target=target, args=(payload,), daemon=True).start()
            return self._json({"ok": True})
        if action == "autonomous_off":
            _AUTO_STOP.set()
            return self._json({"ok": True})
        handlers = {
            "master_on": _master_on,
            "master_off": _master_off,
            "load_model": _load_model,
            "unload_model": _unload_model,
            "voice_on": _voice_on,
            "voice_off": _voice_off,
            "browser_on": _browser_on,
            "browser_off": _browser_off,
            "sandbox_on": _sandbox_on,
            "sandbox_off": _sandbox_off,
        }
        fn = handlers.get(action)
        if fn is None:
            return self._json({"error": f"unknown action {action!r}"}, 400)
        threading.Thread(target=fn, daemon=True).start()
        return self._json({"ok": True})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Laya Control Center: http://127.0.0.1:{PORT}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()


if __name__ == "__main__":
    main()