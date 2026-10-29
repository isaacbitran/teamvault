"""Generate CONTEXT.md (the current truth, within a byte budget) and INDEX.md (where everything is) for a vault.
Deterministic: same tree + same git state => byte-identical output. Never hand-edit the outputs.

Vendored from the author's personal vault tooling (MIT, Isaac Bitran); trimmed for teamvault. VAULT_DIR must be set."""
import os, re, subprocess, sys, pathlib, shutil

VAULT = pathlib.Path(os.environ["VAULT_DIR"])
BUDGET = 65_536
RG = shutil.which("rg") or "rg"
DATE_RE = re.compile(r"^- \[(\d{4}-\d{2}(?:-\d{2})?)\] ")


def split(path):
    t = path.read_text()
    parts = t.split("---\n", 2)
    if len(parts) < 3 or parts[0] != "":
        return {}, t
    fm = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.split("#", 1)[0].strip()
    return fm, parts[2]


def aliases(fm):
    v = fm.get("aliases", "")
    v = v.strip("[]")
    return [a.strip() for a in v.split(",") if a.strip()]


def sections(body):
    """Return (now_lines, log_lines)."""
    now, log, cur = [], [], None
    for line in body.splitlines():
        if line.strip() == "## Now":
            cur = "now"; continue
        if line.strip() == "## Log":
            cur = "log"; continue
        if cur == "now" and line.startswith("- "):
            now.append(line)
        elif cur == "log" and DATE_RE.match(line):
            log.append(line)
    return now, log


