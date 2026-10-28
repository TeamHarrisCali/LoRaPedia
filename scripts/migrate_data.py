"""Make a COPY of a LoRaPedia data folder work in a new place (typically inside Docker). Standard library only.

    python migrate_data.py /data --ollama-url http://ollama:11434

Two things in the data refer to the old machine and break when it moves:
  * the library records the absolute path of each Wikipedia file (index.db, table zims);
  * saved settings may point at Ollama on `localhost`, which inside a container is the container itself (lorapedia.db).
Both are rewritten. Everything else (the index, the query log, every other setting) is left exactly as it was. It is idempotent,
and it refuses to touch a folder that is not a LoRaPedia data folder. It never runs against a live server's files: the shell
wrapper (migrate-to-docker.sh) works on a copy.
"""
import argparse
import json
import os
import sqlite3
import sys
from urllib.parse import urlparse

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}  # urlparse() strips the brackets from [::1]


def _is_local(url) -> bool:
    try:
        return (urlparse(url).hostname or "") in LOCAL_HOSTS
    except ValueError:
        return False


def patch(data_dir: str, container_data: str | None = None, ollama_url: str | None = None) -> dict:
    """Rewrite paths and Ollama URLs in a data folder. Returns what it did. Raises FileNotFoundError / ValueError if this is not a data folder."""
    index_db, app_db = os.path.join(data_dir, "index.db"), os.path.join(data_dir, "lorapedia.db")
    for p in (index_db, app_db):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"{p} not found: this does not look like a LoRaPedia data folder")
    base = container_data or data_dir
    report = {"zims": [], "missing": [], "settings": {}}

    ix = sqlite3.connect(index_db)
    try:
        for name, old in ix.execute("SELECT name, path FROM zims").fetchall():
            present = os.path.isfile(os.path.join(data_dir, "zim", name))
            new = os.path.join(base, "zim", name)
            if present:
                if old != new:
                    ix.execute("UPDATE zims SET path=? WHERE name=?", (new, name))
                report["zims"].append({"name": name, "from": old, "to": new, "changed": old != new})
            else:
                report["missing"].append({"name": name, "path": old})  # left alone: the file is not in the folder
        ix.commit()
    finally:
        ix.close()

    app = sqlite3.connect(app_db)
    try:
        row = app.execute("SELECT v FROM kv WHERE k='settings'").fetchone()
        if row and ollama_url:
            settings = json.loads(row[0])
            targets = {"llm_base_url": ollama_url.rstrip("/") + "/v1", "embed_base_url": ollama_url.rstrip("/")}
            for key, value in targets.items():
                old = settings.get(key)
                if old and _is_local(old):  # a deliberately remote Ollama is left alone
                    settings[key] = value
                    report["settings"][key] = {"from": old, "to": value}
            if report["settings"]:
                app.execute("UPDATE kv SET v=? WHERE k='settings'", (json.dumps(settings),))
                app.commit()
    finally:
        app.close()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("data_dir")
    ap.add_argument("--container-data", help="where this folder will be seen by LoRaPedia (default: where it is now)")
    ap.add_argument("--ollama-url", help="e.g. http://ollama:11434; saved settings pointing at localhost are repointed here")
    args = ap.parse_args(argv)
    try:
        r = patch(args.data_dir, args.container_data, args.ollama_url)
    except (FileNotFoundError, ValueError, sqlite3.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for z in r["zims"]:
        print(f"library: {z['name']}: " + (f"{z['from']} -> {z['to']}" if z["changed"] else "already correct"))
    for m in r["missing"]:
        print(f"WARNING: library entry {m['name']} has no file in the zim folder; left as is", file=sys.stderr)
    for k, v in r["settings"].items():
        print(f"setting {k}: {v['from']} -> {v['to']}")
    if not r["settings"]:
        print("settings: nothing to change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
