"""A decision engine makes choices; an optional small OpenAI-compatible model writes field values.

JEV_DECISION=laya (the default) routes operation/target choices through the local Laya System 1
engine (https://github.com/NandhaKishorM/laya). Set JEV_DECISION=typesafe to use the TypeSafe API
instead; that path needs TYPESAFE_API_KEY. The text helper is unchanged either way.
"""

import json
import math
import os
import threading
import time
from pathlib import Path

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)
_LAYA_AGENT = None
_LAYA_LOCK = threading.Lock()


def post_json(url, key, body, extra_headers=None):
    for attempt in range(3):
        try:
            response = CLIENT.post(
                url, json=body, headers={"Authorization": f"Bearer {key}", **(extra_headers or {})}
            )
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def _laya_max_elements():
    # Per-element scoring costs about 305 tokens (~22 ms) per element, because build_sequence
    # embeds the whole state in every question. Measured warm on a dense page: cap 4 = 129 ms,
    # 12 = 340 ms, 24 = 589 ms, against a 434-536 ms baseline for the old single-choice path.
    return int(os.environ.get("JEV_LAYA_MAX_ELEMENTS", "12"))


def _laya_max_text():
    return int(os.environ.get("JEV_LAYA_MAX_TEXT", "600"))


def _cap_action_space(elements, targets, max_elements):
    """Trim the indexed action space so Laya's small head budget can resolve each option.

    Laya splits head_max_len (192-256 tokens) across the target options, so 40 elements leave
    about 6 tokens each and the choice degrades to noise; fewer options means more tokens each.
    """
    if not max_elements or len(elements) <= max_elements:
        return elements, targets
    keep = {element["index"] for element in elements[:max_elements]}
    capped = {
        operation: {key: action for key, action in candidates.items() if key.split(":")[0] in keep}
        for operation, candidates in targets.items()
    }
    return elements[:max_elements], capped


def _operation_labels():
    return {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }


