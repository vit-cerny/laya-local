"""Offline contracts for the Laya decision path and the desktop safety gate.

Pure logic only: no model loading, no GPU, no browser, no desktop. The Laya engine
itself is never imported; choose_laya is patched out. jev_cu is imported lazily
because its module import calls load_env() (setdefault-only, never overrides).
"""

import time
from unittest.mock import patch

import pytest

from jev_ultrafast import model

GOAL = "Find a book and open it"

ACTIONS = [
    {"kind": "click", "id": "e1", "node": 1, "role": "button", "label": "Go"},
    {"kind": "fill", "id": "e2", "node": 2, "role": "textbox", "label": "Search", "value": ""},
    {"kind": "select", "id": "e3", "node": 3, "role": "combobox", "label": "Sort by",
     "value": "Price", "current_value": "Price"},
    {"kind": "click", "id": "e4", "node": 4, "role": "button", "label": "Cancel"},
    {"kind": "wait", "id": "WAIT", "node": None, "label": "Wait for the UI to settle"},
]

OP_PROBS = {
    "CLICK": 0.8, "TYPE_TEXT": 0.1, "SELECT": 0.05, "WAIT": 0.03, "DONE": 0.01, "BLOCKED": 0.01,
}


def op_probs(choice):
    probs = {k: 0.01 for k in ("CLICK", "TYPE_TEXT", "SELECT", "WAIT", "DONE", "BLOCKED")}
    probs[choice] = 0.95
    return probs


def state():
    return {"url": "https://example.test/", "title": "Test", "text": "Test", "actions": ACTIONS}


def build():
    return model._build_questions(state(), GOAL, [])


def canned(operation_answer, target_answer=None):
    answers = {"operation": operation_answer}
    if target_answer is not None:
        answers[operation_answer["choice"].lower() + "_target"] = target_answer
    return {"model": "test-model", "answers": answers, "usage": {"prompt_tokens": 5}}


def decide(result, body=None):
    elements, targets, controls, operations, questions = build()
    return model._decide(
        elements, targets, controls, operations, questions, body or {"model": "test"},
        result, time.perf_counter(),
    )


def test_build_questions_operations_cover_all_heads():
    _, _, _, operations, _ = build()
    assert {"CLICK", "TYPE_TEXT", "SELECT", "DONE", "BLOCKED", "WAIT"} <= set(operations)
    assert operations["DONE"] and operations["BLOCKED"]


def test_build_questions_target_heads_keyed_by_element_index():
    _, targets, _, _, questions = build()
    assert set(targets) == {"CLICK", "TYPE_TEXT", "SELECT"}
    assert set(questions["click_target"]["criteria"]) == {"1", "4"}
    assert questions["click_target"]["criteria"]["1"]["element"] == "[1] Go"
    assert questions["type_text_target"]["criteria"]["2"]["role"] == "textbox"
    assert questions["select_target"]["criteria"]["3:1"]["current_value"] == "Price"


def test_build_questions_instructions_carry_goal():
    _, _, _, _, questions = build()
    assert questions["operation"]["instructions"]["goal"] == GOAL
    assert questions["click_target"]["instructions"]["goal"] == GOAL
    assert questions["click_target"]["instructions"]["operation"] == "CLICK"


def test_decide_click_returns_full_decision_dict():
    result = canned(
        {"choice": "CLICK", "confidence": 0.9, "probabilities": OP_PROBS},
        {"choice": "4", "confidence": 0.95, "probabilities": {"1": 0.1, "4": 0.9}},
    )
    d = decide(result)
    assert set(d) == {
        "choice", "operation", "target", "confidence", "probabilities",
        "operation_probabilities", "target_probabilities", "target_confidence",
        "raw_answers", "model", "usage", "latency_ms", "request",
    }
    assert d["choice"] == "e4" and d["operation"] == "CLICK" and d["target"] == "4"
    assert d["probabilities"] == {"e1": 0.1, "e4": 0.9}
    assert d["operation_probabilities"] == OP_PROBS
    assert d["target_probabilities"] == {"1": 0.1, "4": 0.9}
    assert d["confidence"] == 0.9 and d["target_confidence"] == 0.95
    assert d["model"] == "test-model" and d["raw_answers"] == result["answers"]
    assert d["request"] == {"model": "test"} and d["usage"] == {"prompt_tokens": 5}


def test_decide_done_has_no_target():
    d = decide(canned({"choice": "DONE", "confidence": 0.9, "probabilities": op_probs("DONE")}))
    assert d["choice"] == "DONE" and d["target"] is None
    assert d["probabilities"] == {"DONE": 0.95}
    assert d["target_probabilities"] == {} and d["target_confidence"] is None


