"""`teamvault demo`: a local server over a seeded example vault (a fictional bakery team), with two demo users.

Uses the model-free librarian unless TEAMVAULT_LIBRARIAN is set, so it runs without any API key."""
import os
import pathlib
import shutil

DEMO_USERS = ("maya", "theo")


def setup(directory: str) -> None:
    root = pathlib.Path(directory).resolve()
    os.environ.setdefault("TEAMVAULT_DATA", str(root / "data"))
    os.environ.setdefault("TEAMVAULT_TEAM", "demo")
    os.environ.setdefault("TEAMVAULT_LIBRARIAN", "naive")

    from . import auth, config, vault   # config reads the env set above

    fresh = not (config.VAULT / ".git").exists()
    vault.init()
    if fresh:
        src = pathlib.Path(__file__).parent / "demo_vault"
        with vault.locked():
            shutil.copytree(src, config.VAULT, dirs_exist_ok=True)
            vault.rebuild()
            vault.git("add", "-A")
            vault.git("commit", "-q", "-m", "seed demo vault", check=True, author="server")

    for u in DEMO_USERS:           # fresh tokens every run; old demo tokens stop working
        auth.revoke(u)
    tokens = {u: auth.mint(u, 7) for u in DEMO_USERS}
    url = config.PUBLIC_URL
    print(f"""
teamvault demo
  vault:      {config.VAULT}
  server:     {url}/mcp   (librarian: {config.LIBRARIAN})
  tokens (valid 7 days, new ones each run):
    maya  {tokens['maya']}
    theo  {tokens['theo']}

Connect Claude Code as maya:
  claude mcp add --transport http team {url}/mcp --header "Authorization: Bearer {tokens['maya']}"
Or run the full client setup (hooks for the briefing and urgent items):
  curl -fsSL {url}/setup-claude.sh -o /tmp/tv.sh && TEAMVAULT_TOKEN={tokens['maya']} sh /tmp/tv.sh
Codex as theo:
  curl -fsSL {url}/setup-codex.sh -o /tmp/tv.sh && TEAMVAULT_TOKEN={tokens['theo']} sh /tmp/tv.sh
Quick check:
  curl -H "Authorization: Bearer {tokens['maya']}" {url}/briefing

Ctrl-C to stop.
""", flush=True)
