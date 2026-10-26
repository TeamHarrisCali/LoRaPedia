import re
from typing import Protocol


class LLM(Protocol):
    async def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str: ...


class MockLLM:
    """Offline stand-in so the whole flow works with no model installed.

    Answers with the leading sentences of the top retrieved chunk, sized to the
    "at most N bytes" hint in the prompt. Replaced by the Ollama client in phase 2.
    """

    async def generate(self, system: str, user: str, *, temperature: float, max_tokens: int) -> str:
        hint = re.search(r"at most (\d+) bytes", user)
        budget = int(hint.group(1)) if hint else 200
        if user.startswith("REWRITE"):
            source = user.split("\n", 1)[1]
        else:
            m = re.search(r"\[1\] (.*?)(?:\n\[2\]|\n\nQuestion:)", user, re.S)
            source = m.group(1) if m else "not found"
            source = re.sub(r"^[^:\n]{1,80}: ", "", source)  # drop the "Title: " label
        out = ""
        for sentence in re.split(r"(?<=[.!?])\s+", source.strip()):
            if len((out + " " + sentence).encode()) > budget:
                break
            out = f"{out} {sentence}".strip()
        return out or source[:budget]
