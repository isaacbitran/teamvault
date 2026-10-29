"""Settings. Every value comes from an environment variable with a default that works on a laptop.

Values can also be put in a KEY=VALUE file: `./teamvault.env`, or the path in TEAMVAULT_CONFIG. Real environment
variables win over the file. See `.env.example` for the full list.

This module is imported first: it sets VAULT_DIR so the vendored vault scripts point at the team vault."""
import os
import pathlib

REPO_URL = "https://github.com/isaacbitran/teamvault"


def _load_file() -> None:
    path = pathlib.Path(os.environ.get("TEAMVAULT_CONFIG", "teamvault.env"))
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_file()
_env = os.environ.get

DATA = pathlib.Path(_env("TEAMVAULT_DATA", "./data")).resolve()
TEAM = _env("TEAMVAULT_TEAM", "default")
VAULT = DATA / "tenants" / TEAM / "vault"          # one folder per team, so several teams can share a data dir
SPOOL = DATA / "spool"
PRINCIPALS = DATA / "principals.json"
LOCK = DATA / ".write.lock"

HOST = _env("TEAMVAULT_HOST", "127.0.0.1")
PORT = int(_env("TEAMVAULT_PORT", "8790"))
PUBLIC_URL = _env("TEAMVAULT_PUBLIC_URL", f"http://{HOST}:{PORT}").rstrip("/")

# Librarian: "claude" runs headless Claude Code (needs the `claude` CLI and a login or ANTHROPIC_API_KEY);
# "naive" files every shared fact verbatim into areas/unsorted.md (no model; for demos and tests).
LIBRARIAN = _env("TEAMVAULT_LIBRARIAN", "claude")
CLAUDE_BIN = _env("TEAMVAULT_CLAUDE_BIN", "claude")
MODEL = _env("TEAMVAULT_MODEL", "opus")
DAILY_CAP = int(_env("TEAMVAULT_DAILY_CAP", "60"))     # max librarian runs per day
# Optional shell command that exits 0 while another job on the same machine should go first; the librarian waits.
BUSY_CMD = _env("TEAMVAULT_BUSY_CMD", "")

os.environ["VAULT_DIR"] = str(VAULT)
