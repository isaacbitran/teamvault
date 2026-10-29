#!/usr/bin/env python3
"""teamvault refresh for Codex (runs every 15 min + at setup). Keeps a managed block in ~/.codex/AGENTS.md with any
URGENT items first, then the team rules, then the latest briefing.
- only a valid briefing (optionally led by a "🚨 URGENT" section) replaces the cache; failures keep the last good copy
- the managed block is rebuilt every run; only whole-line markers count, so user text is never deleted
- AGENTS.md is written atomically (symlinks and file mode kept) under one lock
- never updates its own code: re-run setup to upgrade. The rules text is refreshed from the server."""
import datetime, fcntl, json, os, pathlib, re, sys, tempfile, urllib.request

HOME = pathlib.Path.home()
CODEX = pathlib.Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
TV = CODEX / "teamvault"
SERVER = json.loads((TV / "config.json").read_text())["server"]
TOKEN = (TV / "token").read_text().strip()
AGENTS, CACHE, META, RULES = CODEX / "AGENTS.md", TV / "briefing.md", TV / "meta.json", TV / "rules.md"
START, END = "<!-- teamvault:start (auto-refreshed, do not edit) -->", "<!-- teamvault:end -->"
MAX = 20000
BLOCK_RE = re.compile(r"(?m)^[ \t]*" + re.escape(START) + r"[ \t]*\n.*?^[ \t]*" + re.escape(END) + r"[ \t]*(?:\n|\Z)", re.S)


def get(path, timeout=8):
    req = urllib.request.Request(SERVER + path, headers={"Authorization": "Bearer " + TOKEN})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def atomic_write(path, text):
    real = path.resolve() if path.is_symlink() else path      # keep a symlinked AGENTS.md a symlink
    mode = real.stat().st_mode & 0o777 if real.exists() else (0o644 if path.name == "AGENTS.md" else 0o600)
    fd, tmp = tempfile.mkstemp(dir=str(real.parent), prefix=".tv-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, str(real))


def escape(text):
    return text.replace("<!--", "<!- -")      # content can never forge a managed-block marker


def strip_blocks(text):
    """Remove complete managed blocks and lone whole-line markers; everything else (user text) stays."""
    text = BLOCK_RE.sub("", text)
    return "\n".join(l for l in text.splitlines() if l.strip() not in (START, END)).rstrip()


def split_urgent(body):
    """'🚨 URGENT …\\n\\n(you are @x)…' → (urgent, briefing). Raises if it isn't a real briefing."""
    i = body.find("(you are @")
    if i < 0 or (i > 0 and not body.startswith("🚨 URGENT")):
        raise ValueError("invalid briefing body")
    return body[:i].strip(), body[i:]


def main():
    TV.mkdir(parents=True, exist_ok=True)
    lk = open(str(TV / ".lock"), "w")
    fcntl.flock(lk, fcntl.LOCK_EX)             # one refresh at a time
    try:
        meta = json.loads(META.read_text())
        if not isinstance(meta, dict):
            raise ValueError
    except Exception:
        meta = {}                              # missing/corrupt metadata never blocks a refresh
    status = "live"
    try:
        body = get("/briefing")
        split_urgent(body)
        atomic_write(CACHE, body)
        meta.update(fetched=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), last_error=None)
    except Exception as e:
        status = "offline"
        meta["last_error"] = "%s %s: %s" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), type(e).__name__, str(e)[:120])
    try:
        r = get("/rules.md")
        if "Team memory" in r and "URGENT" in r:
            atomic_write(RULES, r)
    except Exception:
        pass
    urgent, brief = ("", "(no briefing yet — call team_briefing)")
    if CACHE.exists():
        try:
            urgent, brief = split_urgent(CACHE.read_text())
        except ValueError:
            pass
    if len(brief) > MAX:
        brief = brief[:MAX] + "\n[briefing truncated — call team_briefing for the full version]"
    head = "### Team briefing (fetched %s%s)" % (
        meta.get("fetched", "never"),
        "; OFFLINE since %s — urgent items below may be stale, call team_briefing when back" % meta["last_error"][:16]
        if status == "offline" else "")
    rules = RULES.read_text() if RULES.exists() else ""
    top = escape(urgent) + "\n\n" if urgent else ""
    block = "%s\n%s%s\n%s\n%s\n%s\n" % (START, top, escape(rules), head, escape(brief), END)
    cur = AGENTS.read_text() if AGENTS.exists() else ""
    base = strip_blocks(cur)
    atomic_write(AGENTS, (base + "\n\n" if base else "") + block)
    atomic_write(META, json.dumps(meta))
    lk.close()
    return 0 if status == "live" else 1


if __name__ == "__main__":
    sys.exit(main())
