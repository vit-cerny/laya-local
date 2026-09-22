"""Drive the browser-use framework with the local Laya decision engine.

browser-use asks its LLM for a structured AgentOutput; Laya is a non-autoregressive decision
model that answers typed choice questions. This adapter bridges the two: it parses the browser
state out of the browser-use prompt, asks Laya which operation and which element to act on,
and returns the AgentOutput the agent expects. No cloud call is made for the decision.

Usage:
    from browser_use import Agent
    from browser_use.browser.session import BrowserSession
    from browser_use_laya import LayaChatModel

    session = BrowserSession(cdp_url="http://127.0.0.1:9222")
    agent = Agent(task="Click the link that says Learn more", llm=LayaChatModel(), browser_session=session)
    await agent.run(max_steps=8)
"""

import os
import re
import sys
import time
import typing

from browser_use.llm.views import ChatInvokeCompletion, ChatInvokeUsage
from browser_use.tools.views import ClickElementAction, DoneAction, ScrollAction

ELEMENT_RE = re.compile(r"\*?\[(\d+)\]<([a-zA-Z0-9_-]+)([^>]*?)(?:/>|>([^<]*)<)", re.DOTALL)
LABEL_ATTR_RE = re.compile(r"(?:aria-label|placeholder|title|name|value|alt)=['\"]([^'\"]+)['\"]")
URL_RE = re.compile(r"Current URL:\s*(\S+)")

OPERATIONS = {
    "CLICK": "Click the element that moves the task forward.",
    "SCROLL": "Scroll the page because the target is not visible yet.",
    "DONE": "Every requirement is visibly satisfied.",
    "BLOCKED": "No available operation can make progress.",
}


def _wrapper_by_field(output_format):
    """Map action name -> its wrapper model, read from the concrete union the agent built."""
    annotation = output_format.model_fields["action"].annotation
    union = typing.get_args(annotation)[0]
    wrappers = {}
    for member in typing.get_args(union.model_fields["root"].annotation):
        name = next(iter(member.model_fields), None)
        if name:
            wrappers[name] = member
    return union, wrappers


def _parse_prompt(messages):
    """Pull the current URL and the indexed interactive elements out of the browser-use prompt.

    The system message documents the format first, including the literal line
    "Current URL: URL of the page you are currently viewing." and the words "Interactive
    elements", so both must be read from the LAST occurrence (the real state), and a
    placeholder URL has to be rejected or it wins over the real one.
    """
    parts = []
    for message in messages:
        content = getattr(message, "content", None)
        parts.append(content if isinstance(content, str) else str(content if content is not None else message))
    text = "\n".join(parts)
    urls = [found for found in URL_RE.findall(text) if "." in found and found != "URL"]
    url = urls[-1] if urls else ""
    section = text.rsplit("Interactive elements", 1)[-1] if "Interactive elements" in text else ""
    elements = []
    seen = set()
    for index, tag, attrs, inner in ELEMENT_RE.findall(section):
        if index in seen:
            continue
        seen.add(index)
        label = re.sub(r"\s+", " ", inner or "").strip()
        if not label:
            match = LABEL_ATTR_RE.search(attrs or "")
            label = match.group(1).strip() if match else ""
        elements.append((int(index), f"[{index}]<{tag}> {label}".strip()[:120]))
    return url, elements[:40]


class LayaChatModel:
    """A BaseChatModel (Protocol) implementation backed by the local Laya engine."""

    model = "laya-typed-decisions"
    _verified_api_keys = True

    def __init__(self, max_elements=40):
        self.max_elements = max_elements
        self.last_error = None

    @property
    def provider(self) -> str:
        return "laya"

    @property
    def name(self) -> str:
        return self.model

    @property
    def model_name(self) -> str:
        return self.model

    def _usage(self, tokens):
        return ChatInvokeUsage(
            prompt_tokens=int(tokens or 0),
            prompt_cached_tokens=None,
            prompt_cache_creation_tokens=None,
            prompt_image_tokens=None,
            completion_tokens=0,
            total_tokens=int(tokens or 0),
        )

    def _decide(self, url, elements, task):
        """Ask Laya for one operation and one target index. Returns (operation, index|None, latency)."""
        from laya_ask import agent

        criteria = {"DONE": OPERATIONS["DONE"], "BLOCKED": OPERATIONS["BLOCKED"]}
        if elements:
            criteria["CLICK"] = OPERATIONS["CLICK"]
            criteria["SCROLL"] = OPERATIONS["SCROLL"]
        questions = {
            "operation": {
                "type": "choice",
                "criteria": criteria,
                "instructions": {"goal": task, "page": url, "rules": "Pick the one operation that advances the goal."},
            }
        }
        if elements:
            questions["click_target"] = {
                "type": "choice",
                "criteria": {str(index): {"element": label} for index, label in elements},
                "instructions": {"goal": task, "page": url, "operation": "CLICK",
                                 "rules": "Pick the element to click."},
            }
        started = time.perf_counter()
        result = agent().predict(
            {"page": {"url": url}, "elements": [{"index": i, "label": lbl} for i, lbl in elements]},
            questions,
        )
        latency = round((time.perf_counter() - started) * 1000)
        answers = result.get("answers", {})
        operation = (answers.get("operation") or {}).get("choice", "BLOCKED")
        index = None
        if operation == "CLICK" and "click_target" in questions:
            target = (answers.get("click_target") or {}).get("choice")
            if target is not None:
                index = int(target)
        return operation, index, latency, result.get("usage", {}).get("input_tokens")

    async def ainvoke(self, messages, output_format=None, **kwargs):
        task = kwargs.get("task") or ""
        if not task:
            for message in messages:
                content = getattr(message, "content", "")
                if isinstance(content, str) and "USER REQUEST" in content.upper():
                    task = content[:400]
                    break
        url, elements = _parse_prompt(messages)
        operation, index, latency, tokens = self._decide(url, elements, task)
        if os.environ.get("LAYA_BU_DEBUG") == "1":
            print(f"[laya] url={url!r} elements={len(elements)} -> {operation} {index} ({latency} ms)",
                  file=sys.stderr, flush=True)

        if output_format is None:
            return ChatInvokeCompletion(completion=f"{operation} {index if index is not None else ''}".strip(),
                                        usage=self._usage(tokens))

        union, wrappers = _wrapper_by_field(output_format)
        thinking = f"laya: {operation} target={index} ({latency} ms, {len(elements)} elements)"

        def build(action_name, params):
            wrapper = wrappers.get(action_name)
            if wrapper is None:
                return None
            field = next(iter(wrapper.model_fields))
            return union(root=wrapper(**{field: params}))

        action = None
        if operation == "CLICK" and index is not None:
            action = build("click", ClickElementAction(index=index))
        elif operation == "SCROLL":
            action = build("scroll", ScrollAction(down=True, pages=1.0))
        if action is None:
            summary = f"Laya finished: {operation}" + (f" on element {index}" if index is not None else "")
            action = build("done", DoneAction(text=summary, success=operation == "DONE")) or build(
                "click", ClickElementAction(index=elements[0][0] if elements else 1)
            )
        completion = output_format(thinking=thinking, evaluation_previous_goal=None, memory=None,
                                   next_goal=f"{operation}", action=[action])
        return ChatInvokeCompletion(completion=completion, usage=self._usage(tokens))
