"""Vault linter: checks one Markdown file of the vault against the format rules (frontmatter, ## Now / ## Log,
append-only Log, supersede pointers, decision shape, dated actions). Returns errors, warnings and auto-fixes.
  python -m teamvault.vendor.vault_lint <path>    lint one file; exit 2 on failure (VAULT_DIR must be set)

Vendored from the author's personal vault tooling (MIT, Isaac Bitran); trimmed for teamvault."""
import os, re, sys, json, pathlib, subprocess, datetime

VAULT = pathlib.Path(os.environ["VAULT_DIR"]).resolve()
TODAY = datetime.date.today().isoformat()
DATE_RE = re.compile(r"^- \[(\d{4}-\d{2}(?:-\d{2})?)\] ")
SEE_RE = re.compile(r"see ((?:areas|people|decisions|meetings|reviews)/[A-Za-z0-9._/-]+\.md)(?:\s+\"([^\"]+)\")?")
SEE_LOG_RE = re.compile(r"see log (\d{4}-\d{2}(?:-\d{2})?)")
LINENUM_RE = re.compile(r"\.md:\d+")
ACTION_RE = re.compile(r"^- \[[ x]\] ")
ACTION_DATE_RE = re.compile(r"^- \[[ x]\] \d{4}-\d{2}-\d{2} ")


def pad(d): return d if len(d) == 10 else d + "-01"


def pad_end(d):
    """Month-precision dates pad to the last day of the month (for future checks)."""
    if len(d) == 10: return d
    import calendar
    y, m = int(d[:4]), int(d[5:7])
    if not 1 <= m <= 12: return d + "-99"
    return f"{d}-{calendar.monthrange(y, m)[1]:02d}"


def valid_date(d):
    try: datetime.date.fromisoformat(pad(d)); return True
    except ValueError: return False


def split(text):
    parts = text.split("---\n", 2)
    if len(parts) < 3 or parts[0] != "":
        return None, text
    fm = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1); fm[k.strip()] = v.split("#", 1)[0].strip()
    return fm, parts[2]


def check_pointers(rel, body, log_dates, errors):
    for m in SEE_RE.finditer(body):
        target = VAULT / m.group(1)
        if not target.exists():
            errors.append(f"pointer target missing: see {m.group(1)}")
        elif m.group(2) and m.group(2) not in target.read_text():
            errors.append(f'pointer phrase not found in {m.group(1)}: "{m.group(2)}"')
    for m in SEE_LOG_RE.finditer(body):
        if m.group(1) not in log_dates:
            errors.append(f"see log {m.group(1)}: no Log line with that date in this file")
    if LINENUM_RE.search(body):
        errors.append("line-number pointer (file.md:N) — use see <path> \"<phrase>\" instead")


def now_bullets(text):
    """Normalized ## Now bullets of a whole file text (for add/remove vs reword comparison)."""
    _, body = split(text)
    out, cur = [], None
    for l in body.splitlines():
        s = l.strip()
        if s == "## Now": cur = "now"; continue
        if s.startswith("## "): cur = None; continue
        if cur == "now" and l.startswith("- ") and not DATE_RE.match(l):
            out.append(re.sub(r"\s+", " ", l[2:]).strip().rstrip(".").lower())
    return out


def now_delta(head_text, cur_text):
    """Return (added, removed, reworded): unmatched new/old bullets and the count of close-match pairs."""
    import difflib
    old, new = now_bullets(head_text), now_bullets(cur_text)
    removed = [b for b in old if b not in new]
    added = [b for b in new if b not in old]
    reworded = 0
    for a in list(added):
        best = max(removed, key=lambda r: difflib.SequenceMatcher(None, a, r).ratio(), default=None)
        if best is not None and difflib.SequenceMatcher(None, a, best).ratio() >= 0.5:
            added.remove(a); removed.remove(best); reworded += 1
    return added, removed, reworded


