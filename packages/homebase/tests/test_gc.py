from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from homebase.app import dev_app

SID = "19c3d05a-b567-4dc6-8db5-6a642d74a708"
KEY = {"project_path": "personal/ateam", "branch": "feat/x"}


@pytest.fixture
def hb(tmp_path):
    app = dev_app(tmp_path)
    clients = {"app": app}
    for name in ("macbook", "nixbox"):
        c = TestClient(app, headers={"X-ATeam-Node": name})
        c.post("/machines/register", json={"name": name, "workspace_root": f"/{name}"}).raise_for_status()
        clients[name] = c
    mac = clients["macbook"]
    mac.post("/sessions", json={"id": SID, "harness": "claude-code", **KEY}).raise_for_status()
    mac.put(f"/sessions/{SID}/files/transcript.jsonl", params={"root": "/macbook"}, content=b"x\n").raise_for_status()
    return clients


def idle(hb, days):
    at = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    hb["app"].state.db.execute("UPDATE sessions SET last_activity_at = ? WHERE id = ?", at, SID)


def inventory(client, *worktrees):
    client.put("/me/inventory", json={"worktrees": list(worktrees)}).raise_for_status()


def verdict(hb, apply=False, **params):
    rows = hb["macbook"].post("/gc", params={"dry_run": not apply, **params}).json()
    return next(r for r in rows if r["id"] == SID)


def test_unknown_inventory_is_never_collected(hb):
    idle(hb, 400)
    assert verdict(hb)["collect"] is False  # the Holder never reported, so it might have the Worktree


def test_holder_with_worktree_keeps_session_forever(hb):
    inventory(hb["macbook"], KEY)
    inventory(hb["nixbox"])
    idle(hb, 400)
    assert verdict(hb) == {"id": SID, "handle": "ateam/feat-x/19c3d0", "collect": False, "reason": "holder has the worktree"}


def test_worktree_gone_everywhere_collects_after_14_days(hb):
    inventory(hb["macbook"])
    inventory(hb["nixbox"])
    idle(hb, 10)
    assert verdict(hb)["collect"] is False
    idle(hb, 15)
    assert verdict(hb)["collect"] is True


def test_worktree_elsewhere_collects_after_60_days(hb):
    inventory(hb["macbook"])
    inventory(hb["nixbox"], KEY)
    idle(hb, 30)
    assert verdict(hb)["collect"] is False
    idle(hb, 61)
    assert verdict(hb)["collect"] is True


def test_an_unreported_machine_counts_as_having_it(hb):
    inventory(hb["macbook"])
    idle(hb, 30)  # nixbox never reported: could still have the Worktree, so only the 60-day rule applies
    assert verdict(hb)["collect"] is False


def test_open_session_is_kept(hb):
    inventory(hb["macbook"])
    inventory(hb["nixbox"])
    hb["macbook"].post(f"/sessions/{SID}/events", json={"kind": "open"})
    idle(hb, 15)  # also makes it Stale, so it is no longer protected
    assert verdict(hb)["collect"] is True


def test_apply_tombstones_and_activity_revives(hb):
    inventory(hb["macbook"])
    inventory(hb["nixbox"])
    idle(hb, 15)
    verdict(hb, apply=True)
    s = hb["macbook"].get(f"/sessions/{SID}").json()
    assert s["tombstoned_at"] and s["files"] == []
    assert hb["nixbox"].post(f"/sessions/{SID}/handoff", json={}).status_code == 410
    # The Holder resumes it: new activity clears the Tombstone and the files come back.
    later = (datetime.now(UTC) + timedelta(minutes=1)).isoformat()
    body = {"id": SID, "harness": "claude-code", "last_activity_at": later, **KEY}
    assert hb["macbook"].post("/sessions", json=body).json()["tombstoned_at"] is None
