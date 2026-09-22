"""The HUD payload is pure logic: counters, speed, click location, confidence colour."""

from jev_ultrafast import hud


def test_payload_counts_speed_and_click_location():
    state = {
        "decisions": [{"latency_ms": 100, "model": "laya/x"}, {"latency_ms": 300, "model": "laya/x"}],
        "history": [{"kind": "click"}],
        "elapsed_ms": 12345,
        "status": "ready",
        "goal": "do a thing",
    }
    decision = {"operation": "CLICK", "target": "e2", "confidence": 0.7, "model": "laya/x"}
    action = {"id": "e2", "label": "Submit", "node": 5, "rect": {"x": 10, "y": 20, "w": 100, "h": 40}}

    payload = hud.payload(state, decision, action)

    assert payload["step"] == "2 decisions | 1 actions"
    assert payload["speed"] == "last 300 ms | avg 200 ms"
    assert payload["rate"] == "5.0/s"
    assert payload["click"] == 'e2 "Submit" at 60,40'
    assert payload["time"] == "12.3 s"
    assert payload["engine"] == "laya/x"
    assert payload["node"] == 5
    assert payload["rect"] == {"x": 10, "y": 20, "w": 100, "h": 40}


def test_low_confidence_is_flagged_red():
    state = {"decisions": [], "history": [], "elapsed_ms": 0, "status": "ready", "goal": "g"}

    payload = hud.payload(state, {"operation": "DONE", "target": None, "confidence": 0.058})

    assert payload["think"] == "DONE  conf 0.058"
    assert payload["thinkColor"] == "#ff7b72"
    assert payload["speed"] == "-"
    assert payload["click"] == "-"


def test_marker_is_suppressed_when_the_click_navigated():
    state = {
        "decisions": [{"latency_ms": 70}],
        "history": [{"kind": "click"}],
        "elapsed_ms": 1000,
        "status": "ready",
        "goal": "g",
    }
    action = {"id": "e9", "label": "Talk", "node": 9, "rect": {"x": 0, "y": 0, "w": 10, "h": 10}}

    assert hud.payload(state, None, action, page_changed=True)["page_changed"] is True
    assert hud.payload(state, None, action, page_changed=False)["page_changed"] is False


def test_hud_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("JEV_HUD", "0")
    assert hud.enabled() is False
    monkeypatch.delenv("JEV_HUD")
    assert hud.enabled() is True
