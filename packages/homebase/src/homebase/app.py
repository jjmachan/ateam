"""HomeBase HTTP API.

Session files are stored opaquely, exactly as the Harness wrote them (ADR 0001), under
`<data>/sessions/<session id>/<path>`. Each file remembers the Workspace Root its paths were
written against, so the CLI can rewrite them when the Session lands on another Machine.
"""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from pydantic import BaseModel

from homebase import handles
from homebase.db import DB, now
from homebase.identity import Caller, HeaderIdentity, Identity, TailscaleIdentity

STALE_AFTER = timedelta(minutes=15)


class RegisterBody(BaseModel):
    name: str
    workspace_root: str
    harness_versions: dict[str, str] = {}


class SessionBody(BaseModel):
    id: str
    harness: str
    harness_version: str | None = None
    project_path: str
    project_remote: str | None = None
    branch: str
    subdir: str = ""
    title: str | None = None
    created_at: str | None = None
    last_activity_at: str | None = None


class HandoffBody(BaseModel):
    force: bool = False
    initiator_kind: Literal["human", "agent"] = "human"
    initiator_session: str | None = None


class EventBody(BaseModel):
    kind: Literal["open", "activity", "close"]


def _safe_path(path: str) -> str:
    parts = Path(path).parts
    if not parts or Path(path).is_absolute() or ".." in parts:
        raise HTTPException(400, f"bad file path {path!r}")
    return path


def is_stale(session: dict) -> bool:
    last = datetime.fromisoformat(session["last_activity_at"])
    return datetime.now(UTC) - last > STALE_AFTER


