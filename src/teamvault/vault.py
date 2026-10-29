"""The team vault on disk: a git repo of Markdown files (Now/Log format). Reads are direct; every write goes through
apply_ops() (or save_meeting()) under one file lock, is linted with the vendored vault-lint, and is
committed by the server with the sender as author. The model never touches git."""
import contextlib
import datetime
import fcntl
import os
import pathlib
import re
import shutil
import subprocess

from . import config
from .vendor import vault_build, vault_lint

VAULT = config.VAULT
RG = shutil.which("rg")
TEMPLATE = pathlib.Path(__file__).parent / "template"
IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
FACT_FILE = re.compile(r"(areas|people)/[a-z0-9-]+\.md")   # the only files ops may append to / edit
GENERATED = ("CONTEXT.md", "INDEX.md", "CLAUDE.md")


def today() -> str:
    return datetime.date.today().isoformat()


def git(*args, check=False, author=None) -> str:
    env = None
    if author:
        env = {**os.environ, "GIT_AUTHOR_NAME": author, "GIT_AUTHOR_EMAIL": f"{author}@teamvault",
               "GIT_COMMITTER_NAME": "teamvault", "GIT_COMMITTER_EMAIL": "server@teamvault"}
    r = subprocess.run(["git", "-C", str(VAULT), *args], capture_output=True, text=True, env=env, timeout=30,
                       stdin=subprocess.DEVNULL)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.strip()[:300]}")
    return r.stdout


