"""Server backstop for the sharing rule. Runs before anything touches disk.
hard  -> withheld, never stored (only the category is logged)
instr -> rejected (instruction-shaped text = memory-poisoning risk)
soft  -> allowed through, flagged for the librarian's judgment
The client rules (CLAUDE.md / AGENTS.md) are the real boundary; this catches what slips past them."""
import re
import unicodedata

# Unicode tag characters, zero-width and bidi controls: invisible text used to smuggle instructions.
_INVISIBLE = re.compile(r"[\U000E0000-\U000E007F​-‏‪-‮⁠-⁤﻿]")

_SELF = r"(my|me|mine|our|ours|founders?|co-?founders?|teammates?)"
_COMP = (r"\b(equity|stake|vest\w*|cliff|cap ?table|salar\w*|compensation|paycheck|walk[- ]away|ownership|"
         r"sweat equity|\d+(\.\d+)?\s?%\s*(of (the )?(company|startup|business)|equity|stake|ownership))\b")
SECRET = re.compile(
    r"(sk-[A-Za-z0-9_-]{16,}|[sr]k_(live|test)_[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,}|tv_[A-Za-z0-9_-]{20,}|"
    r"AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY|\b(password|passwd|api[_ ]?key|secret)\s*[:=])", re.I)
INSTRUCTION = re.compile(
    r"\b(ignore (all |any |the |your )?(previous|prior|above)|disregard (all|your|the)|from now on|"
    r"you (must|should) (always|never)|always (say|tell|share|send|reply)|new instructions|system prompt|"
    r"pretend to be|do not tell)\b", re.I)
SOFT = re.compile(
    r"\b(mom|mother|father|family|parents?|brother|sister|girlfriend|boyfriend|wife|husband|relationship|health|"
    r"injur\w*|sick|hospital|therapy|diagnos\w*|class(es)?|grades?|homework|exams?|gpa|"
    r"drop(ping)? out|annoying|lazy|don'?t trust|hate|equity|stake|vest\w*|dilut\w*|salar\w*|"
    r"co-?founders?)\b", re.I)


def _compensation(names: list[str]) -> re.Pattern:
    """Compensation talk tied to a team member ("alice wants 20% equity", "my vesting cliff"). Generic finance
    about other companies ("the fund holds a 60% stake") is left to the librarian."""
    who = "|".join([_SELF] + [re.escape(n) for n in names if n])
    who = rf"\b({who})\b"
    return re.compile(rf"{who}.{{0,40}}{_COMP}|{_COMP}.{{0,40}}{who}", re.I)


def _team_names() -> list[str]:
    try:
        from .urgent import users
        return users()
    except Exception:
        return []


def clean(text: str) -> str:
    return unicodedata.normalize("NFKC", _INVISIBLE.sub("", text)).strip()


def check(text: str, names: list[str] | None = None) -> tuple[str, str]:
    """('ok'|'soft'|'withhold'|'reject', category). names = team user names (defaults to active token holders)."""
    if _compensation(_team_names() if names is None else names).search(text):
        return "withhold", "compensation"
    if SECRET.search(text):
        return "withhold", "secret"
    if INSTRUCTION.search(text):
        return "reject", "instruction-shaped"
    m = SOFT.search(text)
    if m:
        return "soft", m.group(0).lower()
    return "ok", ""
