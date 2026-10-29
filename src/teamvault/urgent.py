"""The URGENT list: items a teammate marked urgent stay at the top of every briefing and every tool reply for each
recipient until that recipient acknowledges them. The assistant is mandated to say them first."""
import datetime
import json
import os
import time
import uuid

from . import config, vault

PATH = config.DATA / "urgent.json"


def _load() -> list:
    try:
        return json.loads(PATH.read_text())
    except FileNotFoundError:
        return []
    except ValueError:   # never silently wipe: keep the corrupt copy aside
        PATH.rename(PATH.with_suffix(f".corrupt-{int(time.time())}"))
        return []


def _save(items: list) -> None:
    tmp = PATH.with_suffix(".tmp")
    with open(tmp, "w") as f:
        f.write(json.dumps(items, indent=1)); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, PATH)


def users() -> list[str]:
    from . import auth
    today = datetime.date.today().isoformat()
    return sorted({v["sub"] for v in auth._load().values() if not v["revoked"] and v["expires"] >= today})


def add(sender: str, text: str, for_whom: str = "all") -> str:
    """Raises ValueError for an unknown recipient instead of silently sending to nobody."""
    known = users()
    who = for_whom.strip().lstrip("@").lower()
    if who in ("", "all", "team", "everyone"):
        targets = [u for u in known if u != sender]
    elif who in known:
        targets = [who]
    else:
        raise ValueError(f"unknown recipient {for_whom!r}; use 'all' or one of {', '.join(known)}")
    iid = uuid.uuid4().hex[:5]
    with vault.locked():
        items = _load()
        items.append({"id": iid, "from": sender, "text": text, "for": targets, "acked": [],
                      "at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})
        _save(items)
    return iid


def pending(user: str) -> list:
    return [i for i in _load() if user in i["for"] and user not in i["acked"]]


def ack(user: str, ids: list[str]) -> int:
    ids = [str(i).strip().lstrip("#") for i in ids]
    n = 0
    with vault.locked():
        items = _load()
        for i in items:
            if user in i["for"] and user not in i["acked"] and (not ids or "all" in ids or i["id"] in ids):
                i["acked"].append(user); n += 1
        _save(items)
    return n


def banner(user: str) -> str:
    p = pending(user)
    if not p:
        return ""
    more = f"\n- … and {len(p) - 10} more (team_ack the ones above to see them)" if len(p) > 10 else ""
    lines = "\n".join(f"- [#{i['id']}] from @{i['from']} ({i['at']}): {i['text']}" for i in p[:10]) + more
    return ("🚨 URGENT — tell the user these FIRST, before anything else in your reply, then call team_ack once they've seen them:\n"
            f"{lines}\n\n")
