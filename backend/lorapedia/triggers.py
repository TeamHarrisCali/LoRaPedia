"""Decide whether an incoming text message is a question, a command, or noise."""

from dataclasses import dataclass
from typing import Literal

COMMANDS = ("help", "more", "stats")


@dataclass(frozen=True)
class Parsed:
    kind: Literal["question", "command", "ignore"]
    text: str = ""  # question text, or command name


def parse_message(
    text: str, is_dm: bool, mode: str = "dm", prefixes: tuple[str, ...] | list[str] = ("?", "!wiki")
) -> Parsed:
    """mode: "dm" (direct messages only), "channel" (prefix required), or "both"."""
    text = text.strip()
    if not text:
        return Parsed("ignore")

    stripped = _strip_prefix(text, prefixes)
    if is_dm:
        if mode not in ("dm", "both"):
            return Parsed("ignore")
        body = stripped if stripped is not None else text  # prefix optional in a DM
    else:
        if mode not in ("channel", "both") or stripped is None:
            return Parsed("ignore")
        body = stripped

    body = body.strip()
    if not body:
        return Parsed("ignore")
    if body.lower() in COMMANDS:
        return Parsed("command", body.lower())
    return Parsed("question", body)


def _strip_prefix(text: str, prefixes) -> str | None:
    lowered = text.lower()
    # Longest prefix first so "!wiki" wins over a shorter overlapping one.
    for prefix in sorted(prefixes, key=len, reverse=True):
        if prefix and lowered.startswith(prefix.lower()):
            rest = text[len(prefix):]
            # Word-like prefixes need a boundary ("!wikipedia" is not "!wiki").
            if prefix[-1].isalnum() and rest and not rest[0].isspace():
                continue
            return rest
    return None
