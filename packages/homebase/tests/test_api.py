from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from homebase.app import dev_app

SID = "19c3d05a-b567-4dc6-8db5-6a642d74a708"


@pytest.fixture
def hb(tmp_path):
    app = dev_app(tmp_path)
    clients = {}
    for name, root in (("macbook", "/Users/j/workspace"), ("nixbox", "/home/j/workspace")):
        c = TestClient(app, headers={"X-ATeam-Node": f"node-{name}"})
        c.post("/machines/register", json={"name": name, "workspace_root": root}).raise_for_status()
        clients[name] = c
    clients["db"] = app
    return clients


def create(client, sid=SID, **extra):
    body = {"id": sid, "harness": "claude-code", "project_path": "personal/ateam", "branch": "feat/init", **extra}
    r = client.post("/sessions", json=body)
    r.raise_for_status()
    return r.json()


def test_unregistered_device_is_rejected(tmp_path):
    c = TestClient(dev_app(tmp_path), headers={"X-ATeam-Node": "stranger"})
    assert c.post("/sessions", json={}).status_code in (401, 422)
    assert c.get("/me").status_code == 401


def test_name_cannot_be_stolen_by_another_device(hb, tmp_path):
    other = TestClient(hb["db"], headers={"X-ATeam-Node": "node-evil"})
    r = other.post("/machines/register", json={"name": "macbook", "workspace_root": "/x"})
    assert r.status_code == 409


def test_first_uploader_becomes_holder_and_gets_handle(hb):
    s = create(hb["macbook"])
    assert s["holder"] == "macbook"
    assert s["handle"] == "ateam/feat-init/19c3d0"


def test_append_only_upload(hb):
    mac = hb["macbook"]
    create(mac)
    url = f"/sessions/{SID}/files/transcript.jsonl"
    root = {"root": "/Users/j/workspace"}
    assert mac.put(url, params={**root, "offset": 0}, content=b"one\n").json()["size"] == 4
    assert mac.put(url, params={**root, "offset": 4}, content=b"two\n").json()["size"] == 8
    assert mac.put(url, params={**root, "offset": 4}, content=b"x").status_code == 409
    assert mac.get(url).content == b"one\ntwo\n"
    assert mac.put(f"/sessions/{SID}/files/../evil", params={**root, "offset": 0}, content=b"x").status_code in (400, 404)


def test_handoff_moves_holder_and_logs_it(hb):
    create(hb["macbook"])
    r = hb["nixbox"].post(f"/sessions/{SID}/handoff", json={"initiator_kind": "agent", "initiator_session": "abc"})
    assert r.status_code == 200, r.text
    assert r.json()["session"]["holder"] == "nixbox"
    log = hb["nixbox"].get("/handoffs", params={"session": SID}).json()
    assert [(h["from_machine"], h["to_machine"], h["initiator_kind"]) for h in log] == [("macbook", "nixbox", "agent")]
    # The old Holder can no longer upload.
    r = hb["macbook"].put(f"/sessions/{SID}/files/transcript.jsonl", params={"root": "/x", "offset": 0}, content=b"x")
    assert r.status_code == 409 and r.json()["detail"]["error"] == "not_holder"


def test_open_session_needs_force_and_agents_only_force_stale(hb):
    create(hb["macbook"])
    hb["macbook"].post(f"/sessions/{SID}/events", json={"kind": "open"}).raise_for_status()
    url = f"/sessions/{SID}/handoff"
    r = hb["nixbox"].post(url, json={})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "open"
    r = hb["nixbox"].post(url, json={"force": True, "initiator_kind": "agent"})
    assert r.status_code == 403
    # Go stale: no activity for longer than the threshold.
    old = (datetime.now(UTC) - timedelta(minutes=20)).isoformat()
    hb["db"].state.db.execute("UPDATE sessions SET last_activity_at = ? WHERE id = ?", old, SID)
    assert hb["nixbox"].get(f"/sessions/{SID}").json()["stale"] is True
    r = hb["nixbox"].post(url, json={"force": True, "initiator_kind": "agent"})
    assert r.status_code == 200
    assert hb["nixbox"].get("/handoffs").json()[-1]["forced"] == 1


def test_human_can_force_active_session(hb):
    create(hb["macbook"])
    hb["macbook"].post(f"/sessions/{SID}/events", json={"kind": "open"})
    r = hb["nixbox"].post(f"/sessions/{SID}/handoff", json={"force": True, "initiator_kind": "human"})
    assert r.status_code == 200


def test_branch_follows_the_session_but_handle_is_fixed(hb):
    create(hb["macbook"], branch="main")
    s = create(hb["macbook"], branch="feat/init")
    assert (s["branch"], s["handle"]) == ("feat/init", "ateam/main/19c3d0")
