"""teamvault: a shared memory that AI agents connect to over MCP.

Commands:
  teamvault serve                  run the MCP server (settings from env vars / teamvault.env)
  teamvault demo [dir]             seed a demo vault, mint two demo tokens, and serve on localhost
  teamvault init                   create the vault if it doesn't exist and print its path
  teamvault mint <user> [days]     create an access token for a user (shown once)
  teamvault revoke <user>          revoke every token of a user
  teamvault weekly                 write the weekly review (additive only)
"""
import sys

__version__ = "0.3.0"


def _serve() -> None:
    import logging
    from . import config
    import uvicorn
    logging.getLogger("mcp").setLevel(logging.WARNING)
    from .server import build_app
    uvicorn.run(build_app(), host=config.HOST, port=config.PORT, log_level="warning", access_log=False,
                timeout_graceful_shutdown=5)


def main() -> None:
    args = sys.argv[1:]
    cmd = args[0] if args else "serve"
    if cmd == "demo":
        from .demo import setup
        setup(args[1] if len(args) > 1 else "./teamvault-demo")   # sets env before config is imported
        return _serve()
    from . import config  # sets VAULT_DIR before anything imports the vendored scripts
    if cmd == "serve":
        _serve()
    elif cmd == "mint" and len(args) > 1:
        from . import auth
        print(auth.mint(args[1], int(args[2]) if len(args) > 2 else 90))
    elif cmd == "revoke" and len(args) > 1:
        from . import auth
        print(f"revoked {auth.revoke(args[1])} token(s)")
    elif cmd == "weekly":
        from .weekly import run
        print(run())
    elif cmd == "init":
        from . import vault
        vault.init(); print(config.VAULT)
    elif cmd in ("-V", "--version", "version"):
        print(__version__)
    else:
        print(__doc__); sys.exit(0 if cmd in ("-h", "--help", "help") else 2)
