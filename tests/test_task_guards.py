import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from laya_cu import tasks


def test_destructive_goal_is_refused():
    kind, token = tasks.unsafe_goal("delete all files in Documents")
    assert kind == "destructive"
    assert token == "delete"


def test_credential_goal_is_refused():
    kind, token = tasks.unsafe_goal("log into the bank and type my password")
    assert kind == "credential"
    assert token == "password"


def test_normal_goal_passes():
    assert tasks.unsafe_goal("open example.com and download an image") == (None, None)
    assert tasks.unsafe_goal("find me a cheap flight to London") == (None, None)


def test_run_task_refuses_before_calling_the_router(monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("llm_json must not run for a refused goal")

    monkeypatch.setattr(tasks, "llm_json", boom)
    result = tasks.run_task("wipe the disk", log=lambda *_a: None)
    assert result["status"] == "blocked"
    assert "destructive" in result["reason"]


def test_run_task_dry_run_stops_before_any_action(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_TASK_LIMITS_STATE", str(tmp_path / "limits.json"))
    monkeypatch.setattr(tasks, "llm_json", lambda *_a, **_k: {"route": "cli", "action": "open", "url": "example.com"})
    opened = []
    monkeypatch.setattr(tasks, "open_in_browser", lambda url, log=print: opened.append(url))
    result = tasks.run_task("open example.com", execute=False, log=lambda *_a: None)
    assert result["status"] == "dry-run"
    assert opened == []


def test_daily_cap_blocks_after_the_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_TASK_LIMITS_STATE", str(tmp_path / "limits.json"))
    monkeypatch.setenv("JEV_TASK_MAX_PER_DAY", "2")
    assert tasks.check_daily_cap()[0] is True
    tasks.record_task()
    assert tasks.check_daily_cap()[0] is True
    tasks.record_task()
    ok, reason = tasks.check_daily_cap()
    assert ok is False
    assert "2/2" in reason


def test_allow_sensitive_overrides_the_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_TASK_LIMITS_STATE", str(tmp_path / "limits.json"))
    monkeypatch.setattr(tasks, "llm_json", lambda *_a, **_k: {"route": "browser", "action": "search"})
    called = []

    def fake_search(goal, url=None):
        called.append(goal)
        return {"status": "done", "final_url": "x", "engine": "test"}

    monkeypatch.setitem(sys.modules, "jev_search", type(sys)("jev_search"))
    sys.modules["jev_search"].run_search = fake_search
    result = tasks.run_task("delete the spam folder", allow_sensitive=True, log=lambda *_a: None)
    assert result["status"] == "done"
    assert called == ["delete the spam folder"]
