"""scripts/migrate_data.py: making a copied data folder work somewhere else."""
import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from lorapedia.config import Settings
from lorapedia.index import Index
from lorapedia.store import Store

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("migrate_data", ROOT / "scripts" / "migrate_data.py")
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

OLD = "/home/someone/LoRaPedia/data"


def make_data(tmp_path, files=("wikipedia_en_top_mini_2026-09.zim",), registered=None, **settings):
    data = tmp_path / "data"
    (data / "zim").mkdir(parents=True)
    for f in files:
        (data / "zim" / f).write_bytes(b"zim")
    ix = Index(data / "index.db")
    for name in registered if registered is not None else files:
        ix.add_zim(name, f"{OLD}/zim/{name}", "T", 10, 5)
    ix.db.close()
    store = Store(data / "lorapedia.db")
    store.save_settings(Settings(**settings))
    return data


def paths(data):
    return dict(sqlite3.connect(str(data / "index.db")).execute("SELECT name, path FROM zims").fetchall())


def saved(data):
    return json.loads(sqlite3.connect(str(data / "lorapedia.db")).execute("SELECT v FROM kv WHERE k='settings'").fetchone()[0])


def test_library_paths_are_repointed_to_the_new_location(tmp_path):
    data = make_data(tmp_path)
    r = M.patch(str(data), container_data="/data")
    assert paths(data) == {"wikipedia_en_top_mini_2026-09.zim": "/data/zim/wikipedia_en_top_mini_2026-09.zim"}
    assert r["zims"][0]["changed"] and r["missing"] == []


def test_localhost_ollama_urls_become_the_compose_service(tmp_path):
    data = make_data(tmp_path, llm_base_url="http://localhost:11434/v1", embed_base_url="http://127.0.0.1:11434")
    M.patch(str(data), ollama_url="http://ollama:11434/")
    s = saved(data)
    assert s["llm_base_url"] == "http://ollama:11434/v1" and s["embed_base_url"] == "http://ollama:11434"


def test_a_deliberately_remote_ollama_is_left_alone(tmp_path):
    data = make_data(tmp_path, llm_base_url="http://gpu-box.lan:11434/v1", embed_base_url="http://localhost:11434")
    M.patch(str(data), ollama_url="http://ollama:11434")
    s = saved(data)
    assert s["llm_base_url"] == "http://gpu-box.lan:11434/v1" and s["embed_base_url"] == "http://ollama:11434"


def test_nothing_else_is_touched(tmp_path):
    data = make_data(tmp_path, hop_limit=5, byte_limit=180, blocklist=["!abcd0001"], llm_model="llama3.2:3b")
    before = saved(data)
    M.patch(str(data), container_data="/data", ollama_url="http://ollama:11434")
    after = saved(data)
    changed = {k for k in before if before[k] != after[k]}
    assert changed == {"llm_base_url", "embed_base_url"}
    assert after["byte_limit"] == 180 and after["blocklist"] == ["!abcd0001"] and after["hop_limit"] == 5


def test_a_library_entry_without_a_file_is_reported_not_invented(tmp_path):
    data = make_data(tmp_path, files=("a.zim",), registered=("a.zim", "gone.zim"))
    r = M.patch(str(data), container_data="/data")
    assert paths(data)["gone.zim"] == f"{OLD}/zim/gone.zim"  # untouched
    assert [m["name"] for m in r["missing"]] == ["gone.zim"] and paths(data)["a.zim"] == "/data/zim/a.zim"


def test_it_is_idempotent(tmp_path):
    data = make_data(tmp_path)
    M.patch(str(data), container_data="/data", ollama_url="http://ollama:11434")
    again = M.patch(str(data), container_data="/data", ollama_url="http://ollama:11434")
    assert not any(z["changed"] for z in again["zims"]) and again["settings"] == {}


def test_default_location_is_where_the_folder_is_now(tmp_path):
    data = make_data(tmp_path)
    M.patch(str(data))
    assert paths(data)["wikipedia_en_top_mini_2026-09.zim"] == str(data / "zim" / "wikipedia_en_top_mini_2026-09.zim")


def test_it_refuses_a_folder_that_is_not_lorapedia_data(tmp_path):
    with pytest.raises(FileNotFoundError, match="does not look like"):
        M.patch(str(tmp_path))
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_data.py"), str(tmp_path)], capture_output=True, text=True)
    assert run.returncode == 1 and "error:" in run.stderr


def test_the_command_line_reports_what_it_did(tmp_path):
    data = make_data(tmp_path, llm_base_url="http://localhost:11434/v1", embed_base_url="http://localhost:11434")
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_data.py"), str(data), "--container-data", "/data",
                          "--ollama-url", "http://ollama:11434"], capture_output=True, text=True)
    assert run.returncode == 0 and "-> /data/zim/wikipedia_en_top_mini_2026-09.zim" in run.stdout and "llm_base_url" in run.stdout