def fix_updated(path, new):
    """Rewrite only the `updated:` frontmatter line. Returns True if the file changed."""
    text = path.read_text()
    parts = text.split("---\n", 2)
    if len(parts) < 3 or parts[0] != "": return False
    fm_lines, hit = [], False
    for l in parts[1].splitlines(keepends=True):
        if l.startswith("updated:") and not hit:
            l = f"updated: {new}\n"; hit = True
        fm_lines.append(l)
    if not hit: return False
    path.write_text("---\n" + "".join(fm_lines) + "---\n" + parts[2])
    return True


def lint_area_person(rel, fm, body, errors, warnings, path=None, fixes=None, text=""):
    slug = pathlib.Path(rel).stem
    if fm is None:
        errors.append("missing frontmatter"); return
    if fm.get("name") != slug: errors.append(f"name: must equal {slug}")
    d = fm.get("description", "")
    if not (20 <= len(d) <= 150): errors.append(f"description must be 20–150 chars (is {len(d)})")
    upd = fm.get("updated", "")
    upd_ok = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", upd))
    if not upd_ok: errors.append("updated: must be YYYY-MM-DD")
    lines = body.splitlines()
    if "## Log" not in [l.strip() for l in lines]: errors.append("missing ## Log heading"); return
    cur, now_n, log_n, log_dates, newest = None, 0, 0, [], ""
    for l in lines:
        s = l.strip()
        if s == "## Now": cur = "now"; continue
        if s == "## Log": cur = "log"; continue
        if not s: continue
        if cur is None:
            errors.append(f"content before ## Now/## Log: {s[:50]}"); continue
        if cur == "now":
            if DATE_RE.match(l): errors.append("dated line inside ## Now (dates belong in ## Log)")
            elif l.startswith("- "): now_n += 1
            else: errors.append(f"non-bullet line under ## Now (would not load): {s[:50]}")
            continue
        m = DATE_RE.match(l)
        if not m:
            errors.append(f"Log line not '- [YYYY-MM-DD] …': {s[:50]}"); continue
        log_n += 1; log_dates.append(m.group(1))
        if not valid_date(m.group(1)): errors.append(f"invalid date {m.group(1)}")
        elif pad_end(m.group(1)) > TODAY: errors.append(f"future-dated Log line {m.group(1)} — future events go to now.md")
        if "superseded" in l and "see " not in l: errors.append("superseded marker needs 'see <pointer>'")
        if len(l) > 400: warnings.append(f"Log line >400 chars ({m.group(1)})")
    if log_dates:
        newest = max(pad(x) for x in log_dates)
    # auto-fix updated: → max(today, newest Log date) when it lags or sits in the future (mechanical, never blocks)
    if upd_ok:
        want = max(TODAY, newest) if (newest and newest > upd) else (TODAY if upd > TODAY else upd)
        if want != upd and path is not None and fix_updated(path, want):
            (fixes if fixes is not None else warnings).append(f"updated: {upd} → {want} (auto-fixed; re-Read before further edits)")
    if now_n > 12: warnings.append(f"## Now has {now_n} bullets (>12)")
    if log_n > 60: warnings.append(f"## Log has {log_n} lines (>60) — consolidate")
    if rel.startswith("areas/") and now_n == 0: errors.append("area has no ## Now")
    check_pointers(rel, body, set(log_dates), errors)
    # Now edited without a Log line (only meaningful when file is tracked & dirty)
    diff = subprocess.run(["git", "-C", str(VAULT), "diff", "-U0", "--", rel], capture_output=True, text=True).stdout
    head = subprocess.run(["git", "-C", str(VAULT), "show", f"HEAD:{rel}"], capture_output=True, text=True).stdout
    if diff:
        head_logs = [l for l in head.splitlines() if DATE_RE.match(l)]
        cur_logs = [l for l in lines if DATE_RE.match(l)]
        # append-only: every HEAD Log line must survive, verbatim or as a superseded-marked extension of itself
        for hl in head_logs:
            if hl in cur_logs: continue
            if any(cl.startswith(hl.rstrip()) and "superseded" in cl for cl in cur_logs): continue
            errors.append(f"Log line removed or edited (append-only): {hl[:60]}")
        new_logs = [l for l in cur_logs if l not in head_logs and not any(l.startswith(hl.rstrip()) for hl in head_logs)]
        if "## Now" in head and not new_logs:
            # compare normalized bullet SETS: added/removed bullets need a Log line; a reword is only a warning
            added, removed, reworded = now_delta(head, text)
            if added or removed:
                what = "; ".join(filter(None, [f"+{len(added)} added" if added else "", f"-{len(removed)} removed" if removed else ""]))
                errors.append(f"## Now bullets changed ({what}) without a NEW Log line — add one saying what changed and when")
            elif reworded:
                warnings.append(f"## Now reworded ({reworded} bullet{'s' if reworded > 1 else ''}) without a new Log line — fine if the facts didn't change")


