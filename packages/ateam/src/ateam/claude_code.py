"""Claude Code Harness adapter (ADR 0001).

Reads Sessions out of Claude Code's own storage and writes them back on Handoff. The files that
make up a Session, under the names HomeBase stores them by:

    transcript.jsonl      <claude>/projects/<encoded cwd>/<id>.jsonl
    session/<rel>         <claude>/projects/<encoded cwd>/<id>/<rel>   (subagents, tool results, ...)
    file-history/<rel>    <claude>/file-history/<id>/<rel>             (checkpoint backups)
"""

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HARNESS = "claude-code"
TRANSCRIPT = "transcript.jsonl"


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def encode_project_dir(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def harness_version() -> str | None:
    try:
        out = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"\d+\.\d+\.\d+", out)
    return match.group(0) if match else None


def version_tuple(version: str | None) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", version or ""))


@dataclass
class LocalSession:
    id: str
    transcript: Path
    cwd: str | None
    branch: str | None
    version: str | None
    title: str | None
    created_at: str | None
    last_activity_at: str | None

    @property
    def session_dir(self) -> Path:
        return self.transcript.with_suffix("")

    def files(self) -> dict[str, Path]:
        """Stored name -> local file, for every file that belongs to this Session."""
        files = {TRANSCRIPT: self.transcript}
        for base, prefix in ((self.session_dir, "session"), (claude_home() / "file-history" / self.id, "file-history")):
            if base.is_dir():
                for path in sorted(p for p in base.rglob("*") if p.is_file()):
                    files[f"{prefix}/{path.relative_to(base).as_posix()}"] = path
        return files


def read_session(transcript: Path) -> LocalSession:
    cwd = branch = version = title = first_ts = last_ts = None
    with open(transcript, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            # Claude Code files the Session under its launch directory, so keep the first cwd. The branch
            # is the last one seen: a checkout can switch branches mid-Session.
            cwd = cwd or entry.get("cwd")
            branch = entry.get("gitBranch") or branch
            version = entry.get("version") or version
            title = entry.get("customTitle") or entry.get("aiTitle") or title
            if ts := entry.get("timestamp"):
                first_ts = first_ts or ts
                last_ts = ts
    return LocalSession(transcript.stem, transcript, cwd, branch, version, title, first_ts, last_ts)


def find_transcript(session_id: str) -> Path | None:
    matches = list((claude_home() / "projects").glob(f"*/{session_id}.jsonl"))
    return max(matches, key=lambda p: p.stat().st_mtime) if matches else None


def discover() -> list[LocalSession]:
    """Every top-level Session transcript on this Machine (subagent transcripts live inside sessions)."""
    transcripts = (claude_home() / "projects").glob("*/*.jsonl")
    return [read_session(t) for t in sorted(transcripts)]


def is_rewritable(name: str) -> bool:
    return name == TRANSCRIPT or (name.startswith("session/") and name.endswith((".jsonl", ".json")))


def rewrite(data: bytes, replacements: list[tuple[str, str]]) -> bytes:
    """Replace path prefixes in one pass, longest first, so a replacement is never rewritten again.

    Only whole path prefixes match: `/Users/j/workspace` is not rewritten inside `/Users/j/workspace2`.
    """
    mapping = {old.encode(): new.encode() for old, new in replacements if old and old != new}
    if not mapping:
        return data
    alternatives = b"|".join(re.escape(old) for old in sorted(mapping, key=len, reverse=True))
    # A path ends at `/`, a quote, whitespace, a backslash (escaped JSON inside JSON) or the end.
    pattern = re.compile(b"(" + alternatives + rb""")(?=[/"'\s\\]|$)""")
    return pattern.sub(lambda m: mapping[m.group(1)], data)


def materialize(session_id: str, cwd: str, files: dict[str, bytes], backup_root: Path) -> Path:
    """Write a Session into Claude Code's storage so `claude --resume` finds it from `cwd`.

    Any local copy is moved under `backup_root` first, so turns that never reached HomeBase are kept.
    """
    home = claude_home()
    project_dir = home / "projects" / encode_project_dir(cwd)
    transcript = project_dir / f"{session_id}.jsonl"
    targets = {
        TRANSCRIPT: transcript,
        "session": project_dir / session_id,
        "file-history": home / "file-history" / session_id,
    }
    stamp = time.strftime("%Y%m%dT%H%M%S")
    existing = [*(home / "projects").glob(f"*/{session_id}.jsonl"), *(home / "projects").glob(f"*/{session_id}")]
    existing += [targets["file-history"]] if targets["file-history"].exists() else []
    for path in existing:
        dest = backup_root / stamp / path.relative_to(home)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), dest)
    for name, data in files.items():
        if name == TRANSCRIPT:
            path = transcript
        else:
            prefix, _, rel = name.partition("/")
            path = targets[prefix] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return transcript
