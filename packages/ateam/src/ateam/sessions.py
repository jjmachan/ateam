"""Uploading Sessions to HomeBase and pulling them onto this Machine."""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ateam import claude_code, worktrees
from ateam.client import HomeBase, HomeBaseError
from ateam.config import Config, state_dir


class Refused(Exception):
    """A Handoff that cannot go ahead; the message says how to fix it."""


@dataclass
class UploadResult:
    session_id: str
    status: str  # uploaded | unchanged | skipped | not_holder
    detail: str = ""
    handle: str | None = None


def upload(hb: HomeBase, cfg: Config, local: claude_code.LocalSession) -> UploadResult:
    if not local.cwd:
        return UploadResult(local.id, "skipped", "transcript has no working directory")
    try:
        loc = worktrees.locate(Path(local.cwd), cfg.root)
    except worktrees.NotInProject as e:
        return UploadResult(local.id, "skipped", str(e))
    branch = local.branch or worktrees.git(loc.worktree, "branch", "--show-current") or "detached"
    try:
        session = hb.post(
            "/sessions",
            {
                "id": local.id,
                "harness": claude_code.HARNESS,
                "harness_version": local.version,
                "project_path": loc.project_path,
                "project_remote": loc.project_remote,
                "branch": branch,
                "subdir": loc.subdir,
                "title": local.title,
                "created_at": local.created_at,
                "last_activity_at": local.last_activity_at,
            },
        )
        stored = {f["path"]: f for f in hb.get(f"/sessions/{local.id}")["files"]}
        changed = sum(_upload_file(hb, cfg, local.id, name, path, stored.get(name)) for name, path in local.files().items())
    except HomeBaseError as e:
        if e.code == "not_holder":
            return UploadResult(local.id, "not_holder", str(e))
        raise
    return UploadResult(local.id, "uploaded" if changed else "unchanged", f"{changed} file(s)", session["handle"])


def _upload_file(hb: HomeBase, cfg: Config, session_id: str, name: str, path: Path, stored: dict | None) -> bool:
    data = path.read_bytes()
    root = str(cfg.root)
    url = f"/sessions/{session_id}/files/{name}"
    if stored and stored["root"] == root and len(data) >= stored["size"]:
        if hashlib.sha256(data[: stored["size"]]).hexdigest() == stored["sha256"]:
            if len(data) == stored["size"]:
                return False
            try:
                hb.put_bytes(url, data[stored["size"] :], root=root, offset=stored["size"])
                return True
            except HomeBaseError as e:
                if e.code != "offset_mismatch":
                    raise
    hb.put_bytes(url, data, root=root, offset=0)
    return True


@dataclass
class Plan:
    session: dict
    worktree: Path
    target_cwd: Path
    warnings: list[str]


def plan_pull(hb: HomeBase, cfg: Config, session_id: str) -> Plan:
    """Check that the Session can land here, before HomeBase changes its Holder."""
    session = hb.get(f"/sessions/{session_id}")
    project_dir = cfg.root / session["project_path"]
    if not project_dir.is_dir():
        raise Refused(f"project {session['project_path']} is not at {project_dir} on this machine")
    worktree = worktrees.find_worktree(project_dir, session["branch"])
    if worktree is None:
        raise Refused(
            f"no worktree for branch {session['branch']!r} in {project_dir}; "
            f"create it with `cd {project_dir} && wt switch {session['branch']}` and retry"
        )
    target = worktree / session["subdir"] if session["subdir"] else worktree
    warnings = []
    local_version = claude_code.harness_version()
    if session["harness_version"] and claude_code.version_tuple(local_version) < claude_code.version_tuple(
        session["harness_version"]
    ):
        warnings.append(
            f"session was written by Claude Code {session['harness_version']} but this machine has {local_version}"
        )
    if not target.is_dir():
        warnings.append(f"{target} does not exist; resuming from the worktree top instead")
        target = worktree
    return Plan(session, worktree, target, warnings)


def pull(hb: HomeBase, cfg: Config, plan: Plan) -> Path:
    """Download the Session and write it into Claude Code's storage with paths moved to this Machine."""
    session = hb.get(f"/sessions/{plan.session['id']}")
    files = {f["path"]: (f, hb.get_bytes(f"/sessions/{session['id']}/files/{f['path']}")) for f in session["files"]}
    if claude_code.TRANSCRIPT not in files:
        raise Refused(f"HomeBase has no transcript for {session['handle']}")
    contents = {}
    for name, (meta, data) in files.items():
        if claude_code.is_rewritable(name):
            pairs = _replacements(files, meta["root"], session["subdir"], plan.worktree, cfg.root)
            data = claude_code.rewrite(data, pairs)
        contents[name] = data
    return claude_code.materialize(session["id"], str(plan.target_cwd), contents, state_dir() / "backups")


def _replacements(files, source_root: str, subdir: str, worktree: Path, root: Path) -> list[tuple[str, str]]:
    """Source Worktree -> local Worktree first (directory names may differ), then Workspace Root -> Root."""
    _, transcript = files[claude_code.TRANSCRIPT]
    pairs = []
    for line in transcript.splitlines():
        if b'"cwd"' in line:
            try:
                cwd = json.loads(line).get("cwd")
            except ValueError:
                continue
            if cwd:
                source_worktree = Path(cwd)
                for _ in range(len(Path(subdir).parts) if subdir else 0):
                    source_worktree = source_worktree.parent
                pairs.append((str(source_worktree), str(worktree)))
                break
    pairs.append((source_root.rstrip("/"), str(root).rstrip("/")))
    return pairs


def report_inventory(hb: HomeBase, cfg: Config) -> int:
    """Tell HomeBase which Worktrees this Machine has, for every Project HomeBase knows about."""
    refs = [
        {"project_path": p["path"], "branch": branch}
        for p in hb.get("/projects")
        for branch in worktrees.branches(cfg.root / p["path"])
    ]
    hb.request("PUT", "/me/inventory", json={"worktrees": refs})
    return len(refs)
