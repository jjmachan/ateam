# A-Team

A-Team is a personal software factory: a set of tools that let agent sessions from different harnesses and models run and cooperate across many machines. HomeBase is its coordination server.

## Language

### Infrastructure

**HomeBase**:
The central A-Team server that records every Machine, Project and Session and holds the canonical copy of each Session.
_Avoid_: server, hub, coordinator

**Machine**:
A computer on the tailnet, registered with HomeBase under a name you choose and bound to its Tailscale device, on which agent sessions run.
_Avoid_: system, host, device, node

**Workspace Root**:
The directory on a Machine under which all Projects live; the same Project sits at the same relative path under every Machine's Workspace Root.
_Avoid_: home, base dir

### Work

**Project**:
A single codebase (one git repository), located by its directory relative to the Workspace Root and checked out as one or more Worktrees.
_Avoid_: repo, folder, directory

**Worktree**:
One checkout of a Project, identified by its branch; its directory name is only how it happens to be laid out on a given Machine.
_Avoid_: checkout, clone, branch folder

**Harness**:
The agent program that produces a Session, such as Claude Code.
_Avoid_: agent, tool, client

**Session**:
One continuous conversation with an agent in a Harness, started inside a Worktree and identified by the Harness's own session ID. Conversations started outside a Workspace Root are not Sessions.
_Avoid_: transcript, conversation, chat

**Handle**:
A Session's short human-readable name, `<project>/<worktree>/<6 hex of session ID>`, fixed when the Session is created; any unique suffix of it also identifies the Session.
_Avoid_: name, slug, alias, short ID

### Movement

**Holder**:
The single Machine on which a Session may currently be continued.
_Avoid_: owner, location, active machine

**Open**:
A Session that its Holder is currently running; an Open Session cannot be handed off without forcing.
_Avoid_: live, active, running

**Stale**:
An Open Session whose Holder has reported no activity for 15 minutes, presumed abandoned (e.g. after a crash).
_Avoid_: dead, orphaned, zombie

**Handoff**:
Moving a Session so that a different Machine becomes its Holder.
_Avoid_: sync, transfer, migrate, move

**Fork**:
Copying a Session into a new, independent Session that continues separately from the original. (Not yet built.)
_Avoid_: clone, branch, copy

### Retention

**Inventory**:
The set of Worktrees a Machine reports having, for the Projects HomeBase knows; a Machine with no Inventory is assumed to have every Worktree.
_Avoid_: manifest, scan, state

**Tombstone**:
The record that a Session existed, kept on HomeBase after its transcript has been garbage-collected.
_Avoid_: archive, stub
