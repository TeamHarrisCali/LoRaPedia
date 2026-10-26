"""Read Wikipedia ZIM files: stream articles, extract clean text, chunk lead + key sections."""

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Iterator

from libzim.reader import Archive

# Elements whose whole subtree is noise for Q&A.
_SKIP_TAGS = {"script", "style", "table", "sup", "figure", "nav", "aside", "noscript", "head", "svg",
              "h1"}  # h1 repeats the title, which is stored separately
_SKIP_CLASSES = ("infobox", "navbox", "hatnote", "mw-editsection", "reference", "thumb", "toc",
                 "sidebar", "metadata", "noprint", "mw-empty-elt", "shortdescription")
_VOID = {"br", "img", "hr", "meta", "link", "input", "wbr", "source"}
_BLOCK = {"p", "li", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br", "blockquote", "dd", "dt", "tr"}
_HEADINGS = {"h2", "h3", "h4"}
# Sections that never help answer a question.
_SKIP_SECTIONS = {"see also", "references", "external links", "notes", "further reading", "bibliography",
                  "sources", "citations", "footnotes", "gallery", "navigation menu"}

CHUNK_CHARS = 800  # target upper bound per chunk; small enough for small-model contexts
MAX_SECTIONS = 6  # lead + a handful of key sections per article
MAX_CHUNKS_PER_SECTION = 2


@dataclass
class Chunk:
    ord: int
    section: str  # "" for the lead
    text: str


class _Extractor(HTMLParser):
    """Collects paragraphs grouped under their most recent h2-h4 heading."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sections: list[tuple[str, list[str]]] = [("", [])]
        self._skip = 0
        self._buf: list[str] = []
        self._in_heading = False
        self._heading_buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._skip:
            if tag not in _VOID:
                self._skip += 1
            return
        classes = (dict(attrs).get("class") or "").lower()
        if tag in _SKIP_TAGS or any(c in classes for c in _SKIP_CLASSES):
            if tag not in _VOID:
                self._skip = 1
            return
        if tag in _BLOCK:
            self._flush()
        if tag in _HEADINGS:
            self._in_heading = True
            self._heading_buf = []

    def handle_endtag(self, tag):
        if self._skip:
            self._skip -= 1
            return
        if tag in _HEADINGS and self._in_heading:
            self._in_heading = False
            heading = " ".join("".join(self._heading_buf).split())
            if tag == "h2":  # deeper headings stay inside their h2 section
                self.sections.append((heading, []))
            self._heading_buf = []
            return
        if tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if self._skip:
            return
        (self._heading_buf if self._in_heading else self._buf).append(data)

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self.sections[-1][1].append(text)


def extract_sections(html: str) -> list[tuple[str, list[str]]]:
    """Return [(heading, paragraphs)]; the first entry (heading "") is the lead."""
    parser = _Extractor()
    parser.feed(html)
    parser.close()
    parser._flush()
    return [(h, p) for h, p in parser.sections if p]


def _split_paragraphs(paras: list[str], limit: int) -> list[str]:
    """Pack paragraphs into chunks of at most ~limit chars, splitting long ones at sentences."""
    pieces: list[str] = []
    for para in paras:
        if len(para) <= limit:
            pieces.append(para)
            continue
        cur = ""
        for sentence in re.split(r"(?<=[.!?])\s+", para):
            if cur and len(cur) + 1 + len(sentence) > limit:
                pieces.append(cur)
                cur = sentence
            else:
                cur = f"{cur} {sentence}".strip()
        if cur:
            pieces.append(cur)
    chunks: list[str] = []
    cur = ""
    for piece in pieces:
        if cur and len(cur) + 1 + len(piece) > limit:
            chunks.append(cur)
            cur = piece
        else:
            cur = f"{cur} {piece}".strip()
    if cur:
        chunks.append(cur)
    return chunks


def chunk_article(html: str, lead_only: bool = False) -> list[Chunk]:
    """Chunk the lead plus key sections. Lead is always chunk 0."""
    chunks: list[Chunk] = []
    kept = 0
    for heading, paras in extract_sections(html):
        if heading.lower() in _SKIP_SECTIONS:
            continue
        if heading and (lead_only or kept >= MAX_SECTIONS - 1):
            continue
        pieces = _split_paragraphs(paras, CHUNK_CHARS)
        # The lead keeps all its chunks; other sections are capped.
        for piece in pieces if not heading else pieces[:MAX_CHUNKS_PER_SECTION]:
            chunks.append(Chunk(len(chunks), heading, piece))
        if heading:
            kept += 1
    return chunks


@dataclass
class Article:
    entry_id: int
    path: str
    title: str
    html: str


class ZimReader:
    def __init__(self, path: str) -> None:
        self.path = path
        self.archive = Archive(path)

    @property
    def entry_count(self) -> int:
        return self.archive.entry_count

    @property
    def article_count(self) -> int:
        return self.archive.article_count

    def metadata(self, key: str, default: str = "") -> str:
        try:
            return self.archive.get_metadata(key).decode("utf-8", "replace")
        except Exception:
            return default

    def articles(self, start: int = 0) -> Iterator[Article]:
        """Yield HTML articles from entry id `start`, skipping redirects and non-HTML resources."""
        for entry_id in range(start, self.archive.entry_count):
            entry = self.archive._get_entry_by_id(entry_id)
            if entry.is_redirect:
                continue
            item = entry.get_item()
            if not item.mimetype.startswith("text/html"):
                continue
            yield Article(entry_id, entry.path, entry.title or entry.path, bytes(item.content).decode("utf-8", "replace"))