def test_decide_control_operation_uses_control_id():
    d = decide(canned({"choice": "WAIT", "confidence": 0.9, "probabilities": op_probs("WAIT")}))
    assert d["choice"] == "WAIT" and d["target"] is None
    assert d["probabilities"] == {"WAIT": 0.95}


def test_decide_select_uses_option_index_target():
    d = decide(
        canned(
            {"choice": "SELECT", "confidence": 0.9, "probabilities": op_probs("SELECT")},
            {"choice": "3:1", "confidence": 0.8, "probabilities": {"3:1": 1.0}},
        )
    )
    assert d["choice"] == "e3" and d["target"] == "3:1"


@pytest.fixture
def choose_mocks():
    with patch("jev_ultrafast.model.choose_typesafe", return_value="ts") as ts, patch(
        "jev_ultrafast.model.choose_laya", return_value="laya"
    ) as la:
        yield ts, la


def test_choose_defaults_to_typesafe(monkeypatch, choose_mocks):
    ts, la = choose_mocks
    monkeypatch.delenv("JEV_DECISION", raising=False)
    assert model.choose({"a": 1}, GOAL, []) == "ts"
    ts.assert_called_once_with({"a": 1}, GOAL, [])
    la.assert_not_called()


def test_choose_typesafe_when_env_says_typesafe(monkeypatch, choose_mocks):
    ts, la = choose_mocks
    monkeypatch.setenv("JEV_DECISION", "typesafe")
    assert model.choose({}, GOAL, []) == "ts"
    ts.assert_called_once()
    la.assert_not_called()


def test_choose_laya_when_env_says_laya(monkeypatch, choose_mocks):
    ts, la = choose_mocks
    monkeypatch.setenv("JEV_DECISION", "laya")
    assert model.choose({}, GOAL, []) == "laya"
    la.assert_called_once()
    ts.assert_not_called()


def cu_decision(choice):
    return {
        "choice": choice, "operation": "CLICK", "target": "1", "confidence": 1.0,
        "probabilities": {choice: 1.0}, "operation_probabilities": {}, "target_probabilities": {},
        "target_confidence": None, "raw_answers": {}, "model": "test", "usage": {},
        "latency_ms": 1, "request": {},
    }


CU_ACTIONS = [
    {"id": "e1", "kind": "click", "label": "Delete file", "element": object()},
    {"id": "e2", "kind": "click", "label": "Open file", "element": object()},
]


def apply(choice, allow_sensitive=False, dry_run=True, actions=CU_ACTIONS):
    from jev_cu import apply_decision

    logs = []
    return apply_decision(
        object(), cu_decision(choice), actions, GOAL, [], allow_sensitive, dry_run, log=logs.append
    )


def test_apply_decision_sensitive_label_blocked_regardless_of_dry_run():
    assert apply("e1", dry_run=True) == "blocked"
    assert apply("e1", dry_run=False) == "blocked"


def test_apply_decision_allow_sensitive_proceeds():
    assert apply("e1", allow_sensitive=True, dry_run=True) == "dry"


def test_apply_decision_innocent_label_dry():
    assert apply("e2", dry_run=True) == "dry"


def test_apply_decision_done_and_blocked_pass_through():
    assert apply("DONE", dry_run=True) == "DONE"
    assert apply("BLOCKED", dry_run=False) == "BLOCKED"


def test_apply_decision_wait_dry_run():
    assert apply("WAIT", dry_run=True) == "dry"


def test_apply_decision_executes_click_on_fake_window():
    from jev_cu import apply_decision

    calls = []

    class FakeElement:
        def set_focus(self):
            calls.append("element.set_focus")

        def click_input(self):
            calls.append("click_input")

    class FakeWindow:
        def set_focus(self):
            calls.append("window.set_focus")

    actions = [{"id": "e1", "kind": "click", "label": "Open file", "element": FakeElement()}]
    history = []
    outcome = apply_decision(
        FakeWindow(), cu_decision("e1"), actions, GOAL, history, False, dry_run=False,
        log=lambda *a: None,
    )
    assert outcome == "executed"
    assert calls == ["window.set_focus", "element.set_focus", "click_input"]
    assert history[-1]["action"] == "Open file"


def test_validate_choice_rejects_probabilities_not_covering_all_ids():
    answer = {"choice": "a", "confidence": 1.0, "probabilities": {"a": 1.0}}
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(answer, {"a", "b"})


def test_jev_tools_exposes_mcp_callables():
    import jev_tools

    if jev_tools.MCPServer is None:
        pytest.skip("mcp.server.mcpserver not importable")
    for name in ("jev_search", "jev_stats", "laya_ask", "jev_cu"):
        assert callable(getattr(jev_tools, name))