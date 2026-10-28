"""Backups of the settings and question log: made, listed, downloaded, uploaded, restored, pruned."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from lorapedia.api import create_app
from lorapedia.backups import MAX_UPLOAD_BYTES, BackupError, BackupService, kind_of, verify
from lorapedia.config import Settings
from lorapedia.embed import HashEmbedder
from lorapedia.llm.base import MockLLM
from lorapedia.store import Store


class Clock:
    def __init__(self, now=1_800_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def log(store, question="what is a quasar?"):
    store.log_query(sender_id="!a", sender_name="A", question=question, parts=["a"], byte_limit=200, latency_s=1.0, sources=[], debug={})


def service(tmp_path, settings=None, clock=None):
    store = Store(tmp_path / "lorapedia.db")
    state = {"s": settings or Settings()}
    return BackupService(store, tmp_path, lambda: state["s"], clock or Clock()), store, state


def queries(path):
    return [r[0] for r in sqlite3.connect(str(path)).execute("SELECT question FROM queries ORDER BY id")]


def test_a_backup_is_a_verified_copy_that_is_listed_newest_first(tmp_path):
    svc, store, _ = service(tmp_path)
    log(store)
    clock = svc._clock
    first = svc.create("manual")
    clock.now += 60
    second = svc.create("manual")
    assert first["name"] != second["name"] and first["kind"] == "manual"
    assert [i["name"] for i in svc.list()] == [second["name"], first["name"]]
    assert queries(svc.file(first["name"])) == ["what is a quasar?"]
    assert not list(svc.folder.glob("*.tmp"))


def test_two_backups_in_the_same_second_do_not_overwrite_each_other(tmp_path):
    svc, _, _ = service(tmp_path)
    assert svc.create("manual")["name"] != svc.create("manual")["name"]
    assert len(svc.list()) == 2


def test_only_names_this_module_makes_are_accepted_so_nothing_escapes_the_folder(tmp_path):
    svc, _, _ = service(tmp_path)
    svc.create("manual")
    outside = tmp_path / "secret.db"
    outside.write_bytes(b"x")
    for bad in ("../secret.db", "..%2Fsecret.db", "/etc/passwd", "secret.db", "manual-20261028-120000.db/../../secret.db", ""):
        with pytest.raises(LookupError):
            svc.file(bad)
    assert kind_of("pre-update-0.1.3-to-0.1.4-20261028-120000.db") == "pre-update"  # the updater's own backups are listed too
    assert kind_of("notes.txt") == ""


def test_restore_brings_back_settings_and_log_and_first_saves_what_it_replaces(tmp_path):
    svc, store, _ = service(tmp_path)
    store.save_settings(Settings(byte_limit=150))
    log(store, "before")
    kept = svc.create("manual")
    store.save_settings(Settings(byte_limit=222))
    log(store, "after")
    result = svc.restore(kept["name"])
    assert store.load_settings().byte_limit == 150
    assert queries(tmp_path / "lorapedia.db") == ["before"]
    assert kind_of(result["saved_previous_as"]) == "before-restore"
    assert queries(svc.file(result["saved_previous_as"])) == ["before", "after"]  # the restore can itself be undone
    svc.restore(result["saved_previous_as"])
    assert store.load_settings().byte_limit == 222


def test_a_damaged_or_foreign_file_is_refused_and_live_data_is_left_alone(tmp_path):
    svc, store, _ = service(tmp_path)
    log(store, "keep me")
    svc.folder.mkdir()
    junk = svc.folder / "manual-20261028-120000.db"
    junk.write_bytes(b"this is not a database" * 100)
    foreign = svc.folder / "manual-20261028-130000.db"
    other = sqlite3.connect(str(foreign))
    other.execute("CREATE TABLE unrelated (x)")
    other.commit()
    other.close()
    for name in (junk.name, foreign.name):
        with pytest.raises(BackupError):
            svc.restore(name)
    assert queries(tmp_path / "lorapedia.db") == ["keep me"]
    assert not any(i["kind"] == "before-restore" for i in svc.list())  # nothing was touched, so nothing was saved


def test_a_backup_whose_settings_this_version_cannot_read_is_refused(tmp_path):
    svc, store, _ = service(tmp_path)
    good = svc.create("manual")
    db = sqlite3.connect(str(svc.file(good["name"])))
    db.execute("UPDATE kv SET v='{\"byte_limit\": 9999}' WHERE k='settings'") if db.execute("SELECT 1 FROM kv WHERE k='settings'").fetchone() \
        else db.execute("INSERT INTO kv VALUES('settings', '{\"byte_limit\": 9999}')")
    db.commit()
    db.close()
    with pytest.raises(BackupError, match="settings"):
        verify(svc.file(good["name"]))


def test_old_backups_without_newer_settings_still_restore(tmp_path):
    svc, store, _ = service(tmp_path)
    store.save_settings(Settings())
    kept = svc.create("manual")
    db = sqlite3.connect(str(svc.file(kept["name"])))
    db.execute("UPDATE kv SET v='{\"byte_limit\": 120}' WHERE k='settings'")  # an older version saved fewer fields
    db.commit()
    db.close()
    svc.restore(kept["name"])
    s = store.load_settings()
    assert s.byte_limit == 120 and s.auto_backup is True  # missing fields take their defaults


def test_upload_is_checked_then_kept_and_rejects_rubbish_and_oversize(tmp_path):
    svc, store, _ = service(tmp_path)
    log(store, "from elsewhere")
    data = svc.file(svc.create("manual")["name"]).read_bytes()
    other, _, _ = service(tmp_path / "other") if (tmp_path / "other").mkdir() is None else (None, None, None)
    added = other.add_upload(data)
    assert added["kind"] == "uploaded" and queries(other.file(added["name"])) == ["from elsewhere"]
    for bad in (b"", b"not sqlite at all", data[: len(data) // 2]):
        with pytest.raises(BackupError):
            other.add_upload(bad)
    with pytest.raises(BackupError, match="larger"):
        other.add_upload(b"x" * (MAX_UPLOAD_BYTES + 1))
    assert len(other.list()) == 1 and not list(other.folder.glob("*.tmp"))


def test_pruning_removes_only_old_automatic_backups(tmp_path):
    svc, _, _ = service(tmp_path)
    clock = svc._clock
    manual = svc.create("manual")
    pre = svc.folder / "pre-update-0.1.3-to-0.1.4-20261028-120000.db"
    pre.write_bytes(svc.file(manual["name"]).read_bytes())
    autos = []
    for _ in range(5):
        clock.now += 86400
        autos.append(svc.create("auto")["name"])
    svc.prune_auto(2)
    left = {i["name"] for i in svc.list()}
    assert set(autos[-2:]) <= left and not set(autos[:3]) & left
    assert manual["name"] in left and pre.name in left


def test_the_daily_automatic_backup_is_due_once_a_day_and_can_be_switched_off(tmp_path):
    svc, _, state = service(tmp_path)
    assert svc.auto_due()  # none yet
    svc.create("auto")
    assert not svc.auto_due()
    svc._clock.now += 23 * 3600
    assert not svc.auto_due()
    svc._clock.now += 2 * 3600
    assert svc.auto_due()
    state["s"] = Settings(auto_backup=False)
    assert not svc.auto_due()
    assert svc.status()["auto_backup"] is False


# ---- over HTTP ---------------------------------------------------------------------------------------------------


def client(tmp_path):
    opts = {"port_lister": lambda: [], "poll_s": 60}
    return TestClient(create_app(str(tmp_path), llm=MockLLM(), embedder=HashEmbedder(), radio_options=opts))


def test_api_backup_flow_make_download_change_restore(tmp_path):
    c = client(tmp_path)
    assert c.put("/api/settings", json=Settings(byte_limit=150).model_dump()).status_code == 200
    made = c.post("/api/backups").json()
    assert made["kind"] == "manual"
    listing = c.get("/api/backups").json()
    assert [i["name"] for i in listing["items"]] == [made["name"]] and listing["auto_backup"] is True

    dl = c.get(f"/api/backups/{made['name']}/download")
    assert dl.status_code == 200 and dl.content.startswith(b"SQLite format 3")

    c.put("/api/settings", json=Settings(byte_limit=222).model_dump())
    assert c.get("/api/settings").json()["byte_limit"] == 222
    r = c.post(f"/api/backups/{made['name']}/restore")
    assert r.status_code == 200 and r.json()["saved_previous_as"].startswith("before-restore-")
    assert c.get("/api/settings").json()["byte_limit"] == 150  # live settings, not just the file on disk


def test_api_names_are_validated_and_unknown_ones_are_404(tmp_path):
    c = client(tmp_path)
    for path in ("/download", ""):
        assert c.get(f"/api/backups/..%2Fetc%2Fpasswd{path}").status_code in (404, 405)
    assert c.get("/api/backups/manual-20200101-000000.db/download").status_code == 404
    assert c.post("/api/backups/manual-20200101-000000.db/restore").status_code == 404
    assert c.delete("/api/backups/manual-20200101-000000.db").status_code == 404
    made = c.post("/api/backups").json()
    assert c.delete(f"/api/backups/{made['name']}").status_code == 200
    assert c.get("/api/backups").json()["items"] == []


def test_api_upload_accepts_a_real_backup_and_rejects_anything_else(tmp_path):
    src = client(tmp_path / "a") if (tmp_path / "a").mkdir() is None else None
    made = src.post("/api/backups").json()
    data = src.get(f"/api/backups/{made['name']}/download").content
    dst = client(tmp_path / "b") if (tmp_path / "b").mkdir() is None else None
    up = dst.post("/api/backups/upload", content=data, headers={"content-type": "application/octet-stream"})
    assert up.status_code == 200 and up.json()["kind"] == "uploaded"
    assert dst.post("/api/backups/upload", content=b"hello", headers={"content-type": "application/octet-stream"}).status_code == 400
    assert len(dst.get("/api/backups").json()["items"]) == 1


def test_a_backup_description_has_exactly_the_documented_fields(tmp_path):
    svc, _, _ = service(tmp_path)
    assert set(svc.create("manual")) == {"name", "kind", "size", "ts"} == set(svc.list()[0])
