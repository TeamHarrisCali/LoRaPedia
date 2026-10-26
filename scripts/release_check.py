"""The release workflow's checks, as a script so the tests run the same code the workflow does. Standard library only.

    python scripts/release_check.py check v0.1.0     # tag is `v` + __version__, valid semver, CHANGELOG.md has a section for it
    python scripts/release_check.py notes 0.1.0      # print that CHANGELOG section (the release notes)
    python scripts/release_check.py version          # print __version__

Exit code 0 = passed; a problem goes to stderr with exit code 1. See docs/RELEASING.md. Adapted from the release check of
TeamHarrisCali/MeshtasticLLM.
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "TeamHarrisCali/LoRaPedia"
# The official semantic-versioning pattern (semver.org): "0.1", "v0.1.0" and "01.2.3" are refused.
SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$")


def read_version(root=ROOT):
    """__version__ from backend/lorapedia/__init__.py, read as text (never imported); None if missing."""
    with open(os.path.join(root, "backend", "lorapedia", "__init__.py"), encoding="utf-8") as f:
        m = re.search(r'^__version__\s*=\s*"([^"]+)"\s*$', f.read(), re.M)
    return m.group(1) if m else None


def is_semver(version):
    return bool(version) and bool(SEMVER.match(version))


def tag_for(version):
    return "v" + version


def changelog_section(text, version):
    """Body of the `## [version]` section of a Keep-a-Changelog file, up to the next `## [` heading or the link
    references at the end; None if the section is missing or empty."""
    heading = re.compile(r"^##\s+\[" + re.escape(version) + r"\](?:\s+-\s+\S.*)?\s*$", re.M)
    m = heading.search(text)
    if not m:
        return None
    rest = text[m.end():]
    end = re.search(r"^## \[|^\[[^\]]+\]:\s*\S", rest, re.M)
    body = (rest[:end.start()] if end else rest).strip()
    return body or None


def read_changelog(root=ROOT):
    with open(os.path.join(root, "CHANGELOG.md"), encoding="utf-8") as f:
        return f.read()


def check_tag(tag, root=ROOT):
    """A list of problems with releasing `tag` from this checkout (empty = fine)."""
    version = read_version(root)
    if version is None:
        return ["backend/lorapedia/__init__.py has no __version__"]
    problems = []
    if not is_semver(version):
        problems.append(f"__version__ {version!r} is not a valid semantic version (X.Y.Z)")
    if tag != tag_for(version):
        problems.append(f"the tag {tag!r} does not match the version in __init__.py (expected {tag_for(version)!r})")
    if changelog_section(read_changelog(root), version) is None:
        problems.append(f"CHANGELOG.md has no non-empty '## [{version}]' section")
    return problems


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 2 and argv[0] == "check":
        problems = check_tag(argv[1])
        for p in problems:
            print("error: " + p, file=sys.stderr)
        if not problems:
            print(f"ok: {argv[1]} matches version {read_version()} and has a CHANGELOG section")
        return 1 if problems else 0
    if len(argv) == 2 and argv[0] == "notes":
        body = changelog_section(read_changelog(), argv[1])
        if body is None:
            print(f"error: CHANGELOG.md has no non-empty '## [{argv[1]}]' section", file=sys.stderr)
            return 1
        print(body)
        return 0
    if argv == ["version"]:
        print(read_version())
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
