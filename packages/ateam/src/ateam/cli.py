"""`ateam`: the A-Team command line."""

import json
import os
import sys
from pathlib import Path
from typing import Annotated

import typer

from ateam import claude_code, config, sessions, worktrees
from ateam.client import HomeBase, HomeBaseError

app = typer.Typer(no_args_is_help=True, add_completion=False, help="A-Team: run agent Sessions across Machines.")
machine_app = typer.Typer(no_args_is_help=True, help="Machines registered with HomeBase.")
app.add_typer(machine_app, name="machine")

JsonOpt = Annotated[bool, typer.Option("--json", help="Machine-readable output.")]


def fail(message: str, code: int = 1, as_json: bool = False, **extra) -> None:
    if as_json:
        typer.echo(json.dumps({"error": message, **extra}))
    else:
        typer.secho(f"error: {message}", fg="red", err=True)
    raise typer.Exit(code)


def client() -> tuple[config.Config, HomeBase]:
    cfg = config.load()
    return cfg, HomeBase(cfg)


def is_agent() -> bool:
    """Commands run by an agent (inside a Harness, or without a terminal) never get interactive prompts."""
    return bool(os.environ.get("CLAUDECODE")) or not sys.stdin.isatty()


# Machines


@machine_app.command("register")
def machine_register(
    name: Annotated[str, typer.Argument(help="Name for this Machine, e.g. macbook or nixbox.")],
    homebase: Annotated[str | None, typer.Option(help="HomeBase URL, e.g. http://nixbox:7420.")] = None,
    root: Annotated[Path | None, typer.Option(help="Workspace Root on this Machine.")] = None,
    dev_node: Annotated[str | None, typer.Option(hidden=True)] = None,
):
    """Register this Machine (bound to its Tailscale device) and save its config."""
    try:
        cfg = config.load()
    except SystemExit:
        if not homebase:
            fail("first registration needs --homebase")
        cfg = config.Config(homebase_url=homebase)
    cfg.homebase_url = homebase or cfg.homebase_url
    cfg.dev_node = dev_node or cfg.dev_node
    root = (root or (Path(cfg.workspace_root) if cfg.workspace_root else Path.home() / "workspace")).resolve()
    if not root.is_dir():
        fail(f"Workspace Root {root} does not exist")
    versions = {claude_code.HARNESS: v} if (v := claude_code.harness_version()) else {}
    try:
        HomeBase(cfg).post("/machines/register", {"name": name, "workspace_root": str(root), "harness_versions": versions})
    except HomeBaseError as e:
        fail(str(e))
    cfg.machine, cfg.workspace_root = name, str(root)
    config.save(cfg)
    sessions.report_inventory(HomeBase(cfg), cfg)
    typer.echo(f"registered {name} (root {root}) with {cfg.homebase_url}")


@machine_app.command("list")
def machine_list(as_json: JsonOpt = False):
    _, hb = client()
    rows = hb.get("/machines")
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    for m in rows:
        typer.echo(f"{m['name']:<16} {m['workspace_root']}")


@machine_app.command("report")
def machine_report(as_json: JsonOpt = False):
    """Send HomeBase this Machine's Inventory: which Worktrees it has (the hooks do this hourly)."""
    cfg, hb = client()
    count = sessions.report_inventory(hb, cfg)
    typer.echo(json.dumps({"worktrees": count}) if as_json else f"reported {count} worktree(s)")


# Sessions


def _location_here(cfg: config.Config) -> tuple[str, str] | None:
    try:
        loc = worktrees.locate(Path.cwd(), cfg.root)
    except worktrees.NotInProject:
        return None
    branch = worktrees.git(loc.worktree, "branch", "--show-current")
    return (loc.project_path, branch) if branch else None


def _state(s: dict) -> str:
    if s.get("tombstoned_at"):
        return "gone"
    if s["is_open"]:
        return "stale" if s.get("stale") else "open"
    return ""


def _print_sessions(rows: list[dict]) -> None:
    if not rows:
        typer.echo("no sessions")
        return
    width = max(len(s["handle"]) for s in rows)
    for s in rows:
        title = (s["title"] or "")[:60]
        typer.echo(
            f"{s['handle']:<{width}}  {s['holder']:<10} {_state(s):<6} {s['last_activity_at'][:16]}  {title}"
        )