def git(*args):
    try:
        return subprocess.run(["git", "-C", str(VAULT), *args], capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return ""


def pad(d):
    return d if len(d) == 10 else d + "-01"


def load_files():
    files = []
    for folder in ("areas", "people"):
        for p in sorted((VAULT / folder).glob("*.md")):
            fm, body = split(p)
            now, log = sections(body)
            files.append({
                "path": f"{folder}/{p.name}", "folder": folder, "slug": p.stem,
                "desc": fm.get("description", ""), "updated": fm.get("updated", "0000-00-00"),
                "aliases": aliases(fm), "now": now, "log": log,
            })
    return files


def days_since(date, today=None):
    import datetime
    try:
        d = datetime.date.fromisoformat(date)
    except ValueError:
        return None
    return ((today or datetime.date.today()) - d).days


def recent():
    out = []
    diff = git("diff", "HEAD", "-U0", "--", "areas", "people", "decisions")
    log = git("log", "-10", "-p", "-U0", "--", "areas", "people", "decisions")
    for blob in (diff, log):
        cur = None
        for line in blob.splitlines():
            if line.startswith("+++ b/"):
                cur = line[6:]
            elif line.startswith("+- [") and cur:
                m = DATE_RE.match(line[1:])
                if m:
                    text = line[1 + m.end():].strip()
                    out.append(f"- {cur} · {m.group(1)} · {text[:60]}")
    # de-dupe preserving order, cap 10
    seen, uniq = set(), []
    for o in out:
        if o not in seen:
            seen.add(o); uniq.append(o)
    return uniq[:10]


# ---- stale detector: Now bullets whose distinctive values are contradicted by a newer Log line ----
MONEY_RE = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?([kKmM])?\b")
VER_RE = re.compile(r"\bv(\d+)\b")
NUM_RE = re.compile(r"(?<![\d$.,:/-])(\d{1,3}(?:,\d{3})+|\d{2,})(\+?)(?![\d,%:/-])")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{4,}")
SEE_RE = re.compile(r"see ((?:areas|people)/[A-Za-z0-9._-]+\.md)")
NEG_RE = re.compile(r"\b(remov\w*|no longer|dropped|gone|ended|stopped|cancell?ed|quit|left|disabl\w*|off|public|unlock\w*)\b", re.I)
KEYWORDS = ("password",)
STOP = {"claude", "about", "which", "there", "their", "these", "those", "would", "could",
        "should", "since", "still", "after", "before", "where", "while", "being", "other", "every", "first", "never",
        "through", "until", "because", "between", "doesn't", "don't", "really", "later", "already", "going", "thing",
        "things", "weekly", "sept", "today", "superseded"}
YEARISH = re.compile(r"^(19|20)\d\d$")


def stems(text):
    return {w.lower()[:5] for w in WORD_RE.findall(text) if w.lower() not in STOP}


def facts(text):
    """{(class, key): value} — money (keyed by nearest noun), v-tags, context-keyed numbers, keywords."""
    out = {}
    for m in MONEY_RE.finditer(text):
        n = float(m.group(1).replace(",", "")) * {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)
        out[("money", "")] = out.get(("money", ""), set()) | {int(n)}
    for m in VER_RE.finditer(text):
        out[("ver", "")] = out.get(("ver", ""), set()) | {int(m.group(1))}
    for m in NUM_RE.finditer(text):
        if YEARISH.match(m.group(1)): continue
        after = re.match(r"[^A-Za-z]{0,3}([A-Za-z]{3,})", text[m.end():])
        if not after: continue
        key = after.group(1).lower()
        if key in ("and", "the", "for", "per", "not", "with", "but", "was", "are", "has", "had", "hrs"): continue
        out[("num", key)] = out.get(("num", key), set()) | {int(m.group(1).replace(",", ""))}
    for kw in KEYWORDS:
        if kw in text.lower():
            out[("kw", kw)] = {0 if NEG_RE.search(text) else 1}
    return out


def stale(files, today=None):
    import datetime
    today = today or datetime.date.today()
    by_path = {f["path"]: f for f in files}
    out = []
    for f in files:
        body_text = "\n".join(f["now"] + f["log"])
        linked = [by_path[p] for p in dict.fromkeys(SEE_RE.findall(body_text)) if p in by_path and p != f["path"]]
        logs = []  # (date, text, path)
        for g in [f] + linked:
            for l in g["log"]:
                m = DATE_RE.match(l)
                logs.append((pad(m.group(1)), l[m.end():], g["path"]))
        for b in f["now"]:
            bf = facts(b)
            if not bf: continue
            bs = stems(b)
            for (cls, key), vals in bf.items():
                def same_class_vals(t):  # every value of this class in a line, any key (120 "total" still supports 120 "users")
                    return set().union(*(v for (c, k), v in facts(t).items() if c == cls)) if cls != "kw" else facts(t).get((cls, key), set())
                # support = newest Log line (same or linked file) that carries this same value
                support = max((d for d, t, p in logs if vals & same_class_vals(t)), default="")
                for d, t, p in sorted(logs, reverse=True):
                    if d <= support: break
                    lv = facts(t).get((cls, key))
                    if not lv or vals & same_class_vals(t): continue
                    if cls == "kw" and not (vals == {1} and lv == {0}): continue
                    if cls in ("money", "num") and not any(0.1 <= a / b <= 10 for a in vals for b in lv if a and b): continue  # same order of magnitude = a correction, not a different quantity
                    if len(bs & stems(t)) < (3 if cls in ("money", "num") else 2): continue  # same topic, not just the same file
                    show = {"money": lambda s: "/".join(f"${v:,}" for v in sorted(s)), "ver": lambda s: "/".join(f"v{v}" for v in sorted(s)),
                            "num": lambda s: "/".join(f"{v:,} {key}" for v in sorted(s)), "kw": lambda s: key + (" (gone)" if s == {0} else "")}[cls]
                    out.append(f"- {f['path']} Now says {show(vals)}; {p if p != f['path'] else 'its'} log {d} says {show(lv)}: "
                               f"\"{t.strip()[:70]}\"")
                    break
    # now.md: unchecked dated actions >7 days past due
    _, nowbody = split(VAULT / "now.md") if (VAULT / "now.md").exists() else ({}, "")
    for l in nowbody.splitlines():
        m = re.match(r"- \[ \] (\d{4}-\d{2}-\d{2}) (.*)", l)
        if m:
            n = days_since(m.group(1), today)
            if n is not None and n > 7:
                out.append(f"- now.md overdue {n}d: {m.group(1)} {m.group(2)[:70]}")
    seen, uniq = set(), []
    for o in out:
        if o not in seen:
            seen.add(o); uniq.append(o)
    return uniq[:12]


def tier(f):
    """0 = areas and recently-touched people with a Now; 1 = other people with a Now; 2 = the rest."""
    if f["folder"] == "areas": return 0
    n = days_since(f["updated"])
    if f["now"] and n is not None and n <= 14: return 0
    return 1 if f["now"] else 2


def build_context(files, budget=BUDGET):
    parts = []   # list of strings, in final order
    size = 0

    def add(s):
        nonlocal size
        parts.append(s); size += len(s.encode())

    add("# Vault context — generated by teamvault. Do not edit.\n\n")
    # 1. now.md
    _, nowbody = split(VAULT / "now.md") if (VAULT / "now.md").exists() else ({}, "")
    add("# Now\n" + nowbody.strip() + "\n\n")
    # 2. profile (optional)
    _, prof = split(VAULT / "profile.md") if (VAULT / "profile.md").exists() else ({}, "")
    if prof.strip():
        add("## Profile\n" + prof.strip() + "\n\n")
    # 3. every Now block: areas, then people with a Now
    add("## Current truth (## Now blocks)\n")
    for f in [x for x in files if x["folder"] == "areas"] + [x for x in files if x["folder"] == "people" and x["now"]]:
        if f["now"]:
            add(f"### {f['path']} — {f['desc']}\n" + "\n".join(f["now"]) + "\n\n")
        elif f["folder"] == "areas":
            add(f"### {f['path']} — {f['desc']}\n(no ## Now yet; see log below)\n\n")
    # 4. recent
    rec = recent()
    add("## Recent\n" + ("\n".join(rec) if rec else "(none)") + "\n\n")
    st = stale(files)
    if st:
        add("## STALE? (Now bullets a newer Log line contradicts; now.md items >7d overdue — verify, then fix the Now)\n" + "\n".join(st) + "\n\n")

    # 5. logs: newest line of every file (≤3-line files whole), then tiered round-robin:
    #    areas + recently-touched people first, then other people with a Now, then the rest; within a tier by updated desc, then size
    order = sorted(files, key=lambda f: (tier(f), -int(pad(f["updated"]).replace("-", "") or 0), -len(f["log"]), f["path"]))
    loaded = {f["path"]: [] for f in files}
    remaining = {f["path"]: sorted(f["log"], key=lambda l: pad(DATE_RE.match(l).group(1)), reverse=True) for f in files}  # newest-DATED first (logs are appended out of order)
    header_cost = {f["path"]: len(f"### {f['path']}\n\n".encode()) for f in files}
    reserve = 0
    decisions = load_decisions()
    dec_units = [len((d["text"] + "\n\n").encode()) for d in decisions]
    log_head = "## Logs (newest first; older lines: rg the file)\n"
    budget_left = budget - size - len(log_head.encode()) - len("## Decisions\n".encode())

    def take(f, n=1):
        nonlocal budget_left
        for _ in range(n):
            if not remaining[f["path"]]:
                return
            line = remaining[f["path"]][0]
            cost = len((line + "\n").encode()) + (header_cost[f["path"]] if not loaded[f["path"]] else 0)
            if cost > budget_left:
                return
            loaded[f["path"]].append(remaining[f["path"]].pop(0)); budget_left -= cost

    for f in order:  # pass 1: newest line; tiny files whole
        take(f, 3 if len(f["log"]) <= 3 else 1)
    dec_loaded = []
    for d, cost in zip(decisions, dec_units):  # pass 2: decisions (the why) before older log lines
        if cost <= budget_left:
            dec_loaded.append(d); budget_left -= cost
    for t in (0, 1, 2):  # round-robin inside each tier; a tier is exhausted before the next one gets budget
        tier_files = [f for f in order if tier(f) == t]
        progressed = True
        while progressed:
            progressed = False
            for f in tier_files:
                before = len(loaded[f["path"]])
                take(f, 1)
                if len(loaded[f["path"]]) > before:
                    progressed = True
    add(log_head)
    for f in files:  # render in stable path order
        if loaded[f["path"]]:
            add(f"### {f['path']}\n" + "\n".join(loaded[f["path"]]) + "\n\n")
    # 6. decisions newest first
    add("## Decisions\n")
    for d in decisions:
        if d in dec_loaded:
            add(d["text"] + "\n\n")
        else:
            add(f"- {d['path']} — {d['desc']} (not loaded)\n")
    # tail: capped — the biggest gaps first, one summary line for the rest (INDEX.md has every file)
    TAIL_MAX = 8
    left = sorted((f for f in files if remaining[f["path"]]), key=lambda f: (-len(remaining[f["path"]]), f["path"]))
    tail = [f"- {f['path']} · {len(remaining[f['path']])} lines not loaded (updated {f['updated']})" for f in left[:TAIL_MAX]]
    if len(left) > TAIL_MAX:
        more = left[TAIL_MAX:]
        tail.append(f"- … {len(more)} more files, {sum(len(remaining[f['path']]) for f in more)} lines: "
                    + ", ".join(f["slug"] for f in more[:12]) + (" …" if len(more) > 12 else ""))
    add("## Not loaded — rg these (full map: INDEX.md)\n" + ("\n".join(tail) if tail else "(everything loaded)") + "\n")
    return "".join(parts)


def load_decisions():
    out = []
    for p in sorted((VAULT / "decisions").glob("*.md"), reverse=True):
        fm, body = split(p)
        out.append({"path": f"decisions/{p.name}", "desc": fm.get("description", ""),
                    "decided": fm.get("decided", ""), "status": fm.get("status", ""),
                    "text": f"### decisions/{p.name} ({fm.get('decided','')}, {fm.get('status','')})\n" + body.strip()})
    return out


def mentions(files):
    targets = ["areas", "people", "decisions", "profile.md", "now.md"]
    lines = []
    for f in [x for x in files if x["folder"] == "people"]:
        terms = [f["slug"].replace("-", " ")] + f["aliases"]
        pat = "|".join(re.escape(t) for t in terms if t)
        try:
            r = subprocess.run([RG, "-l", "-i", "-w", "--no-config", "--no-ignore", pat, *targets],
                               cwd=VAULT, capture_output=True, text=True, timeout=10)
            hits = sorted(h for h in r.stdout.split() if h != f["path"])
        except Exception:
            hits = []
        groups = {}
        for h in hits:
            groups.setdefault(h.split("/")[0] if "/" in h else h, []).append(h.replace(".md", ""))
        rendered = "; ".join(", ".join(groups[k]) for k in ("areas", "people", "decisions", "profile.md", "now.md") if k in groups)
        lines.append(f"- {f['slug']} → {f['path'].replace('.md','')}" + (f"; {rendered}" if rendered else "")
                     )
    return lines


def build_index(files):
    out = ["# Vault index — generated by teamvault. Do not edit. Use: rg '^- <slug> ' INDEX.md\n"]
    out.append("## Map")
    for special in ("now.md", "profile.md"):
        p = VAULT / special
        if p.exists():
            fm, _ = split(p); out.append(f"- {special} — {fm.get('description','')} (updated {fm.get('updated','')})")
    for f in files:
        if f["folder"] == "areas" or f["now"] or len(f["log"]) >= 3:
            out.append(f"- {f['path']} — {f['desc']} · {len(f['log'])} facts · updated {f['updated']}")
    out.append("\n## Mentions (people → files that mention them)")
    out.extend(mentions(files))
    out.append("\n## Decisions (newest first)")
    for d in load_decisions():
        out.append(f"- {d['path']} — {d['desc']} ({d['decided']}, {d['status']})")
    others = [f["slug"] for f in files if f["folder"] == "people" and not f["now"] and len(f["log"]) < 3]
    out.append("\n## People — other (1–2 facts, no Now)")
    out.append(", ".join(others))
    return "\n".join(out) + "\n"


def main():
    files = load_files()
    target = BUDGET
    for _ in range(6):  # tail/decision lines aren't pre-counted; shrink the target until the file fits
        ctx = build_context(files, target)
        over = len(ctx.encode()) - BUDGET
        if over <= 0:
            break
        target -= over + 256
    idx = build_index(files)
    (VAULT / "CONTEXT.md").write_text(ctx)
    (VAULT / "INDEX.md").write_text(idx)
    print(f"CONTEXT.md {len(ctx.encode())} bytes; INDEX.md {len(idx.encode())} bytes")


if __name__ == "__main__":
    main()
