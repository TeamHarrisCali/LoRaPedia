"""Curated one-click Wikipedia packages.

Modelled on Project NOMAD's idea of a small, named, sized list of Wikipedia options instead of a raw file
browser. Each package maps to a Kiwix variant; the newest version is looked up at install time (pinned versions
go stale when Kiwix rotates files), with the pinned URL below only as an offline fallback. Sizes shown before
the lookup are approximate. "maxi" (with images) is deliberately absent: LoRaPedia never uses images.
"""

import re
from typing import Any

import httpx

from .download import KIWIX_WIKIPEDIA, DownloadError, fetch_catalog

PINNED_VERSION = "2026-06"  # fallback only, when the Kiwix listing cannot be read

PACKAGES: list[dict[str, Any]] = [
    {"id": "quick", "name": "Quick Reference", "variant": "top_mini", "approx_mb": 331, "recommended": True,
     "blurb": "The most-read articles, introductions only. Small, quick to set up, and plenty for most questions on a mesh.",
     "good_for": "Start here."},
    {"id": "popular", "name": "Popular Articles", "variant": "top_nopic", "approx_mb": 2239, "recommended": False,
     "blurb": "The same popular articles with their full text, so answers can draw on more detail.",
     "good_for": "More depth on common topics."},
    {"id": "complete-compact", "name": "Complete Wikipedia (Compact)", "variant": "all_mini", "approx_mb": 12531,
     "recommended": False, "blurb": "Every English article, introductions only. The widest coverage for its size.",
     "good_for": "Obscure topics, at a moderate size."},
    {"id": "complete", "name": "Complete Wikipedia (Full Text)", "variant": "all_nopic", "approx_mb": 52690,
     "recommended": False, "blurb": "Every English article with full text and no images. Very large and slow to index.",
     "good_for": "A permanent, complete library on a big disk."},
]
BY_ID = {p["id"]: p for p in PACKAGES}
DISK_MARGIN = 1.3  # the search index is stored beside the file, so require headroom beyond the download itself


def size_to_mb(size: str) -> int:
    """'283M' -> 283, '2.6G' -> 2662. Unknown units return 0."""
    m = re.fullmatch(r"([\d.]+)\s*([KMGT])", size.strip())
    if not m:
        return 0
    return round(float(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024, "T": 1024 * 1024}[m.group(2)])


def package_for_filename(name: str) -> dict[str, Any] | None:
    for p in PACKAGES:
        if name.startswith(f"wikipedia_en_{p['variant']}_"):
            return p
    return None


async def resolve(pkg: dict[str, Any], transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    """The newest file for a package: from Kiwix if reachable, else the pinned fallback."""
    try:
        for c in await fetch_catalog(transport):
            if c["variant"] == pkg["variant"]:
                return {"url": c["url"], "filename": c["filename"], "version": c["date"],
                        "size_mb": size_to_mb(c["size"]) or pkg["approx_mb"], "source": "kiwix"}
    except DownloadError:
        pass
    name = f"wikipedia_en_{pkg['variant']}_{PINNED_VERSION}.zim"
    return {"url": KIWIX_WIKIPEDIA + name, "filename": name, "version": PINNED_VERSION,
            "size_mb": pkg["approx_mb"], "source": "pinned"}
