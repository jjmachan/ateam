"""Garbage collection: delete old Session files on HomeBase, keeping a Tombstone.

Rules, in order:
1. A Session whose Holder still has its Worktree is never collected.
2. A Session that is Open (and not Stale) is never collected.
3. Inactive for `gone_days` and its Worktree exists on no Machine -> collected.
4. Inactive for `inactive_days` -> collected.

What a Machine has comes from its Inventory. A Machine that has never reported one might have
anything, so it always counts as still having the Worktree.
"""

import shutil
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from homebase.db import DB, now

INACTIVE_DAYS = 60
GONE_DAYS = 14


@dataclass
class Verdict:
    id: str
    handle: str
    collect: bool
    reason: str


def _inventories(db: DB) -> dict[str, set[tuple[str, str]] | None]:
    """Machine -> its (project, branch) Worktrees, or None if it has never reported."""
    inventories = {m["name"]: None if m["inventory_at"] is None else set() for m in db.query("SELECT * FROM machines")}
    for row in db.query("SELECT machine, project_path, branch FROM machine_worktrees"):
        if inventories.get(row["machine"]) is not None:
            inventories[row["machine"]].add((row["project_path"], row["branch"]))
    return inventories


def judge(session: dict, inventories: dict, at: datetime, inactive_days: int, gone_days: int, stale) -> Verdict:
    def verdict(collect: bool, reason: str) -> Verdict:
        return Verdict(session["id"], session["handle"], collect, reason)

    key = (session["project_path"], session["branch"])
    holder = inventories.get(session["holder"])
    if holder is None:
        return verdict(False, f"{session['holder']} has not reported its inventory")
    if key in holder:
        return verdict(False, "holder has the worktree")
    if session["is_open"] and not stale(session):
        return verdict(False, "open")
    idle = at - datetime.fromisoformat(session["last_activity_at"])
    exists = any(inv is None or key in inv for inv in inventories.values())
    if not exists and idle > timedelta(days=gone_days):
        return verdict(True, f"worktree gone everywhere, idle {idle.days}d")
    if idle > timedelta(days=inactive_days):
        return verdict(True, f"idle {idle.days}d")
    return verdict(False, f"idle {idle.days}d")


def run(
    db: DB, files_dir: Path, stale, dry_run: bool = True, inactive_days: int = INACTIVE_DAYS, gone_days: int = GONE_DAYS
) -> list[dict]:
    inventories = _inventories(db)
    at = datetime.now(UTC)
    verdicts = [
        judge(s, inventories, at, inactive_days, gone_days, stale)
        for s in db.query("SELECT * FROM sessions WHERE tombstoned_at IS NULL")
    ]
    if not dry_run:
        for v in verdicts:
            if v.collect:
                shutil.rmtree(files_dir / v.id, ignore_errors=True)
                db.execute("DELETE FROM session_files WHERE session_id = ?", v.id)
                db.execute("UPDATE sessions SET tombstoned_at = ?, is_open = 0 WHERE id = ?", now(), v.id)
    return [asdict(v) for v in verdicts]