def lint_decision(rel, fm, body, errors, warnings):
    slug = pathlib.Path(rel).stem
    if fm is None: errors.append("missing frontmatter"); return
    if fm.get("name") != slug: errors.append(f"name: must equal {slug}")
    d = fm.get("description", "")
    if not (20 <= len(d) <= 150): errors.append(f"description must be 20–150 chars (is {len(d)})")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", fm.get("decided", "")): errors.append("decided: must be YYYY-MM-DD")
    if fm.get("status") not in ("active", "reversed", "revisit"): errors.append("status: must be active|reversed|revisit")
    blines = [l for l in body.splitlines() if l.strip()]
    labels = [l.split(":**")[0].strip("* ") for l in blines if l.startswith("**")]
    if labels != ["Decided", "Why", "Alternatives", "Revisit if"]:
        errors.append("body must be exactly **Decided:** **Why:** **Alternatives:** **Revisit if:** lines")
    for l in blines:
        if not l.startswith("**"): errors.append(f"stray line in decision body: {l[:40]}")
        elif not l.split(":**", 1)[-1].strip(): errors.append(f"empty field: {l.split(':**')[0]}**")
    check_pointers(rel, body, set(), errors)


def lint_now(rel, fm, body, errors, warnings):
    n = len(body.splitlines())
    if n > 60: warnings.append(f"now.md is {n} lines (>60) — trim it")
    for l in body.splitlines():
        if ACTION_RE.match(l) and not ACTION_DATE_RE.match(l):
            errors.append(f"action needs YYYY-MM-DD after the checkbox (use a plain '- ' bullet for undated items): {l[:50]}")
    check_pointers(rel, body, set(), errors)


def lint_profile(rel, fm, body, errors, warnings):
    if fm is None or "updated" not in fm: errors.append("profile needs frontmatter with updated:")
    if len(body.split()) > 300: errors.append(f"profile body is {len(body.split())} words (>300)")
    if re.search(r"^- \[", body, re.M): errors.append("profile is prose — no '- [' lines")
    check_pointers(rel, body, set(), errors)


def lint_file(path):
    """Returns (errors, warnings, fixes). fixes = auto-applied rewrites (informational)."""
    path = pathlib.Path(path).resolve()
    try: rel = str(path.relative_to(VAULT))
    except ValueError: return [], [], []
    if not path.exists() or path.suffix != ".md": return [], [], []
    if rel in ("CONTEXT.md", "INDEX.md", "CLAUDE.md", "inbox.md") or rel.startswith(("meetings/", "reviews/", "research/")):
        return [], [], []
    text = path.read_text()
    fm, body = split(text)
    errors, warnings, fixes = [], [], []
    if rel.startswith(("areas/", "people/")): lint_area_person(rel, fm, body, errors, warnings, path=path, fixes=fixes, text=text)
    elif rel.startswith("decisions/"): lint_decision(rel, fm, body, errors, warnings)
    elif rel == "now.md": lint_now(rel, fm, body, errors, warnings)
    elif rel == "profile.md": lint_profile(rel, fm, body, errors, warnings)
    return errors, warnings, fixes


def main():
    if len(sys.argv) != 2:
        print(__doc__); sys.exit(2)
    e, w, fx = lint_file(sys.argv[1])
    for x in e: print(f"ERROR: {x}")
    for x in w: print(f"warn: {x}")
    for x in fx: print(f"fixed: {x}")
    sys.exit(2 if e else 0)


if __name__ == "__main__":
    main()