@contextlib.contextmanager
def locked():
    config.LOCK.parent.mkdir(parents=True, exist_ok=True)
    with open(config.LOCK, "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _restore_tree() -> None:
    """Back to the last commit, but never delete a transcript: untracked meetings get committed first."""
    (VAULT / ".git" / "index.lock").unlink(missing_ok=True)
    if git("status", "--porcelain", "--", "meetings").strip():
        git("add", "meetings"); git("commit", "-q", "-m", "recovered transcript", author="server")
    git("reset", "-q", "--hard"); git("clean", "-qfd", "-e", "meetings/")


def init() -> None:
    """Create the vault the first time; afterwards keep the librarian rules in sync with the template."""
    if not (VAULT / ".git").exists():
        VAULT.mkdir(parents=True, exist_ok=True)
        shutil.copytree(TEMPLATE, VAULT, dirs_exist_ok=True)
        for d in ("areas", "people", "decisions", "meetings", "research", "reviews"):
            (VAULT / d).mkdir(exist_ok=True)
            (VAULT / d / ".keep").touch()
        git("init", "-q", check=True)
        git("config", "user.name", "teamvault"); git("config", "user.email", "server@teamvault")
        rebuild()
        git("add", "-A"); git("commit", "-q", "-m", "init team vault", check=True, author="server")
        return
    with locked():
        _restore_tree()
        rules = (TEMPLATE / "CLAUDE.md").read_text()
        if (VAULT / "CLAUDE.md").read_text() != rules:
            (VAULT / "CLAUDE.md").write_text(rules)
            git("add", "CLAUDE.md"); git("commit", "-q", "-m", "update librarian rules", author="server")


# ---------- reads ----------

def safe(rel: str) -> pathlib.Path:
    rel = rel.strip().lstrip("/")
    p = (VAULT / rel).resolve()
    if not str(p).startswith(str(VAULT.resolve()) + "/") or ".git" in p.parts or p.is_symlink():
        raise ValueError("path outside the team vault")
    return p


def as_data(text: str) -> str:
    """Tool output is reference data, never instructions; images stripped, fence can't be closed from inside."""
    body = re.sub(r"</?\s*team_memory\s*>", "[tag removed]", IMAGE_RE.sub("[image removed]", text), flags=re.I)
    return f"<team_memory>\n{body}\n</team_memory>"


def read(rel: str, offset: int = 0, limit: int = 20000) -> str:
    if not rel.strip():
        raise ValueError("path is required (e.g. 'areas/product.md')")
    p = safe(rel)
    if p.is_dir():
        names = sorted(x.name for x in p.iterdir() if not x.name.startswith("."))
        raise ValueError(f"{rel} is a folder; files in it: {', '.join(names) or '(empty)'}")
    if not p.is_file():
        raise ValueError(f"no such file: {rel}")
    t = p.read_text(errors="replace")
    if offset < 0 or offset > len(t):
        raise ValueError(f"offset must be between 0 and {len(t)} (file length in characters)")
    end = min(offset + limit, len(t))
    tail = f"\n[chars {offset}–{end} of {len(t)}; " + (f"continue with offset={end}]" if end < len(t) else "end of file]")
    return t[offset:end] + (tail if offset or end < len(t) else "")


def norm_since(since: str) -> str | None:
    """git-ready 'since' or None if invalid."""
    s = since.strip().lower()
    if s == "today":
        return today() + " 00:00"   # git treats a bare date as "that date at the current time"
    if s == "yesterday" or re.fullmatch(r"\d{1,4}\s+(minute|hour|day|week)s?\s+ago", s):
        return s
    try:
        return datetime.date.fromisoformat(s).isoformat() + " 00:00"
    except ValueError:
        return None


STOP = set("the a an and or of to in on for is are was were be by with at from as it this that what which who how "
           "when where why do does did we our you your can could should would much many any all about into than "
           "el la los las un una de del y o en por para con que qué es son se lo al como cómo cuál cuánto".split())  # en + es


def _grep(patterns: list[str], per_file: int) -> list[str]:
    if RG:
        r = subprocess.run([RG, "-i", "-n", "-F", "--no-config", "-g", "*.md", "-g", "!CONTEXT.md", "-g", "!INDEX.md",
                            "-g", "!CLAUDE.md", "-m", str(per_file), *[a for p in patterns for a in ("-e", p)], "--", "."],
                           cwd=VAULT, capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL)
        return [l[2:] if l.startswith("./") else l for l in r.stdout.splitlines()]
    out = []
    for p in sorted(VAULT.rglob("*.md")):
        if ".git" in p.parts or p.name in GENERATED: continue
        for i, l in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if any(q.lower() in l.lower() for q in patterns):
                out.append(f"{p.relative_to(VAULT)}:{i}:{l}")
    return out


def search(query: str, limit: int = 30) -> str:
    """Exact phrase first; if that finds little and the query has several words, rank lines by how many words hit."""
    curated = ("areas/", "now.md", "decisions/")    # team facts beat raw research; dated facts first
    lines = sorted(_grep([query], 5), key=lambda l: (not l.startswith(curated), not re.search(r":\d+:- \[\d{4}-", l)))
    terms = [t for t in re.findall(r"[\w\-]+", query.lower()) if len(t) > 2 and t not in STOP]
    note = ""
    if len(lines) < 5 and terms and terms != [query.lower().strip()]:
        seen = set(lines)
        need = 1 if len(terms) == 1 else 2 if len(terms) <= 3 else (len(terms) + 1) // 2
        pats = [re.compile(r"\b" + re.escape(t)) for t in terms]
        scored = []
        for l in _grep(terms, 50):
            path, _, text = l.partition(":")
            n = sum(bool(p.search(text.lower())) for p in pats)
            if n >= need and l not in seen:   # curated team facts beat raw research
                scored.append((-(n * 10 + (5 if path.startswith(curated) else 0)), l))
        scored.sort(key=lambda x: x[0])
        if scored:
            note = f"(also lines matching ≥{need} of: {', '.join(terms)})\n"
        lines += [l for _, l in scored]
    body = "\n".join(l[:400] + (" …[clipped; team_read the file for the full line]" if len(l) > 400 else "")
                     for l in lines[:limit])
    return (note + body) if body else "(no matches)"


def recent(since: str = "2 days ago", max_commits: int = 40) -> str:
    """Who filed what, when: only the fact lines each change added (Log lines, Now bullets, actions)."""
    out = git("log", f"--since={since}", f"-{max_commits}", "--date=format:%Y-%m-%d %H:%M", "--format=@@%ad · %an · %s",
              "-p", "-U0", "--", ".", *[f":!{g}" for g in GENERATED])
    lines, cur, shown = [], None, 0
    for l in out.splitlines():
        if l.startswith("@@") and " · " in l and not l.startswith("@@ "):
            lines.append("\n" + l[2:]); shown = 0
        elif l.startswith("+++ b/"):
            cur = l[6:]
        elif l.startswith("+- ") and shown < 8:
            lines.append(f"  {cur}: {l[1:][:300]}" + (" …[clipped]" if len(l) > 301 else "")); shown += 1
    return ("\n".join(lines).strip() or f"(no changes since {since})")


def briefing(user: str, extra: str = "", limit: int = 8500) -> str:
    """Newest first, so it survives truncation: header → last 48h by who → priorities/actions → Now per topic."""
    vault_lint.TODAY = today()
    rec = recent("48 hours ago")
    if len(rec) > 3000:
        rec = rec[:3000] + "\n[more — call team_recent]"
    _, nowbody = vault_build.split(VAULT / "now.md") if (VAULT / "now.md").exists() else ({}, "")
    files = sorted(vault_build.load_files(), key=lambda f: f["updated"], reverse=True)
    topics = "\n".join(f"### {f['path']} — {f['desc']}\n" + "\n".join(f["now"]) + "\n" for f in files if f["now"])
    head = f"(you are @{user})\n{extra}\n## Last 48h, by who\n{rec}\n\n# Now\n{nowbody.strip()}\n\n## Current truth (newest first)\n"
    room = max(1500, limit - len(head))
    if len(topics) > room:
        topics = topics[:room] + "\n[more topics — team_search / team_read areas/<file>]"
    return head + topics


# ---------- writes ----------

def rebuild() -> None:
    """Regenerate CONTEXT.md + INDEX.md with the vault's own builder (deterministic)."""
    files = vault_build.load_files()
    (VAULT / "CONTEXT.md").write_text(vault_build.build_context(files, vault_build.BUDGET))
    (VAULT / "INDEX.md").write_text(vault_build.build_index(files))


def _lint(paths) -> list[str]:
    vault_lint.TODAY = today()
    errs = []
    for p in paths:
        e, _, _ = vault_lint.lint_file(p)
        errs += [f"{p.relative_to(VAULT)}: {x}" for x in e]
    return errs


def _clean(s) -> str:
    """One line of plain text: no newlines/markers that could forge Log lines, headings or frontmatter."""
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s.lstrip("#-").strip()


def _fact_file(rel: str) -> pathlib.Path:
    if not FACT_FILE.fullmatch(rel or ""):
        raise ValueError(f"ops may only edit areas/<slug>.md or people/<slug>.md (got {rel!r})")
    p = safe(rel)
    if not p.exists():
        raise ValueError(f"{rel} does not exist (create_file first)")
    return p


def _now_edit(p: pathlib.Path, fn) -> None:
    t = p.read_text()
    m = re.search(r"(## Now\n)(.*?)(\n*## Log)", t, flags=re.S)
    if not m:
        raise ValueError(f"{p.name}: no ## Now / ## Log sections")
    bullets = [l for l in m.group(2).splitlines() if l.startswith("- ")]
    bullets = fn(bullets)
    p.write_text(t[:m.start(2)] + "\n".join(bullets) + "\n\n## Log" + t[m.end(3):])


def _apply_one(op: dict, user: str, source: str, touched: set) -> None:
    kind = op.get("op")
    if kind == "append_log":
        p = _fact_file(op.get("file"))
        line = f"- [{today()}] {_clean(op['fact'])} · {source} · @{user}"
        p.write_text(p.read_text().rstrip("\n") + "\n" + line + "\n"); touched.add(p)
    elif kind == "now_add":
        p = _fact_file(op.get("file"))
        _now_edit(p, lambda b: b + [f"- {_clean(op['bullet'])}"]); touched.add(p)
    elif kind == "now_replace":
        p = _fact_file(op.get("file"))
        old = _clean(op["old_contains"])
        def repl(b):
            hits = [i for i, x in enumerate(b) if old and old in x]
            if len(hits) != 1:
                raise ValueError(f"now_replace: {len(hits)} Now bullets match {old!r}")
            b[hits[0]] = f"- {_clean(op['bullet'])}"
            return b
        _now_edit(p, repl); touched.add(p)
    elif kind == "supersede":
        p = _fact_file(op.get("file"))
        old = _clean(op["old_contains"])
        lines = p.read_text().splitlines()
        hits = [i for i, l in enumerate(lines) if old and old in l and l.startswith("- [") and "superseded" not in l]
        if len(hits) != 1:
            raise ValueError(f"supersede: {len(hits)} matching lines in {op['file']}")
        lines[hits[0]] += f" — superseded {today()}, see log {today()}"
        p.write_text("\n".join(lines) + "\n"); touched.add(p)
    elif kind == "create_file":
        rel = op.get("file") or ""
        if not FACT_FILE.fullmatch(rel):
            raise ValueError("create_file: only areas/<slug>.md or people/<slug>.md")
        p = safe(rel)
        if p.exists():
            raise ValueError(f"{rel} already exists")
        aliases = ", ".join(_clean(a).replace(",", " ") for a in op.get("aliases", []) if _clean(a))
        now = "\n".join(f"- {_clean(b)}" for b in op.get("now", []) if _clean(b))
        p.write_text(f"---\nname: {p.stem}\ndescription: {_clean(op['description']).replace('#', '')}\n"
                     + (f"aliases: [{aliases}]\n" if aliases else "")
                     + f"updated: {today()}\n---\n## Now\n{now}\n\n## Log\n")
        touched.add(p)
    elif kind == "add_action":
        p = VAULT / "now.md"
        date = datetime.date.fromisoformat(op.get("date") or today()).isoformat()
        p.write_text(p.read_text().rstrip("\n") + f"\n- [ ] {date} {_clean(op['text'])} · @{user}\n"); touched.add(p)
    elif kind == "create_decision":
        slug = re.sub(r"[^a-z0-9-]+", "-", _clean(op["slug"]).lower()).strip("-")[:60] or "decision"
        p = safe(f"decisions/{today()}-{slug}.md")
        if p.exists():
            raise ValueError("decision already exists")
        p.write_text(f"---\nname: {p.stem}\ndescription: {_clean(op['description']).replace('#', '')}\ndecided: {today()}\n"
                     f"status: active\n---\n**Decided:** {_clean(op['decided'])} (@{user})\n"
                     f"**Why:** {_clean(op['why'])}\n**Alternatives:** {_clean(op['alternatives'])}\n"
                     f"**Revisit if:** {_clean(op['revisit_if'])}\n")
        touched.add(p)
    else:
        raise ValueError(f"unknown op {kind!r}")


def already_applied(item_id: str) -> bool:
    return bool(git("log", "--oneline", "-1", "--fixed-strings", f"--grep=#{item_id}:").strip())


def apply_ops(user: str, ops: list[dict], source: str, label: str) -> tuple[bool, str]:
    """All-or-nothing: apply, lint, commit (label carries #item-id for idempotency); on error restore the tree."""
    with locked():
        if git("status", "--porcelain").strip():
            _restore_tree()
        touched: set = set()
        try:
            for op in ops:
                _apply_one(op, user, source, touched)
            errs = _lint(touched)
            if errs:
                raise ValueError("; ".join(errs)[:1200])
            rebuild()
            git("add", "-A")
            files = ", ".join(sorted(str(p.relative_to(VAULT)) for p in touched))
            git("commit", "-q", "-m", f"@{user} {label}: {files}", check=True, author=user)
            return True, files
        except Exception as e:
            _restore_tree()
            return False, str(e)


def save_meeting(user: str, title: str, date: str, text: str) -> str:
    title = _clean(title)[:200] or "meeting"
    date = datetime.date.fromisoformat(date).isoformat()
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "meeting"
    with locked():
        rel, n = f"meetings/{date}-{slug}.md", 2
        while safe(rel).exists():
            rel, n = f"meetings/{date}-{slug}-{n}.md", n + 1
        p = safe(rel)
        p.write_text(f"---\nname: {p.stem}\ndescription: {title.replace('#', '')}\ndate: {date}\n"
                     f"source: uploaded by @{user}\n---\n## Transcript (verbatim)\n\n{text.strip()}\n")
        try:
            git("add", rel)
            git("commit", "-q", "-m", f"@{user} transcript: {rel}", check=True, author=user)
        except Exception as e:
            git("reset", "-q", "--", rel); p.unlink(missing_ok=True)
            raise ValueError(f"could not save the transcript, try again ({e})")
    return rel
