"""A live in-page HUD: steps, speed, the current decision, and where it clicks.

Injected over CDP into the tab being driven, so it appears in Chrome itself. The overlay
carries aria-hidden + inert + pointer-events:none, which is what keeps it out of the
snapshotter: snapshot.js skips candidates under [aria-hidden]/[inert] and the text walker
skips text whose parent is not visible(). A HUD update must never break a run, so callers
record failures on the state instead of raising.

Set JEV_HUD=0 to turn it off.
"""

import json
import os

HUD_ID = "__layaHud"
MARK_ID = "__layaMark"
STYLE_ID = "__layaHudStyle"

PULSE = "@keyframes __layaPulse{0%{transform:scale(.35);opacity:.95}100%{transform:scale(2.4);opacity:0}}"

# __DATA__ is replaced with a JSON payload. Kept as a single expression so it can go
# straight through Runtime.evaluate.
_JS = """(() => {
  if (!document.body) return false;
  const d = __DATA__;
  if (!document.getElementById('__STYLE_ID__')) {
    const style = document.createElement('style');
    style.id = '__STYLE_ID__';
    style.textContent = '__PULSE__';
    document.head.appendChild(style);
  }
  let hud = document.getElementById('__HUD_ID__');
  if (!hud) {
    hud = document.createElement('div');
    hud.id = '__HUD_ID__';
    hud.setAttribute('aria-hidden', 'true');
    hud.setAttribute('inert', '');
    hud.style.cssText = 'position:fixed;top:10px;left:10px;z-index:2147483647;'
      + 'pointer-events:none;font:12px/1.5 Consolas,ui-monospace,monospace;'
      + 'background:rgba(10,12,18,.88);color:#e6edf3;padding:9px 11px;border-radius:7px;'
      + 'border:1px solid rgba(88,166,255,.4);box-shadow:0 6px 20px rgba(0,0,0,.5);'
      + 'min-width:290px;letter-spacing:.2px;';
    document.body.appendChild(hud);
  }
  const row = (label, value, color) => {
    const line = document.createElement('div');
    const key = document.createElement('span');
    key.textContent = (label + '       ').slice(0, 7);
    key.style.color = '#7d8590';
    const val = document.createElement('span');
    val.textContent = value;
    if (color) val.style.color = color;
    line.appendChild(key);
    line.appendChild(val);
    return line;
  };
  const head = document.createElement('div');
  head.style.cssText = 'display:flex;justify-content:space-between;align-items:baseline;'
    + 'color:#58a6ff;font-weight:bold;letter-spacing:1.5px;'
    + 'border-bottom:1px solid rgba(88,166,255,.25);margin-bottom:5px;padding-bottom:3px;';
  const title = document.createElement('span');
  title.textContent = 'LAYAHUD';
  const rate = document.createElement('span');
  rate.textContent = d.rate;
  rate.style.cssText = 'color:#3fb950;font-weight:normal;letter-spacing:0;';
  head.append(title, rate);
  hud.replaceChildren(
    head,
    row('step', d.step),
    row('engine', d.engine),
    row('think', d.think, d.thinkColor),
    row('speed', d.speed),
    row('click', d.click, '#ff7b72'),
    row('time', d.time),
    row('status', d.status, d.statusColor),
    row('goal', d.goal)
  );

  let mark = document.getElementById('__MARK_ID__');
  if (!mark) {
    mark = document.createElement('div');
    mark.id = '__MARK_ID__';
    mark.setAttribute('aria-hidden', 'true');
    mark.setAttribute('inert', '');
    mark.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483646;';
    document.body.appendChild(mark);
  }
  mark.replaceChildren();
  let rect = d.rect;
  const node = d.node === null || d.node === undefined ? null : window.__jevFast?.nodes?.get(d.node);
  if (node && node.isConnected) {
    const r = node.getBoundingClientRect();
    rect = {x: r.x, y: r.y, w: r.width, h: r.height};
  }
  if (rect && rect.w > 0 && rect.h > 0 && !d.page_changed) {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;left:' + rect.x + 'px;top:' + rect.y + 'px;width:'
      + rect.w + 'px;height:' + rect.h + 'px;border:2px solid #58a6ff;border-radius:3px;';
    const cx = rect.x + rect.w / 2, cy = rect.y + rect.h / 2;
    const dot = document.createElement('div');
    dot.style.cssText = 'position:fixed;left:' + cx + 'px;top:' + cy + 'px;width:20px;height:20px;'
      + 'margin:-10px 0 0 -10px;border-radius:50%;border:2px solid #ff7b72;'
      + 'animation:__layaPulse 900ms ease-out 2;';
    mark.appendChild(box);
    mark.appendChild(dot);
  }
  return true;
})()"""


