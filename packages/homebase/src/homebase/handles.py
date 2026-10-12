"""Handles: `<project>/<worktree>/<6 hex>` names for Sessions, and resolving what a human or agent typed."""

import re

ID_CHARS = 6


def worktree_slug(branch: str) -> str:
    """A branch as it appears in a Handle: `/` would clash with the Handle separator."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", branch).strip("-")
    return slug or "detached"


def project_name(project_path: str, other_project_paths: list[str]) -> str:
    """Shortest trailing slice of the Project's path that no other Project shares."""
    parts = project_path.strip("/").split("/")
    others = [p.strip("/").split("/") for p in other_project_paths if p != project_path]
    for n in range(1, len(parts) + 1):
        tail = parts[-n:]
        if not any(o[-n:] == tail for o in others):
            return "/".join(tail)
    return "/".join(parts)


def make_handle(session_id: str, project_path: str, other_project_paths: list[str], branch: str) -> str:
    short = session_id.replace("-", "")[:ID_CHARS]
    return f"{project_name(project_path, other_project_paths)}/{worktree_slug(branch)}/{short}"


def matches(query: str, session_id: str, handle: str) -> bool:
    """Whether `query` names this Session: full ID, ID prefix, the Handle, or a trailing/leading part of it."""
    q = query.strip().strip("/")
    if not q:
        return False
    if q == session_id or q == handle:
        return True
    if re.fullmatch(r"[0-9a-f-]{4,}", q) and session_id.startswith(q):
        return True
    # `exp-batch-one/19c3d0` or `itsmbench-core/exp-batch-one`: whole path segments only.
    return handle.endswith("/" + q) or handle.startswith(q + "/")
