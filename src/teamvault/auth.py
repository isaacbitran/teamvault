"""Per-person tokens. Random, shown once, stored only as sha256 in principals.json; expiring and revocable.
Identity comes only from here, never from a tool argument."""
import datetime
import hashlib
import json
import secrets

from . import config


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _load() -> dict:
    try:
        return json.loads(config.PRINCIPALS.read_text())
    except (FileNotFoundError, ValueError):
        return {}


def mint(sub: str, days: int = 90) -> str:
    import re
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", sub):
        raise SystemExit("user name must be lowercase letters/digits/dashes, e.g. 'alice'")
    token = "tv_" + secrets.token_urlsafe(32)
    data = _load()
    exp = (datetime.date.today() + datetime.timedelta(days=days)).isoformat()
    data[_h(token)] = {"sub": sub, "created": datetime.date.today().isoformat(), "expires": exp, "revoked": False}
    _save(data)
    return token


def _save(data: dict) -> None:
    import os
    config.PRINCIPALS.parent.mkdir(parents=True, exist_ok=True)
    tmp = config.PRINCIPALS.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2)); tmp.chmod(0o600); os.replace(tmp, config.PRINCIPALS)


def revoke(sub: str) -> int:
    data = _load()
    n = 0
    for v in data.values():
        if v["sub"] == sub and not v["revoked"]:
            v["revoked"] = True; n += 1
    _save(data)
    return n


def verify(token: str | None) -> str | None:
    if not token:
        return None
    p = _load().get(_h(token))
    if not p or p["revoked"] or p["expires"] < datetime.date.today().isoformat():
        return None
    return p["sub"]


def bearer(headers) -> str | None:
    h = headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


_SEEN: dict = {}


def seen(user: str, ua: str, path: str) -> None:
    """Remember who connected last, from what tool (written at most once a minute per user)."""
    import time, os
    now = time.time()
    tool = "codex" if "codex" in ua.lower() else "claude-code" if "claude" in ua.lower() else \
           "refresher" if path == "/briefing" and "python" in ua.lower() else ua.split("/")[0][:20]
    prev = _SEEN.get(user)
    _SEEN[user] = {"at": now, "tool": tool}
    if prev and now - prev.get("written", 0) < 60 and prev["tool"] == tool:
        _SEEN[user]["written"] = prev["written"]; return
    _SEEN[user]["written"] = now
    try:
        f = config.DATA / "seen.json"
        data = json.loads(f.read_text()) if f.exists() else {}
        data.setdefault(user, {})[tool] = now
        tmp = f.with_suffix(".tmp"); tmp.write_text(json.dumps(data)); os.replace(tmp, f)
    except Exception:
        pass


def last_seen() -> str:
    import time
    try:
        data = json.loads((config.DATA / "seen.json").read_text())
    except Exception:
        return ""
    def ago(t):
        m = int((time.time() - t) / 60)
        return f"{m}m ago" if m < 120 else f"{m // 60}h ago" if m < 2880 else f"{m // 1440}d ago"
    return " · ".join(f"@{u}: " + ", ".join(f"{k} {ago(v)}" for k, v in sorted(tools.items(), key=lambda x: -x[1]))
                      for u, tools in sorted(data.items()))


class AuthMiddleware:
    """ASGI wrapper: every request except /health needs a valid token; browsers (Origin header) are refused."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in ("/health", "/setup-claude.sh", "/setup-codex.sh"):
            return await self.app(scope, receive, send)
        if scope["path"].rstrip("/") == "/mcp" and scope["method"] == "GET":   # stateless: no server-push stream
            await send({"type": "http.response.start", "status": 405, "headers": [(b"allow", b"POST")]})
            return await send({"type": "http.response.body", "body": b""})
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        user = verify(bearer(headers))
        if "origin" in headers or not user:
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401 if not user else 403,
                        "headers": [(b"content-type", b"application/json"), (b"www-authenticate", b"Bearer")]})
            return await send({"type": "http.response.body", "body": body})
        scope.setdefault("state", {})["user"] = user
        seen(user, headers.get("user-agent", "?"), scope["path"])
        return await self.app(scope, receive, send)
