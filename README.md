# A-Team
> An opinionated personal software factory

## Components

1. HomeBase: the server that tracks everything

## Session Handoff (v1)

Move a Claude Code Session between Machines. Vocabulary lives in [CONTEXT.md](CONTEXT.md), decisions in [docs/adr](docs/adr).

```sh
# HomeBase (on the tailnet; identifies callers with `tailscale whois`)
uv run homebase --host <tailnet-ip> --port 7420

# each Machine, once
uv run ateam machine register macbook --homebase http://<tailnet-ip>:7420 --root ~/workspace
uv run ateam install            # Claude Code hooks: upload after every turn (use --print to inspect first)
uv run ateam import             # upload existing Sessions under the Workspace Root

# day to day
uv run ateam sessions [--here]  # Handles look like project/worktree/6hex
uv run ateam handoff ateam/feat-init/9e3cee   # pull here, rewrite paths, `claude --resume`
uv run ateam handoffs           # who moved what, when
```

Layout: `packages/homebase` (FastAPI + SQLite server), `packages/ateam` (CLI and Claude Code adapter). Tests: `uv run pytest`.
