"""Claude Code hooks: keep HomeBase current without ever slowing the Harness down.

`ateam hook <event>` reads the hook payload, hands the work to a detached worker and returns at
once. The worker uploads the Session and reports Open/activity/close. If HomeBase is unreachable
the event is queued and retried by the next worker. If this Machine is no longer the Holder, the
next hook tells the user inside Claude Code.
"""

import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

from ateam import claude_code, config, sessions
from ateam.client import HomeBase, HomeBaseError

INVENTORY_EVERY = 3600  # seconds
EVENTS = {"session-start": "open", "stop": "activity", "session-end": "close"}


def _dir(name: str) -> Path:
    path = config.state_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def run_hook(event: str, payload: dict) -> dict | None:
    """Entry point for the Harness. Returns hook output for Claude Code, if any."""
    session_id = payload.get("session_id")
    if event not in EVENTS or not session_id:
        return None
    message = None
    marker = _dir("not-holder") / session_id
    if marker.exists():
        message = marker.read_text()
    elif event == "session-start":
        message = _check_holder(session_id)
    log = open(_dir("") / "hooks.log", "ab")
    subprocess.Popen(
        [sys.executable, "-m", "ateam.hooks", event, session_id, payload.get("transcript_path") or ""],
        stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
    )
    return {"systemMessage": f"A-Team: {message}"} if message else None


def _check_holder(session_id: str) -> str | None:
    try:
        cfg = config.load()
        hb = HomeBase(cfg)
        hb.http.timeout = 2
        session = hb.get(f"/sessions/{session_id}")
    except (SystemExit, HomeBaseError):
        return None
    if session["holder"] != cfg.machine:
        return f"session {session['handle']} is held by {session['holder']}; run `ateam handoff {session['handle']}` before continuing here"
    return None


def work(event: str, session_id: str, transcript: str) -> None:
    """Runs detached: retry anything queued, then sync this Session."""
    cfg = config.load()
    hb = HomeBase(cfg)
    # Stop and SessionEnd fire back to back; one worker at a time keeps uploads in order.
    lock = open(_dir("") / "worker.lock", "w")
    fcntl.flock(lock, fcntl.LOCK_EX)
    pending = _dir("pending")
    for queued in sorted(pending.iterdir()):
        queued_event, queued_transcript = queued.read_text().split("\n", 1)
        queued.unlink()
        _sync(hb, cfg, queued_event, queued.name, queued_transcript)
    _sync(hb, cfg, event, session_id, transcript)
    _maybe_report_inventory(hb, cfg)


def _maybe_report_inventory(hb: HomeBase, cfg: config.Config) -> None:
    stamp = _dir("") / "inventory.stamp"
    if stamp.exists() and time.time() - stamp.stat().st_mtime < INVENTORY_EVERY:
        return
    try:
        sessions.report_inventory(hb, cfg)
        stamp.touch()
    except HomeBaseError as e:
        print(f"inventory: {e}", file=sys.stderr)


def _sync(hb: HomeBase, cfg: config.Config, event: str, session_id: str, transcript: str) -> None:
    path = Path(transcript) if transcript else claude_code.find_transcript(session_id)
    try:
        if path and path.exists():
            result = sessions.upload(hb, cfg, claude_code.read_session(path))
            if result.status == "not_holder":
                (_dir("not-holder") / session_id).write_text(result.detail)
                return
            if result.status == "skipped":
                return
        hb.post(f"/sessions/{session_id}/events", {"kind": EVENTS[event]})
        (_dir("not-holder") / session_id).unlink(missing_ok=True)
    except HomeBaseError as e:
        if e.status == 0:
            (_dir("pending") / session_id).write_text(f"{event}\n{path or ''}")
        elif e.code == "not_holder":
            (_dir("not-holder") / session_id).write_text(str(e))
        elif e.status != 404:  # 404: nothing uploaded yet for a brand-new Session.
            print(f"{event} {session_id}: {e}", file=sys.stderr)


def settings_snippet(command: str) -> dict:
    hook = lambda event: [{"hooks": [{"type": "command", "command": f"{command} hook {event}"}]}]  # noqa: E731
    return {"hooks": {"SessionStart": hook("session-start"), "Stop": hook("stop"), "SessionEnd": hook("session-end")}}


def install(settings_path: Path, command: str) -> None:
    settings = json.loads(settings_path.read_text()) if settings_path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    for event, entries in settings_snippet(command)["hooks"].items():
        existing = hooks.setdefault(event, [])
        commands = {h.get("command") for e in existing for h in e.get("hooks", [])}
        if entries[0]["hooks"][0]["command"] not in commands:
            existing.extend(entries)
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")


if __name__ == "__main__":
    work(*sys.argv[1:4])
