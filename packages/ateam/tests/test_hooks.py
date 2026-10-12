import json

from ateam import hooks


def test_install_merges_and_is_idempotent(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "other"}]}]}}))
    hooks.install(settings, "/bin/ateam")
    hooks.install(settings, "/bin/ateam")
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"
    stop = [h["command"] for e in data["hooks"]["Stop"] for h in e["hooks"]]
    assert stop == ["other", "/bin/ateam hook stop"]
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == "/bin/ateam hook session-start"


def test_unreachable_homebase_queues_event(tmp_path, monkeypatch):
    from ateam import config

    monkeypatch.setenv("ATEAM_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("ATEAM_CONFIG", str(tmp_path / "config.json"))
    config.save(config.Config(homebase_url="http://127.0.0.1:9", machine="m", workspace_root=str(tmp_path)))
    hb = hooks.HomeBase(config.load())
    hooks._sync(hb, config.load(), "stop", "abc", "")
    assert (tmp_path / "state/pending/abc").read_text().startswith("stop\n")


def test_not_holder_marker_is_shown_by_next_hook(tmp_path, monkeypatch):
    monkeypatch.setenv("ATEAM_STATE", str(tmp_path / "state"))
    (tmp_path / "state/not-holder").mkdir(parents=True)
    (tmp_path / "state/not-holder/abc").write_text("session x/y/abc was handed off to nixbox")
    monkeypatch.setattr(hooks.subprocess, "Popen", lambda *a, **k: None)
    out = hooks.run_hook("stop", {"session_id": "abc"})
    assert out == {"systemMessage": "A-Team: session x/y/abc was handed off to nixbox"}
