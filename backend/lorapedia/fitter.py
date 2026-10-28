"""Byte-budget fitting for Meshtastic text payloads.

All limits are UTF-8 *bytes*, never characters. Answers are sent as one message
when they fit; if they do not and multi-part is enabled, they are split into
numbered "(i/n) " chunks, each within the limit, up to an admin-set part count.
"""

ELLIPSIS = "..."

# Common non-ASCII punctuation the LLM likes to emit, folded to plain ASCII so we
# don't burn 2-3 bytes per character on the radio.
_ASCII_FOLD = str.maketrans(
    {
        "‘": "'", "’": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "−": "-", "…": "...",
        " ": " ", "•": "-",
    }
)
_MARKDOWN_CHARS = str.maketrans("", "", "*#`_~>")


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def clean_text(text: str) -> str:
    """Strip markdown, fold smart punctuation to ASCII, collapse whitespace."""
    text = text.translate(_ASCII_FOLD).translate(_MARKDOWN_CHARS)
    return " ".join(text.split())


def _prefix_by_bytes(text: str, limit: int) -> str:
    """Longest prefix of text within `limit` bytes, never splitting a character."""
    if limit <= 0:
        return ""
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def truncate_bytes(text: str, limit: int, ellipsis: str = ELLIPSIS) -> str:
    """Fit text into `limit` bytes, cutting at a word boundary and adding `...`."""
    text = text.strip()
    if byte_len(text) <= limit:
        return text
    if limit <= 0:
        return ""
    if byte_len(ellipsis) > limit:
        ellipsis = ""
    cut = _prefix_by_bytes(text, limit - byte_len(ellipsis))
    if len(cut) < len(text) and not text[len(cut)].isspace():
        # Cut landed mid-word: back off to the previous space if there is one.
        space = cut.rfind(" ")
        if space > 0:
            cut = cut[:space]
    return cut.rstrip(" ,;:-") + ellipsis


def part_prefix(index: int, total: int) -> str:
    return f"({index}/{total}) "


def part_room(limit: int, max_parts: int) -> int:
    """Payload bytes available per part once the worst-case "(i/n) " is reserved."""
    if max_parts <= 1:
        return limit
    return limit - byte_len(part_prefix(max_parts, max_parts))


def total_budget(limit: int, max_parts: int) -> int:
    """Max bytes of answer text across all parts (what the LLM may aim for)."""
    return max(part_room(limit, max_parts), 0) * max(max_parts, 1)


def _wrap(text: str, room: int) -> list[str]:
    """Greedy word-wrap into chunks of at most `room` bytes."""
    chunks: list[str] = []
    cur = ""
    for word in text.split(" "):
        while byte_len(word) > room:  # single word bigger than a chunk: hard split
            piece = _prefix_by_bytes(word, room)
            if not piece:  # one character wider than the room; drop it
                word = word[1:]
                continue
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
            word = word[len(piece):]
        if not word:
            continue
        candidate = f"{cur} {word}" if cur else word
        if byte_len(candidate) <= room:
            cur = candidate
        else:
            chunks.append(cur)
            cur = word
    if cur:
        chunks.append(cur)
    return chunks


def split_parts(text: str, limit: int, max_parts: int = 3) -> list[str]:
    """Split text into at most `max_parts` messages, each <= `limit` bytes.

    A text that fits is returned unchanged as a single message. Otherwise parts
    are numbered "(i/n) ". Overflow beyond `max_parts` is truncated with `...`.
    """
    text = " ".join(text.split())
    if max_parts <= 1 or byte_len(text) <= limit:
        return [truncate_bytes(text, limit)]
    room = part_room(limit, max_parts)
    if room < 1:
        return [truncate_bytes(text, limit)]
    chunks = _wrap(text, room)
    if len(chunks) > max_parts:
        tail = " ".join(chunks[max_parts - 1:])
        chunks = chunks[: max_parts - 1] + [truncate_bytes(tail, room)]
    n = len(chunks)
    return [f"{part_prefix(i, n)}{c}" for i, c in enumerate(chunks, 1)]


def fit_answer(text: str, limit: int, max_parts: int = 1) -> list[str]:
    """Final, guaranteed-safe step: clean the text and fit it to the budget."""
    return split_parts(clean_text(text), limit, max_parts)
