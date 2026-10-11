# HomeBase observes harness storage instead of replacing it

HomeBase holds the canonical copy of every Session. Each Machine keeps the Harness's own storage (`~/.claude/projects`, `~/.claude/file-history`) untouched. A per-Harness adapter uploads appends as they happen, using hooks, and writes Sessions back into native storage on Handoff, rewriting paths between Workspace Roots at that point. We chose this over taking over `~/.claude` with a symlink or mount because Claude Code's on-disk format is internal and changes between releases. An adapter that only reads and writes the files fails in one place we control. The same adapter seam is how other Harnesses will plug in.

## Consequences

- HomeBase stores transcripts exactly as the Harness wrote them, with the source Machine's absolute paths left in. Path rewriting happens only when a Session is written onto a Machine.
- Uploads only ever append to what HomeBase already has. Normalising transcripts on the way in would break that.
