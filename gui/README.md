# First Mate GUI

A small local web app for chatting with First Mate in a browser instead of a
terminal.

## What it is

`server.py` is a dependency-free Python HTTP server. Each message you send in
the browser runs `claude -p "<message>" --output-format json --resume <id>`
from this repo's root, so that headless session loads this repo's `AGENTS.md`
and behaves as First Mate exactly as an interactive terminal session would.
`--resume` keeps the same conversation going across messages; the session id
is cached in `state/gui-session.json` (gitignored).

This is a separate First Mate session from any terminal session you have
open elsewhere - it doesn't share conversation history with them, though it
reads and can act on the same repo state (backlog, projects, fleet).

Replies render as markdown (headers, lists, code blocks, links). The "Fleet"
indicator in the header polls `/api/fleet` every 5s, which reads
`state/*.meta` directly (read-only) to show how many tasks are currently
fanned out, their project, harness/model, and latest status line - click it
to expand the list. It reflects the fleet of whichever First Mate home this
repo's `state/` belongs to, not just this GUI session.

## Run it

```sh
bin/fm-gui.sh          # starts the server on http://127.0.0.1:8756
bin/fm-gui.sh --open   # also opens it in your default browser
```

Override the port with `FM_GUI_PORT`.

## Safety notes

- Every message runs with `--dangerously-skip-permissions` (this repo's
  default permission posture per `config/claude-permission-mode`), so First
  Mate acts on whatever you send with no per-tool confirmation. Treat this
  chat like an unattended agent, not a sandboxed toy.
- The server only binds to `127.0.0.1`. Do not put it behind a reverse proxy
  or expose the port to your network - anyone who can reach it can run
  arbitrary commands as you in this repo.
- "New conversation" clears the cached session id; it does not stop or undo
  anything the prior conversation already did.

## Limits

Single-user, single conversation at a time, no auth, no streaming (each
message blocks until `claude` returns a full reply), and the message log is
only kept in the browser tab - reloading the page clears it (the underlying
`claude` session and its history are unaffected). Good enough for a local
prototype; not meant to be exposed beyond your own machine.
