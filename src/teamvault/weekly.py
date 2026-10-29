"""Weekly job. Fully automatic and additive only: never condenses, merges or deletes anything.
Writes reviews/YYYY-WW.md (what changed this week, STALE flags, overdue actions, lint warnings) and rebuilds the index."""
import datetime

from . import config, vault
from .vendor import vault_build, vault_lint


def run() -> str:
    with vault.locked():
        if vault.git("status", "--porcelain").strip():
            vault._restore_tree()
        y, w, _ = datetime.date.today().isocalendar()
        rel = f"reviews/{y}-W{w:02d}-{datetime.date.today().isoformat()}.md"
        files = vault_build.load_files()
        stale = vault_build.stale(files)
        warnings = []
        vault_lint.TODAY = vault.today()
        for p in sorted(config.VAULT.rglob("*.md")):
            if ".git" in p.parts: continue
            _, w_, _ = vault_lint.lint_file(p)
            warnings += [f"- {p.relative_to(config.VAULT)}: {x}" for x in w_]
        week = vault.recent("7 days ago")
        body = (f"# Week {y}-W{w:02d} — generated {vault.today()} (additive; nothing was changed or removed)\n\n"
                f"## STALE? (verify, then fix the Now)\n" + ("\n".join(stale) or "(none)") +
                "\n\n## Lint warnings\n" + ("\n".join(warnings) or "(none)") +
                f"\n\n## What changed this week, by who\n{week}\n")
        (config.VAULT / rel).write_text(body)
        vault.rebuild()
        vault.git("add", "-A")
        vault.git("commit", "-q", "-m", f"weekly review {rel}", author="server")
        return rel