def enabled():
    return os.environ.get("JEV_HUD", "1") != "0"


def _confidence_color(confidence):
    if confidence is None:
        return "#7d8590"
    if confidence < 0.2:
        return "#ff7b72"
    if confidence < 0.5:
        return "#d29922"
    return "#3fb950"


def payload(state, decision=None, action=None, page_changed=None):
    """Build the HUD payload from the live agent state."""
    decisions = state.get("decisions") or []
    latencies = [d.get("latency_ms") for d in decisions if d.get("latency_ms") is not None]
    last = latencies[-1] if latencies else None
    average = round(sum(latencies) / len(latencies)) if latencies else None
    engine = (decision or {}).get("model") or (decisions[-1].get("model") if decisions else None)

    think = "waiting for a decision"
    if decision:
        confidence = decision.get("confidence")
        think = f"{decision.get('operation')} {decision.get('target') or ''}".strip()
        think += f"  conf {confidence:.3f}" if isinstance(confidence, float) else ""

    click = "-"
    node, rect = None, None
    if action:
        node = action.get("node")
        rect = action.get("rect")
        label = (action.get("label") or "")[:22]
        click = f"{action.get('id')} \"{label}\""
        if rect:
            click += f" at {round(rect['x'] + rect['w'] / 2)},{round(rect['y'] + rect['h'] / 2)}"

    speed = f"last {last} ms | avg {average} ms" if last is not None else "-"
    rate = f"{round(1000 / average, 1)}/s" if average else "-"

    return {
        "step": f"{len(decisions)} decisions | {len(state.get('history') or [])} actions",
        "engine": engine or "(not chosen yet)",
        "think": think,
        "thinkColor": _confidence_color((decision or {}).get("confidence")),
        "speed": speed,
        "click": click,
        "time": f"{round((state.get('elapsed_ms') or 0) / 1000, 1)} s",
        "status": state.get("status"),
        "statusColor": {"ready": "#3fb950", "predicted": "#d29922"}.get(state.get("status"), "#ff7b72"),
        "goal": (state.get("goal") or "")[:58],
        "rate": rate,
        "node": node,
        "rect": rect,
        # A click that navigated destroyed the element, so its rect now refers to the
        # previous page. Drawing it would put the marker somewhere it never clicked.
        "page_changed": bool(page_changed),
    }


def update(browser, *, state, decision=None, action=None, page_changed=None):
    """Draw or refresh the overlay in the tab being driven."""
    script = (
        _JS.replace("__DATA__", json.dumps(payload(state, decision, action, page_changed)))
        .replace("__HUD_ID__", HUD_ID)
        .replace("__MARK_ID__", MARK_ID)
        .replace("__STYLE_ID__", STYLE_ID)
        .replace("__PULSE__", PULSE)
    )
    result = browser.evaluate(script)
    # The agent drives a background tab so it never steals focus. Watching live therefore
    # needs an explicit opt-in: JEV_HUD_SHOW=1 raises that tab once per update.
    if os.environ.get("JEV_HUD_SHOW") == "1":
        browser.call("Page.bringToFront")
    return result
