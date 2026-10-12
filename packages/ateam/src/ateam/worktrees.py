"""Finding Projects and Worktrees on disk.

A Project is located by its directory relative to the Workspace Root. With the `wt` layout the
Project directory holds sibling Worktrees (`itsmbench-core/main`, `itsmbench-core/exp-batch-one`);
a plain clone is its own Project directory. Worktrees are identified by branch, never by directory name.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

PRIMARY_DIR_NAMES = ("main", "master")


class NotInProject(Exception):
    pass


@dataclass(frozen=True)
class Location:
    project_path: str  # relative to the Workspace Root
    project_remote: str | None
    worktree: Path  # absolute directory of the Worktree on this Machine
    subdir: str  # where inside the Worktree, "" for its top


def git(cwd: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=10)
    except OSError:
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def project_dir_for(primary_worktree: Path) -> Path:
    """`wt` keeps the primary checkout in `<project>/main`; otherwise the checkout is the Project."""
    if primary_worktree.name in PRIMARY_DIR_NAMES:
        return primary_worktree.parent
    return primary_worktree


def locate(cwd: Path, root: Path) -> Location:
    cwd, root = cwd.resolve(), root.resolve()
    if not cwd.is_relative_to(root):
        raise NotInProject(f"{cwd} is outside the Workspace Root {root}")
    if not cwd.is_dir():
        raise NotInProject(f"{cwd} no longer exists")
    top = git(cwd, "rev-parse", "--show-toplevel")
    common = git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not top or not common:
        raise NotInProject(f"{cwd} is not inside a git worktree")
    worktree = Path(top).resolve()
    primary = Path(common).resolve().parent
    project = project_dir_for(primary)
    if not project.is_relative_to(root) or project == root:
        raise NotInProject(f"{cwd} is not inside a Project under {root}")
    return Location(
        project_path=project.relative_to(root).as_posix(),
        project_remote=git(worktree, "remote", "get-url", "origin"),
        worktree=worktree,
        subdir=cwd.relative_to(worktree).as_posix() if cwd != worktree else "",
    )


def repo_dir(project_dir: Path) -> Path | None:
    if (project_dir / ".git").exists():
        return project_dir
    for name in PRIMARY_DIR_NAMES:
        if (project_dir / name / ".git").exists():
            return project_dir / name
    for child in sorted(project_dir.iterdir()) if project_dir.is_dir() else []:
        if (child / ".git").exists():
            return child
    return None


def find_worktree(project_dir: Path, branch: str) -> Path | None:
    repo = repo_dir(project_dir)
    if repo is None:
        return None
    listing = git(repo, "worktree", "list", "--porcelain") or ""
    path = None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree "))
        elif line == f"branch refs/heads/{branch}" and path is not None:
            return path
    return None
