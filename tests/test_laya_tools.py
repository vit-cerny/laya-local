"""Regression pins for the uncovered pure seams of the Laya tooling.

No model load, no microphone, no browser, no desktop mutation. These lock behavior
before the slop-removal pass so any accidental semantic change shows up as a failure.
"""

import os
from unittest.mock import patch

import pytest

# --- laya_ask public state API ---------------------------------------------


def test_laya_ask_loaded_is_false_and_unload_is_safe_without_a_model():
    import laya_ask

    original = laya_ask._AGENT
    laya_ask._AGENT = None
    try:
        assert laya_ask.loaded() is False
        laya_ask.unload()
        assert laya_ask.loaded() is False
    finally:
        laya_ask._AGENT = original


def test_laya_ask_loaded_reflects_a_loaded_agent():
    import laya_ask

    original = laya_ask._AGENT
    laya_ask._AGENT = object()
    try:
        assert laya_ask.loaded() is True
        laya_ask.unload()
        assert laya_ask.loaded() is False
    finally:
        laya_ask._AGENT = original


# --- jev_browser sandbox profile selection ---------------------------------


def test_sandboxed_reads_the_env_flag():
    import jev_browser

    with patch.dict(os.environ, {"JEV_SANDBOX": "1"}):
        assert jev_browser.sandboxed() is True
    with patch.dict(os.environ, {"JEV_SANDBOX": "0"}):
        assert jev_browser.sandboxed() is False
    with patch.dict(os.environ, {}, clear=True):
        assert jev_browser.sandboxed() is False


def test_profile_for_separates_sandbox_from_normal():
    import jev_browser

    normal = jev_browser.profile_for("chrome.exe", sandbox=False)
    sandbox = jev_browser.profile_for("chrome.exe", sandbox=True)
    assert normal.name == "jev-chrome-profile"
    assert sandbox.name == "jev-chrome-sandbox-profile"
    assert normal != sandbox


def test_sandbox_flags_cover_the_hardening_set():
    import jev_browser

    flags = set(jev_browser.SANDBOX_FLAGS)
    for expected in ("--disable-extensions", "--disable-sync", "--disable-background-networking", "--no-pings"):
        assert expected in flags
    assert all(flag.startswith("--") for flag in flags)


def test_sandbox_profiles_returns_a_list():
    import jev_browser

    assert isinstance(jev_browser.sandbox_profiles(), list)


# --- voice transcript gate and routing hints -------------------------------


def test_voice_accept_gate():
    from laya_voice import _accept

    assert _accept("open notepad", 0.05) is True
    assert _accept("open notepad", 0.001) is False  # near-silent
    assert _accept("hi", 0.05) is False  # too short
    assert _accept("[BLANK_AUDIO]", 0.05) is False  # filler
    assert _accept("  ", 0.05) is False


def test_voice_routing_hints_classify_browser_vs_desktop():
    from laya_voice import BROWSER_HINT, STOP_HINT

    assert BROWSER_HINT.search("check the price on the web")
    assert BROWSER_HINT.search("open the site")
    assert not BROWSER_HINT.search("open notepad and type hello")
    assert STOP_HINT.search("stop listening")
    assert STOP_HINT.search("quit")
    assert not STOP_HINT.search("open notepad")


# --- jev_cu window-miss containment ---------------------------------------


def test_run_goal_reports_a_missing_window_as_an_error():
    pytest.importorskip("pywinauto")
    from jev_cu import run_goal

    result = run_goal("do something", window="NoSuchWindowZZZ", execute=False, max_steps=1, log=lambda *_: None)
    assert result["status"] == "error"
    assert "NoSuchWindowZZZ" in str(result.get("error"))
