# teamvault

A team memory system for connecting any number of AI agents, on any platform, to one shared memory. Claude Code, Codex, or any client that speaks MCP connects to the same server, so every agent reads the same current facts. The memory is a git repo of plain Markdown files, so every change has an author and a commit.

Agents never write files directly. A shared fact goes into a queue, a librarian model reads the vault and proposes edits as JSON, and the server lints those edits and commits them, or rolls them back if they break the format. A regex check runs before anything touches disk and refuses secrets, compensation details and instruction-shaped text.

![teamvault demo](docs/demo.gif)

## Quickstart (local demo, no API key)

Requires [uv](https://docs.astral.sh/uv/) and git.

```bash
git clone https://github.com/isaacbitran/teamvault && cd teamvault
uv run teamvault demo
```

This seeds a small example vault (a fictional bakery team), mints tokens for two demo users, `maya` and `theo`, and serves on `http://127.0.0.1:8790`. The demo uses the model-free librarian, which files each shared fact verbatim into `areas/unsorted.md`. To use the real librarian, run `TEAMVAULT_LIBRARIAN=claude uv run teamvault demo` with Claude Code installed and logged in.

Connect Claude Code (the demo prints these commands with real tokens):

```bash
claude mcp add --transport http team http://127.0.0.1:8790/mcp --header "Authorization: Bearer <maya's token>"
```

Or call it from Python:

```bash
uv run python examples/client.py http://127.0.0.1:8790 <token> team_search '{"query": "flour"}'
```

## Connecting agents

Each person gets their own token from the admin (`teamvault mint <name>`). The server hosts a setup script for each client, so a teammate doesn't need a copy of the repo.

**Claude Code.** `curl -fsSL https://<your-server>/setup-claude.sh -o /tmp/tv.sh && sh /tmp/tv.sh` adds the MCP server, a SessionStart hook that loads the team briefing, a UserPromptSubmit hook that checks for urgent items before every message, and a short rule in `~/.claude/CLAUDE.md` (skip it with `TEAMVAULT_NO_CLAUDE_MD=1`).

**Codex.** `curl -fsSL https://<your-server>/setup-codex.sh -o /tmp/tv.sh && sh /tmp/tv.sh` adds the server to `~/.codex/config.toml` and keeps a managed block in `~/.codex/AGENTS.md` with the rules and the latest briefing, refreshed every 15 minutes by launchd (macOS) or cron.

**Anything else.** Point any MCP client that supports streamable HTTP at `https://<your-server>/mcp` with an `Authorization: Bearer <token>` header. The server's MCP instructions tell the agent when to share and how to treat what it reads.

## Tools

| Tool | What it does |
|---|---|
| `team_briefing` | Urgent items, priorities, what changed in the last 48 hours and by whom, and the current truth per topic. |
| `team_search` | Exact-phrase search over the vault, then keyword matching if the phrase finds little. Team facts rank above raw documents. |
| `team_read` | Reads one file, paged in 20,000-character chunks. |
| `team_recent` | The fact lines each commit added since a time, plus the status of your own queued shares. |
| `team_share` | Queues up to 25 facts. Returns immediately with a receipt; the librarian files them within about a minute. `urgent=true` also sends them as urgent items. |
| `team_transcript` | Saves a meeting transcript verbatim, then the librarian extracts up to 15 facts per run and queues the rest. |
| `team_ack` | Marks urgent items as seen. |

Plain HTTP endpoints for hooks and scripts: `GET /briefing`, `GET /urgent`, `PUT /upload?title=..&date=..` (long transcripts), `GET /health`.

## How it works

```
 Claude Code ─┐                         ┌─ privacy check (regex, before disk)
 Codex ───────┼─ MCP over HTTPS ─ server┤     withhold / reject / flag for review
 any client ──┘   (per-user token)      │
                                        ├─ spool/pending/<id>.json   (team_share returns here)
                                        │
                                        └─ librarian worker
                                              headless Claude Code, read-only tools (Read, Grep, Glob)
                                              → JSON ops: append_log, now_replace, supersede, create_file, ...
                                              → server applies ops under a file lock
                                              → lint → commit as @user   (lint fails: roll back, retry once)
                                              → receipt to the sender
```

The vault is a folder of Markdown files with a fixed shape:

- `now.md` holds priorities and dated actions.
- `areas/<topic>.md` and `people/<name>.md` each have a `## Now` section (the current truth, at most 12 bullets) and a `## Log` section (dated, append-only lines with a source and an author).
- `decisions/` holds one file per decision with Decided, Why, Alternatives and Revisit if.
- `meetings/` holds verbatim transcripts and is never edited.

When a fact changes, the old Log line is marked superseded instead of deleted, and the Now bullet is rewritten in the same commit. The lint rejects a Now change that has no new Log line, an edited or removed Log line, a future-dated Log line, and a broken `see <path>` pointer. `CONTEXT.md` and `INDEX.md` are regenerated on every write. `teamvault weekly` writes a review file listing the week's changes, Now bullets that a newer Log line contradicts, and actions more than 7 days overdue. It only adds a file and never rewrites anything.

**Urgent items.** `team_share(..., urgent=true)` puts the fact on a list for each recipient. Until a recipient acknowledges it, every tool reply and every briefing they get starts with it, and the Claude Code hook injects it before each message. A fact that the privacy check flags as possibly personal is filed through the librarian but not sent as urgent.

## Security model and limits

What it does:

- Each person has their own random token. The server stores only its SHA-256 hash; tokens expire (90 days by default) and can be revoked. Identity comes from the token, never from a tool argument.
- Requests with an `Origin` header are refused, so a web page in someone's browser can't call the server. DNS-rebinding protection is on.
- The librarian runs with read-only tools, no session persistence, and an environment stripped to `PATH`, `HOME`, locale and its own credential. It returns proposals; the server is the only thing that writes files or runs git.
- Ops can only edit `areas/` and `people/` files, append dated actions to `now.md`, and create new decision files. Every field is flattened to one line, so a fact can't forge a Log line, a heading or frontmatter.
- Everything a tool returns is wrapped in a `<team_memory>` fence and described to the agent as data, not instructions. Invisible Unicode (tag characters, zero-width and bidi controls) is stripped from input.
- Every write is a git commit with the sender as author. The deploy examples include a nightly `git bundle` backup.

What it doesn't do:

- Everyone with a token can read the whole vault. There are no per-file permissions.
- The privacy check is a regex backstop. It catches common phrasings of compensation talk, API keys and prompt injection, not every possible one. The real boundary is the rule each client follows about what to share, and the librarian's review.
- Prompt injection through shared facts is reduced, not solved. Instruction-shaped text is rejected, and tool output is fenced as data, but a determined attacker with a token can still try to influence other people's agents.
- The librarian currently runs through the Claude Code CLI, so filing needs a Claude login or an Anthropic API key. Without one, use `TEAMVAULT_LIBRARIAN=naive`.
- One server process serves one team, with a single writer under a file lock. It is built for a small team, not for many concurrent writers.
- Search is grep-based (uses `ripgrep` if installed), not semantic.
- Server and client scripts assume macOS or Linux.

## Running your own server

```bash
cp .env.example teamvault.env      # edit: TEAMVAULT_PUBLIC_URL, TEAMVAULT_LIBRARIAN, credentials
uv sync
uv run teamvault init              # creates the vault (a git repo) under TEAMVAULT_DATA
uv run teamvault mint alice        # prints alice's token once
uv run teamvault serve
```

Keep the server on `127.0.0.1` and put HTTPS in front of it. `deploy/` has an example Caddy site block, a hardened systemd unit, and timers for the weekly review and nightly backup. For the librarian on a headless server, run `claude setup-token` on your own machine and put the result in `CLAUDE_CODE_OAUTH_TOKEN`, or set `ANTHROPIC_API_KEY`. `TEAMVAULT_DAILY_CAP` limits librarian runs per day (default 60).

## Tests

```bash
uv run pytest
```

20 tests: the privacy check, the vault write path (lint, rollback, path and newline guards), and end-to-end tests that start a real server and call it with the MCP Python client (auth, search, sharing, withholding, urgent items and acknowledgment).

## Roadmap

- Per-folder visibility, so some files are readable only by some people.
- A librarian backend that calls a model API directly, instead of the Claude Code CLI.
- OAuth sign-in for MCP clients that support it, instead of pasted tokens.
- Map-first reads: a short topic directory in the briefing and section-level `team_read` for large files.
- Offsite backups.

Related: [agent-memory](https://github.com/isaacbitran/agent-memory) has the single-agent memory rules and lint this format grew out of.

## License

MIT. `src/teamvault/vendor/` contains the vault builder and linter, adapted from my own vault tooling under the same license.