@app.command("sessions")
def sessions_cmd(
    query: Annotated[str | None, typer.Argument(help="Handle, part of a Handle, or session ID.")] = None,
    here: Annotated[bool, typer.Option("--here", help="Only Sessions in the current Worktree.")] = False,
    holder: Annotated[str | None, typer.Option(help="Only Sessions held by this Machine.")] = None,
    as_json: JsonOpt = False,
):
    """List Sessions known to HomeBase, most recently active first."""
    cfg, hb = client()
    params = {"q": query, "holder": holder}
    if here:
        where = _location_here(cfg)
        if where is None:
            fail("not inside a Worktree under the Workspace Root", as_json=as_json)
        params["project"], params["branch"] = where
    rows = hb.get("/sessions", **params)
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
    else:
        _print_sessions(rows)


@app.command("import")
def import_cmd(
    session_ids: Annotated[list[str] | None, typer.Argument(help="Only these session IDs.")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be uploaded.")] = False,
    as_json: JsonOpt = False,
):
    """Upload this Machine's Sessions (those inside the Workspace Root) to HomeBase."""
    cfg, hb = client()
    local = claude_code.discover()
    if session_ids:
        local = [s for s in local if s.id in session_ids]
    results = []
    for s in local:
        if dry_run:
            try:
                loc = worktrees.locate(Path(s.cwd), cfg.root) if s.cwd else None
                results.append(sessions.UploadResult(s.id, "would_upload" if loc else "skipped", loc and loc.project_path or "no cwd"))
            except worktrees.NotInProject as e:
                results.append(sessions.UploadResult(s.id, "skipped", str(e)))
            continue
        try:
            results.append(sessions.upload(hb, cfg, s))
        except HomeBaseError as e:
            results.append(sessions.UploadResult(s.id, "error", str(e)))
    if as_json:
        typer.echo(json.dumps([r.__dict__ for r in results], indent=2))
        return
    for r in results:
        if r.status == "skipped" and not dry_run:
            continue
        typer.echo(f"{r.status:<12} {r.handle or r.session_id}  {r.detail}")
    skipped = sum(r.status == "skipped" for r in results)
    if skipped:
        typer.echo(f"{skipped} session(s) skipped: not inside a Project under {cfg.root}")


def _resolve(hb: HomeBase, cfg: config.Config, query: str | None, as_json: bool) -> dict:
    if query:
        rows = hb.get("/sessions", q=query)
    else:
        where = _location_here(cfg)
        if where is None:
            fail("name a session, or run this inside a Worktree", as_json=as_json)
        rows = hb.get("/sessions", project=where[0], branch=where[1])
    if len(rows) == 1:
        return rows[0]
    if not rows:
        fail(f"no session matches {query!r}" if query else "no sessions in this Worktree", as_json=as_json)
    if is_agent() or as_json:
        candidates = [{k: r[k] for k in ("id", "handle", "holder", "title", "last_activity_at")} for r in rows]
        fail("ambiguous; name one of the candidates", code=2, as_json=True, candidates=candidates)
    for i, r in enumerate(rows, 1):
        typer.echo(f"{i:>3}. {r['handle']}  [{r['holder']}] {r['last_activity_at'][:16]}  {(r['title'] or '')[:50]}")
    choice = typer.prompt("which session", type=int)
    if not 1 <= choice <= len(rows):
        fail("no such choice")
    return rows[choice - 1]


@app.command("handoff")
def handoff(
    query: Annotated[str | None, typer.Argument(help="Handle, part of a Handle, or session ID.")] = None,
    to: Annotated[str | None, typer.Option("--to", help="Target Machine (only this Machine for now).")] = None,
    force: Annotated[bool, typer.Option("--force", help="Take an Open Session.")] = False,
    resume: Annotated[bool, typer.Option("--resume/--no-resume", help="Start `claude --resume` afterwards.")] = True,
    as_json: JsonOpt = False,
):
    """Hand a Session off to this Machine and resume it."""
    cfg, hb = client()
    if to and to != cfg.machine:
        fail("pushing a Handoff to another Machine is not built yet; run this on the target", as_json=as_json)
    try:
        session = _resolve(hb, cfg, query, as_json)
        plan = sessions.plan_pull(hb, cfg, session["id"])
        for warning in plan.warnings:
            typer.secho(f"warning: {warning}", fg="yellow", err=True)
        result = hb.post(
            f"/sessions/{session['id']}/handoff",
            {
                "force": force,
                "initiator_kind": "agent" if is_agent() else "human",
                "initiator_session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
            },
        )
        transcript = sessions.pull(hb, cfg, plan)
    except sessions.Refused as e:
        fail(str(e), as_json=as_json)
    except HomeBaseError as e:
        fail(str(e), as_json=as_json, code=3 if e.code in ("open", "agent_force_active") else 1)
    moved = result["handoff"]
    summary = {
        "id": session["id"],
        "handle": session["handle"],
        "from": moved["from_machine"] if moved else cfg.machine,
        "to": cfg.machine,
        "cwd": str(plan.target_cwd),
        "transcript": str(transcript),
        "resume": f"cd {plan.target_cwd} && claude --resume {session['id']}",
        "warnings": plan.warnings,
    }
    if as_json:
        typer.echo(json.dumps(summary, indent=2))
    elif moved:
        typer.echo(f"{session['handle']}: {moved['from_machine']} -> {cfg.machine}")
    else:
        typer.echo(f"{session['handle']}: already held here; refreshed local copy")
    if resume and not as_json and not is_agent():
        os.chdir(plan.target_cwd)
        os.execvp("claude", ["claude", "--resume", session["id"]])
    elif not as_json:
        typer.echo(summary["resume"])


@app.command("handoffs")
def handoffs(
    query: Annotated[str | None, typer.Argument(help="Only this Session.")] = None,
    as_json: JsonOpt = False,
):
    """The Handoff log: who moved which Session where, and when."""
    cfg, hb = client()
    session = _resolve(hb, cfg, query, as_json) if query else None
    rows = hb.get("/handoffs", session=session["id"] if session else None)
    if as_json:
        typer.echo(json.dumps(rows, indent=2))
        return
    for h in rows:
        who = h["initiator_kind"] + (f" ({h['initiator_session'][:8]})" if h["initiator_session"] else "")
        forced = " forced" if h["forced"] else ""
        typer.echo(f"{h['at'][:16]}  {h['session_id'][:8]}  {h['from_machine']} -> {h['to_machine']}{forced}  by {who}")


@app.command("gc")
def gc_cmd(
    apply: Annotated[bool, typer.Option("--apply", help="Delete files; without this only shows what would go.")] = False,
    inactive_days: Annotated[int, typer.Option(help="Collect after this many idle days.")] = 60,
    gone_days: Annotated[int, typer.Option(help="...or this many if the Worktree is gone everywhere.")] = 14,
    show_all: Annotated[bool, typer.Option("--all", help="Also list Sessions that are kept.")] = False,
    as_json: JsonOpt = False,
):
    """Garbage-collect old Session files on HomeBase, keeping Tombstones."""
    cfg, hb = client()
    try:
        sessions.report_inventory(hb, cfg)
    except HomeBaseError as e:
        typer.secho(f"warning: could not report inventory: {e}", fg="yellow", err=True)
    verdicts = hb.request(
        "POST", "/gc", params={"dry_run": not apply, "inactive_days": inactive_days, "gone_days": gone_days}
    ).json()
    if as_json:
        typer.echo(json.dumps(verdicts, indent=2))
        return
    shown = verdicts if show_all else [v for v in verdicts if v["collect"]]
    for v in shown:
        typer.echo(f"{'collect' if v['collect'] else 'keep':<8} {v['handle']}  {v['reason']}")
    collected = sum(v["collect"] for v in verdicts)
    verb = "collected" if apply else "would collect"
    typer.echo(f"{verb} {collected} of {len(verdicts)} session(s)" + ("" if apply else "; pass --apply to delete"))


# Harness integration


@app.command("hook", hidden=True)
def hook(event: Annotated[str, typer.Argument(help="session-start | stop | session-end")]):
    """Called by Claude Code hooks; reads the hook payload on stdin and returns immediately."""
    from ateam import hooks

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    if out := hooks.run_hook(event, payload):
        typer.echo(json.dumps(out))


@app.command("install")
def install(
    print_only: Annotated[bool, typer.Option("--print", help="Show the hooks instead of installing them.")] = False,
):
    """Install the Claude Code hooks that keep HomeBase current."""
    from ateam import hooks

    command = str(Path(sys.argv[0]).resolve())
    if print_only:
        typer.echo(json.dumps(hooks.settings_snippet(command), indent=2))
        return
    settings = claude_code.claude_home() / "settings.json"
    hooks.install(settings, command)
    typer.echo(f"installed A-Team hooks into {settings}")