def create_app(data_dir: Path, identity: Identity | None = None) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True)
    db = DB(data_dir / "homebase.db")
    files_dir = data_dir / "sessions"
    identity = identity or TailscaleIdentity()
    app = FastAPI(title="HomeBase")
    app.state.db = db

    def caller(request: Request) -> Caller:
        return identity(request)

    def me(c: Caller = Depends(caller)) -> dict:
        machine = db.machine_by_node(c.node_id)
        if machine is None:
            raise HTTPException(401, f"device {c.node_name!r} is not registered; run `ateam machine register`")
        return machine

    def session_or_404(session_id: str) -> dict:
        row = db.one("SELECT * FROM sessions WHERE id = ?", session_id)
        if row is None:
            raise HTTPException(404, f"no session {session_id}")
        return row

    def require_holder(session: dict, machine: dict) -> None:
        if session["holder"] != machine["name"]:
            raise HTTPException(
                409,
                {
                    "error": "not_holder",
                    "message": f"session {session['handle']} was handed off to {session['holder']}",
                    "holder": session["holder"],
                },
            )

    # Machines

    @app.post("/machines/register")
    def register(body: RegisterBody, c: Caller = Depends(caller)):
        existing = db.machine(body.name)
        if existing and existing["node_id"] != c.node_id:
            raise HTTPException(409, f"machine name {body.name!r} is bound to another device")
        bound = db.machine_by_node(c.node_id)
        if bound and bound["name"] != body.name:
            raise HTTPException(409, f"this device is already registered as {bound['name']!r}")
        db.execute(
            """INSERT INTO machines (name, node_id, workspace_root, harness_versions, registered_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET workspace_root = excluded.workspace_root,
                                               harness_versions = excluded.harness_versions""",
            body.name, c.node_id, body.workspace_root.rstrip("/"), json.dumps(body.harness_versions), now(),
        )
        return db.machine(body.name)

    @app.get("/machines")
    def machines():
        return db.machines()

    @app.get("/me")
    def whoami(machine: dict = Depends(me)):
        return machine

    # Sessions

    @app.post("/sessions")
    def upsert_session(body: SessionBody, machine: dict = Depends(me)):
        existing = db.one("SELECT * FROM sessions WHERE id = ?", body.id)
        if existing:
            require_holder(existing, machine)
            db.execute(
                """UPDATE sessions SET harness_version = COALESCE(?, harness_version), title = COALESCE(?, title),
                   branch = ?, subdir = ?,
                   last_activity_at = MAX(last_activity_at, COALESCE(?, last_activity_at)) WHERE id = ?""",
                body.harness_version, body.title, body.branch, body.subdir, body.last_activity_at, body.id,
            )
            return session_or_404(body.id)
        db.execute(
            "INSERT INTO projects (path, remote) VALUES (?, ?) ON CONFLICT(path) DO UPDATE SET remote = COALESCE(excluded.remote, remote)",
            body.project_path, body.project_remote,
        )
        others = [r["path"] for r in db.query("SELECT path FROM projects")]
        handle = handles.make_handle(body.id, body.project_path, others, body.branch)
        if db.one("SELECT 1 FROM sessions WHERE handle = ?", handle):
            # Two sessions in one Worktree sharing 6 hex digits: fall back to the full ID.
            handle = handle[: -handles.ID_CHARS] + body.id
        created = body.created_at or now()
        db.execute(
            """INSERT INTO sessions (id, handle, harness, harness_version, project_path, branch, subdir, title,
                                     holder, created_at, last_activity_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            body.id, handle, body.harness, body.harness_version, body.project_path, body.branch, body.subdir,
            body.title, machine["name"], created, body.last_activity_at or created,
        )
        return session_or_404(body.id)

    @app.get("/sessions")
    def list_sessions(
        q: str | None = None, project: str | None = None, branch: str | None = None, holder: str | None = None
    ):
        rows = db.query("SELECT * FROM sessions ORDER BY last_activity_at DESC")
        if q:
            rows = [r for r in rows if handles.matches(q, r["id"], r["handle"])]
        if project:
            rows = [r for r in rows if r["project_path"] == project]
        if branch:
            rows = [r for r in rows if r["branch"] == branch]
        if holder:
            rows = [r for r in rows if r["holder"] == holder]
        for r in rows:
            r["stale"] = bool(r["is_open"]) and is_stale(r)
        return rows

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str):
        row = session_or_404(session_id)
        row["stale"] = bool(row["is_open"]) and is_stale(row)
        row["files"] = db.query("SELECT path, size, sha256, root FROM session_files WHERE session_id = ?", session_id)
        return row

    @app.post("/sessions/{session_id}/events")
    def session_event(session_id: str, body: EventBody, machine: dict = Depends(me)):
        session = session_or_404(session_id)
        require_holder(session, machine)
        is_open = {"open": 1, "activity": 1, "close": 0}[body.kind]
        db.execute("UPDATE sessions SET is_open = ?, last_activity_at = ? WHERE id = ?", is_open, now(), session_id)
        return session_or_404(session_id)

    @app.post("/sessions/{session_id}/handoff")
    def handoff(session_id: str, body: HandoffBody, machine: dict = Depends(me)):
        """Pull: the calling Machine becomes the Holder."""
        session = session_or_404(session_id)
        if session["tombstoned_at"]:
            raise HTTPException(410, f"session {session['handle']} was garbage-collected")
        if session["holder"] == machine["name"]:
            return {"session": session, "handoff": None}
        if session["is_open"]:
            stale = is_stale(session)
            if not body.force:
                raise HTTPException(
                    409,
                    {
                        "error": "open",
                        "stale": stale,
                        "message": f"session {session['handle']} is open on {session['holder']}"
                        + (" but stale" if stale else "")
                        + "; pass --force to take it",
                    },
                )
            if not stale and body.initiator_kind == "agent":
                raise HTTPException(
                    403,
                    {"error": "agent_force_active", "message": "agents may only force stale sessions; ask a human"},
                )
        db.execute(
            "UPDATE sessions SET holder = ?, is_open = 0, last_activity_at = ? WHERE id = ?",
            machine["name"], now(), session_id,
        )
        db.execute(
            """INSERT INTO handoffs (session_id, from_machine, to_machine, at, forced, initiator_kind, initiator_session)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            session_id, session["holder"], machine["name"], now(), int(body.force), body.initiator_kind,
            body.initiator_session,
        )
        record = db.one("SELECT * FROM handoffs WHERE session_id = ? ORDER BY id DESC LIMIT 1", session_id)
        return {"session": session_or_404(session_id), "handoff": record}

    @app.get("/handoffs")
    def handoffs(session: str | None = None):
        if session:
            return db.query("SELECT * FROM handoffs WHERE session_id = ? ORDER BY id", session)
        return db.query("SELECT * FROM handoffs ORDER BY id")

    # Session files

    @app.put("/sessions/{session_id}/files/{path:path}")
    async def put_file(
        session_id: str,
        path: str,
        request: Request,
        root: str = Query(...),
        offset: int = Query(0),
        machine: dict = Depends(me),
    ):
        """Write bytes at `offset`; offset 0 replaces the file, any other offset must equal its current size."""
        session = session_or_404(session_id)
        require_holder(session, machine)
        path = _safe_path(path)
        body = await request.body()
        target = files_dir / session_id / path
        current = db.one("SELECT * FROM session_files WHERE session_id = ? AND path = ?", session_id, path)
        if offset:
            if current is None or current["size"] != offset or current["root"] != root:
                raise HTTPException(409, {"error": "offset_mismatch", "size": current["size"] if current else 0})
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "ab" if offset else "wb") as f:
            f.write(body)
        sha = hashlib.sha256(target.read_bytes()).hexdigest()
        db.execute(
            """INSERT INTO session_files (session_id, path, size, sha256, root) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(session_id, path) DO UPDATE SET size = excluded.size, sha256 = excluded.sha256,
                                                          root = excluded.root""",
            session_id, path, target.stat().st_size, sha, root,
        )
        return {"path": path, "size": target.stat().st_size, "sha256": sha}

    @app.get("/sessions/{session_id}/files/{path:path}")
    def get_file(session_id: str, path: str):
        session_or_404(session_id)
        target = files_dir / session_id / _safe_path(path)
        if not target.is_file():
            raise HTTPException(404, f"no file {path}")
        return Response(target.read_bytes(), media_type="application/octet-stream")

    return app


def dev_app(data_dir: Path) -> FastAPI:
    return create_app(data_dir, HeaderIdentity())
