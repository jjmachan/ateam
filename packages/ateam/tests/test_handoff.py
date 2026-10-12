"""Two simulated Machines with different Workspace Roots and worktree layouts, one in-process HomeBase."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ateam import claude_code, config, sessions, worktrees
from ateam.client import HomeBase
from homebase.app import dev_app

SID = "19c3d05a-b567-4dc6-8db5-6a642d74a708"


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def make_project(root: Path, worktree_dir: str) -> Path:
    """`<root>/personal/proj/main` plus a worktree for branch feat/x at `<root>/personal/proj/<worktree_dir>`."""
    main = root / "personal/proj/main"
    main.mkdir(parents=True)
    git(main, "init", "-q", "-b", "main")
    (main / "src").mkdir()
    (main / "src/app.py").write_text("print('hi')\n")
    git(main, "add", ".")
    git(main, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
    git(main, "worktree", "add", "-q", "-b", "feat/x", str(main.parent / worktree_dir))
    return main.parent / worktree_dir


class Machine:
    def __init__(self, tmp: Path, name: str, worktree_dir: str, app):
        self.name = name
        self.root = tmp / name / "workspace"
        self.root.mkdir(parents=True)
        self.worktree = make_project(self.root, worktree_dir).resolve()
        self.root = self.root.resolve()
        self.claude = tmp / name / "claude"
        self.state = tmp / name / "state"
        self.cfg = config.Config(homebase_url="http://hb", machine=name, workspace_root=str(self.root), dev_node=name)
        self.hb = HomeBase(self.cfg)
        self.hb.http = TestClient(app, base_url="http://hb", headers={"X-ATeam-Node": name})
        self.hb.post("/machines/register", {"name": name, "workspace_root": str(self.root)})

    def activate(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(self.claude))
        monkeypatch.setenv("ATEAM_STATE", str(self.state))


def write_transcript(m: Machine, cwd: Path, lines: list[dict]) -> Path:
    path = m.claude / "projects" / claude_code.encode_project_dir(str(cwd)) / f"{SID}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines))
    sub = path.with_suffix("") / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-a1.jsonl").write_text(json.dumps({"cwd": str(cwd), "sessionId": SID}) + "\n")
    fh = m.claude / "file-history" / SID
    fh.mkdir(parents=True)
    (fh / "abc@v1").write_text(f"backup mentions {m.root} but is file content\n")
    return path


@pytest.fixture
def machines(tmp_path, monkeypatch):
    monkeypatch.setattr(claude_code, "harness_version", lambda: "2.1.291")
    app = dev_app(tmp_path / "homebase")
    return Machine(tmp_path, "macbook", "feat-x", app), Machine(tmp_path, "nixbox", "main.feat-x", app)


def line(cwd, text, ts):
    return {"type": "user", "cwd": str(cwd), "gitBranch": "feat/x", "version": "2.1.284", "sessionId": SID,
            "timestamp": ts, "message": {"content": f"{text} in {cwd}/app.py"}}


def test_locate_understands_wt_layout(machines):
    mac, _ = machines
    loc = worktrees.locate(mac.worktree / "src", mac.root)
    assert (loc.project_path, loc.worktree, loc.subdir) == ("personal/proj", mac.worktree, "src")
    assert worktrees.find_worktree(mac.root / "personal/proj", "feat/x") == mac.worktree
    with pytest.raises(worktrees.NotInProject):
        worktrees.locate(mac.root, mac.root)


def test_round_trip_handoff(machines, monkeypatch):
    mac, nix = machines
    mac_cwd = mac.worktree / "src"

    # On the Mac: a Session is written and uploaded.
    mac.activate(monkeypatch)
    write_transcript(mac, mac_cwd, [line(mac_cwd, "hello", "2026-10-11T10:00:00Z"), {"type": "ai-title", "aiTitle": "Fix app"}])
    result = sessions.upload(mac.hb, mac.cfg, claude_code.read_session(claude_code.find_transcript(SID)))
    assert (result.status, result.handle) == ("uploaded", "proj/feat-x/19c3d0")
    assert sessions.upload(mac.hb, mac.cfg, claude_code.read_session(claude_code.find_transcript(SID))).status == "unchanged"

    # On nixbox: pull it. The worktree has a different directory name there.
    nix.activate(monkeypatch)
    plan = sessions.plan_pull(nix.hb, nix.cfg, SID)
    assert plan.target_cwd == nix.worktree / "src"
    nix.hb.post(f"/sessions/{SID}/handoff", {})
    transcript = sessions.pull(nix.hb, nix.cfg, plan)
    assert transcript.parent.name == claude_code.encode_project_dir(str(nix.worktree / "src"))
    text = transcript.read_text()
    assert str(mac.root) not in text and f'"cwd": "{nix.worktree}/src"' in text
    assert str(nix.worktree / "src") in (transcript.with_suffix("") / "subagents/agent-a1.jsonl").read_text()
    # Checkpoint backups are user file content and are never rewritten.
    assert str(mac.root) in (nix.claude / "file-history" / SID / "abc@v1").read_text()

    # The Mac is no longer the Holder.
    mac.activate(monkeypatch)
    assert sessions.upload(mac.hb, mac.cfg, claude_code.read_session(claude_code.find_transcript(SID))).status == "not_holder"

    # nixbox continues and uploads, then the Mac takes it back.
    nix.activate(monkeypatch)
    with transcript.open("a") as f:
        f.write(json.dumps(line(nix.worktree / "src", "more", "2026-10-11T11:00:00Z")) + "\n")
    assert sessions.upload(nix.hb, nix.cfg, claude_code.read_session(transcript)).status == "uploaded"

    mac.activate(monkeypatch)
    plan = sessions.plan_pull(mac.hb, mac.cfg, SID)
    mac.hb.post(f"/sessions/{SID}/handoff", {})
    back = sessions.pull(mac.hb, mac.cfg, plan)
    text = back.read_text()
    assert str(nix.root) not in text and text.count(f"{mac.worktree}/src") == 4
    assert list((mac.state / "backups").rglob(f"{SID}.jsonl")), "the stale local copy was backed up"
    log = mac.hb.get("/handoffs")
    assert [(h["from_machine"], h["to_machine"]) for h in log] == [("macbook", "nixbox"), ("nixbox", "macbook")]


def test_pull_refuses_missing_worktree(machines, monkeypatch):
    mac, nix = machines
    mac.activate(monkeypatch)
    write_transcript(mac, mac.worktree, [line(mac.worktree, "hi", "2026-10-11T10:00:00Z")])
    sessions.upload(mac.hb, mac.cfg, claude_code.read_session(claude_code.find_transcript(SID)))
    git(nix.root / "personal/proj/main", "worktree", "remove", str(nix.worktree))
    nix.activate(monkeypatch)
    with pytest.raises(sessions.Refused, match="wt switch feat/x"):
        sessions.plan_pull(nix.hb, nix.cfg, SID)
    assert nix.hb.get(f"/sessions/{SID}")["holder"] == "macbook"


def test_sessions_outside_a_project_are_skipped(machines, monkeypatch):
    mac, _ = machines
    mac.activate(monkeypatch)
    write_transcript(mac, Path("/tmp"), [line("/tmp", "hi", "2026-10-11T10:00:00Z")])
    assert sessions.upload(mac.hb, mac.cfg, claude_code.read_session(claude_code.find_transcript(SID))).status == "skipped"