def _operation_set(targets, controls):
    """Every key the operation question offers, and the keys _decide accepts."""
    operations = {key: _operation_labels()[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    return operations


def _rich_questions(targets, controls, goal):
    operations = _operation_set(targets, controls)
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    return operations, questions


def _laya_min_score():
    return float(os.environ.get("JEV_LAYA_MIN_SCORE", "0.5"))


def _laya_candidates(elements, controls, goal):
    """Everything the agent may pick next: one entry per element, plus every control.

    Controls (WAIT, SCROLL_UP, SCROLL_DOWN) are scored the same way as elements. Scoring only
    elements leaves no way to wait or to reach content that is below the fold.
    """
    trimmed = goal[:200]
    candidates = [
        {
            "key": element["index"],
            "question": f"Is element [{element['index']}] the control to use next to reach the goal: {trimmed}?",
        }
        for element in elements
    ]
    candidates += [
        {
            "key": key,
            "question": f"Should the agent '{control['label']}' next to reach the goal: {trimmed}?",
        }
        for key, control in controls.items()
    ]
    return candidates


def _laya_questions(candidates, goal):
    """One yes/no question per candidate, plus a completion question.

    Laya answers a single content yes/no question correctly (measured 4/4 on element matching)
    but cannot rank an N-way choice at all (1/5, and it returns the first key whatever the
    option order), so each candidate is scored on its own and the argmax wins.
    """
    questions = {
        f"c_{candidate['key']}": {
            "type": "noul",
            "instructions": candidate["question"],
            "criteria": {"false": "no", "true": "yes"},
        }
        for candidate in candidates
    }
    questions["complete"] = {
        "type": "noul",
        "instructions": f"Does the page already satisfy the goal: {goal[:200]}?",
        "criteria": {"false": "no", "true": "yes"},
    }
    return questions


def _laya_decision(candidates, targets, controls, result, started, body):
    """Turn per-candidate scores into the decision dict the loop consumes."""
    answers = result["answers"]
    scores = {
        candidate["key"]: float((answers.get(f"c_{candidate['key']}") or {}).get("noul", 0.0))
        for candidate in candidates
    }
    best = max(scores, key=scores.get) if scores else None
    best_score = scores.get(best, 0.0)
    complete = float((answers.get("complete") or {}).get("noul", 0.0))

    operation = target_key = action = None
    if complete >= _laya_min_score():
        operation = "DONE"
    elif best is not None and best_score >= _laya_min_score():
        if best in controls:
            operation, action = best, controls[best]
        else:
            # The chosen element's own kind decides the operation; typing is the more specific one.
            for candidate in ("TYPE_TEXT", "SELECT", "CLICK"):
                for key, candidate_action in (targets.get(candidate) or {}).items():
                    if key.split(":")[0] == best:
                        operation, target_key, action = candidate, key, candidate_action
                        break
                if operation:
                    break
    if operation is None:
        operation = "BLOCKED"

    probabilities = {}
    if action is not None and operation in controls:
        probabilities = {action["id"]: best_score}
    elif action is not None:
        probabilities = {
            candidate_action["id"]: scores.get(key.split(":")[0], 0.0)
            for key, candidate_action in (targets.get(operation) or {}).items()
        }
    return {
        "choice": action["id"] if action else operation,
        "operation": operation,
        "target": target_key,
        "confidence": complete if operation == "DONE" else best_score,
        "probabilities": probabilities,
        "operation_probabilities": scores,
        "target_probabilities": {},
        "target_confidence": best_score,
        "raw_answers": answers,
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def _build_questions(state, goal, history):
    """The operation/target question set the TypeSafe provider consumes."""
    elements, targets, controls = action_space(state["actions"])
    operations, questions = _rich_questions(targets, controls, goal)
    return elements, targets, controls, operations, questions


def _decide(elements, targets, controls, operations, questions, body, result, started):
    """Turn a provider result (TypeSafe or Laya shape) into the decision dict the loop consumes."""
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


# ponytail: deliberately duplicated in laya_ask.py. Importing it from here would execute this
# package's __init__, pulling agent/browser/browser_harness into the decision-only CLIs.
def vram_guard(min_free_mb=1500):
    """Fail with an actionable error instead of a CUDA access violation when the GPU is full.

    Loading the model into an exhausted device segfaults natively (0xC0000005), which looks
    like a crash in this code. Checking first turns it into a message the caller can act on.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return
        free, _total = torch.cuda.mem_get_info()
        if free < min_free_mb * 1024 * 1024:
            raise RuntimeError(
                f"only {free // (1024 * 1024)} MB of VRAM free, Laya needs about {min_free_mb} MB. "
                "Close other GPU users (a stray voice listener or dashboard process is the usual cause) and retry."
            )
    except ImportError:
        return


def _laya_call(body):
    """One local Laya forward pass; lazy singleton so the torch import stays out of TypeSafe runs."""
    global _LAYA_AGENT
    if _LAYA_AGENT is None:
        with _LAYA_LOCK:
            if _LAYA_AGENT is None:
                vram_guard()
                from laya import Agent

                default_path = str(Path(__file__).resolve().parent.parent / "models" / "laya-typed-decisions")
                _LAYA_AGENT = Agent(
                    os.environ.get("JEV_LAYA_MODEL") or default_path,
                    device=os.environ.get("JEV_LAYA_DEVICE") or None,
                )
    result = _LAYA_AGENT.predict(body["state"], body["questions"])
    result["model"] = "laya/" + str(_LAYA_AGENT.cfg.get("model_name", "rl-agent"))
    return result


def choose_typesafe(state, goal, history):
    elements, targets, controls, operations, questions = _build_questions(state, goal, history)
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    return _decide(elements, targets, controls, operations, questions, body, result, started)


def choose_laya(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    elements, targets = _cap_action_space(elements, targets, _laya_max_elements())
    candidates = _laya_candidates(elements, controls, goal)
    body = {
        "state": {
            "goal": goal,
            "page": {
                "url": state.get("url", ""),
                "title": state.get("title", ""),
                "text": (state.get("text") or "")[:_laya_max_text()],
            },
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-4:]
            ],
        },
        "questions": _laya_questions(candidates, goal),
    }
    started = time.perf_counter()
    result = _laya_call(body)
    return _laya_decision(candidates, targets, controls, result, started, body)


def laya_loaded():
    return _LAYA_AGENT is not None


def unload_laya():
    global _LAYA_AGENT
    with _LAYA_LOCK:
        _LAYA_AGENT = None
    # Same reason as laya_ask.unload: torch keeps the VRAM reserved until the cache is emptied.
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except ImportError:
        pass


def llm_json(system, user):
    """One JSON-object response from the configured text model (deepseek-v4-flash via opencode go)."""
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TEXT_MODEL_API_KEY is not set; the text model is required for routing and decisions")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    extra = (
        {
            "x-opencode-session": os.environ.get("TEXT_MODEL_SESSION", "jev-ultrafast-default"),
            "User-Agent": "jev-ultrafast/0.1.0",
        }
        if "opencode.ai" in base
        else None
    )
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        },
        extra,
    )
    return json.loads(result["choices"][0]["message"]["content"])


DECISION_SYSTEM = """You drive a Windows desktop agent. The user gives a goal and the current UI state.
Pick ONE next operation and ONE target from the offered elements and controls.
Return JSON: {"operation": "CLICK|TYPE_TEXT|SELECT|DONE|BLOCKED|<control>", "target": "<element index or control id>"}.
Prefer TYPE_TEXT for an empty editable field the goal needs filled. DONE only when the goal is visibly
satisfied. BLOCKED only when nothing offered can help. Never invent an element index."""


def choose_deepseek(state, goal, history):
    """A general LLM decides the next operation and target, so tasks Laya cannot reason about work."""
    elements, targets, controls = action_space(state["actions"])
    elements, targets = _cap_action_space(elements, targets, _laya_max_elements())
    table = "\n".join(
        f"[{e['index']}] {e['role']}: {e['label']}" + (f" value={e['value']!r}" if e.get("value") else "")
        for e in elements
    )
    controls_text = ", ".join(f"{key} ({value['label']})" for key, value in controls.items()) or "-"
    user = json.dumps(
        {
            "goal": goal,
            "page": {"url": state.get("url", ""), "title": state.get("title", ""),
                     "text": (state.get("text") or "")[:600]},
            "elements": table,
            "controls": controls_text,
            "recent_actions": [{k: h.get(k) for k in ("action", "kind", "text")} for h in history[-4:]],
        },
        ensure_ascii=False,
    )
    started = time.perf_counter()
    answer = llm_json(DECISION_SYSTEM, user)
    answer = answer if isinstance(answer, dict) else {}
    operation = str(answer.get("operation", "BLOCKED")).upper()
    target = answer.get("target")
    action = None
    if operation in controls:
        action, target = controls[operation], operation
    elif operation in targets:
        for key, candidate in targets[operation].items():
            if key.split(":")[0] == str(target) or candidate["id"] == str(target):
                action, target = candidate, key
                break
    if action is None:
        operation = "BLOCKED"
    return {
        "choice": action["id"] if action else operation,
        "operation": operation,
        "target": target,
        "confidence": 1.0 if action else 0.0,
        "probabilities": {action["id"]: 1.0} if action else {},
        "operation_probabilities": {},
        "target_probabilities": {},
        "target_confidence": 1.0 if action else 0.0,
        "raw_answers": answer,
        "model": os.environ.get("TEXT_MODEL", "deepseek-chat"),
        "usage": {},
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {"system": DECISION_SYSTEM, "user": user},
    }


def choose(state, goal, history):
    """Pick the next operation and target. JEV_DECISION selects the engine: laya (local, free),
    deepseek (opencode go, reasons), or typesafe (cloud API)."""
    engine = os.environ.get("JEV_DECISION", "laya").lower()
    if engine == "laya":
        return choose_laya(state, goal, history)
    if engine == "deepseek":
        return choose_deepseek(state, goal, history)
    return choose_typesafe(state, goal, history)


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    # ponytail: OpenCode Go's client spec requires a session id and a real user agent.
    extra = (
        {
            "x-opencode-session": os.environ.get("TEXT_MODEL_SESSION", "jev-ultrafast-default"),
            "User-Agent": "jev-ultrafast/0.1.0",
        }
        if "opencode.ai" in base
        else None
    )
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
        extra,
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
