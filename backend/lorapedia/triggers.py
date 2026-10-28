"""Decide whether an incoming text message is a question, a command, or noise. Only direct messages are ever answered."""

from dataclasses import dataclass
from typing import Literal

COMMANDS = ("help", "more", "stats", "ping")


@dataclass(frozen=True)
class Parsed:
    kind: Literal["question", "command", "ignore"]
    text: str = ""  # question text, or command name


def parse_message(text: str, is_dm: bool, prefixes: tuple[str, ...] | list[str] = ("?", "!wiki")) -> Parsed:
    """Only a direct message can be a question or a command: whatever is said in a public channel is ignored, always."""
    if not is_dm:
        return Parsed("ignore")
    text = text.strip()
    if not text:
        return Parsed("ignore")

    stripped = _strip_prefix(text, prefixes)
    body = (stripped if stripped is not None else text).strip()  # the prefix is optional in a direct message
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
