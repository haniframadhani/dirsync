import os
import sqlite3
import threading

import pytest

from dirsync import StateManager


class TestStateManagerSchema:
    def test_creates_db_file(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        sm = StateManager(db_path, "/src", "/dst")
        assert os.path.exists(db_path)
        sm.close()

    def test_schema_has_files_table(self, state_db):
        conn = state_db._main_conn
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "files" in tables

    def test_schema_has_meta_table(self, state_db):
        conn = state_db._main_conn
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert "meta" in tables

    def test_meta_stores_source_root(self, state_db):
        conn = state_db._main_conn
        row = conn.execute("SELECT value FROM meta WHERE key='source_root'").fetchone()
        assert row is not None
        assert row[0] == state_db.source_root

    def test_meta_stores_dest_root(self, state_db):
        conn = state_db._main_conn
        row = conn.execute(
            "SELECT value FROM meta WHERE key='destination_root'"
        ).fetchone()
        assert row is not None
        assert row[0] == state_db.dest_root

    def test_meta_stores_version(self, state_db):
        conn = state_db._main_conn
        row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
        assert row is not None
        assert row[0] == "1.0.0"


class TestStateManagerMismatchedRoots:
    def test_raises_on_different_source(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        sm1 = StateManager(db_path, "/src1", "/dst1")
        sm1.close()
        with pytest.raises(RuntimeError, match="different copy operation"):
            StateManager(db_path, "/src2", "/dst1")

    def test_raises_on_different_dest(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        sm1 = StateManager(db_path, "/src1", "/dst1")
        sm1.close()
        with pytest.raises(RuntimeError, match="different copy operation"):
            StateManager(db_path, "/src1", "/dst2")


class TestUpsertAndGetFile:
    def test_upsert_new_file(self, state_db):
        state_db.upsert_file("a.txt", 100, 12345, "pending")
        result = state_db.get_file_state("a.txt")
        assert result is not None
        assert result["status"] == "pending"
        assert result["size"] == 100
        assert result["mtime_ns"] == 12345

    def test_upsert_updates_existing(self, state_db):
        state_db.upsert_file("a.txt", 100, 12345, "pending")
        state_db.upsert_file("a.txt", 200, 99999, "in-progress")
        result = state_db.get_file_state("a.txt")
        assert result["status"] == "in-progress"
        assert result["size"] == 200
        assert result["mtime_ns"] == 99999

    def test_get_nonexistent_file(self, state_db):
        result = state_db.get_file_state("nope.txt")
        assert result is None

    def test_upsert_with_all_fields(self, state_db):
        state_db.upsert_file(
            "a.txt", 100, 12345, "in-progress", offset=50, attempts=2, dest_missing=1
        )
        result = state_db.get_file_state("a.txt")
        assert result["offset"] == 50
        assert result["attempts"] == 2


class TestMarkStatus:
    def test_mark_in_progress(self, state_db):
        state_db.upsert_file("a.txt", 100, 12345, "pending")
        state_db.mark_in_progress("a.txt", 42)
        result = state_db.get_file_state("a.txt")
        assert result["status"] == "in-progress"
        assert result["offset"] == 42

    def test_mark_verified(self, state_db):
        state_db.upsert_file("a.txt", 100, 12345, "in-progress")
        state_db.mark_verified("a.txt")
        result = state_db.get_file_state("a.txt")
        assert result["status"] == "verified"
        assert result["offset"] == 0

    def test_mark_failed(self, state_db):
        state_db.upsert_file("a.txt", 100, 12345, "in-progress")
        state_db.mark_failed("a.txt")
        result = state_db.get_file_state("a.txt")
        assert result["status"] == "failed"
        assert result["offset"] == 0

    def test_mark_pending(self, state_db):
        state_db.upsert_file("a.txt", 100, 12345, "verified")
        state_db.mark_pending("a.txt")
        result = state_db.get_file_state("a.txt")
        assert result["status"] == "pending"
        assert result["offset"] == 0


class TestGetStats:
    def test_empty_db(self, state_db):
        stats = state_db.get_stats()
        assert stats == {}

    def test_mixed_statuses(self, state_db):
        state_db.upsert_file("a.txt", 100, 1, "verified")
        state_db.upsert_file("b.txt", 200, 2, "verified")
        state_db.upsert_file("c.txt", 300, 3, "failed")
        stats = state_db.get_stats()
        assert stats["verified"]["count"] == 2
        assert stats["verified"]["bytes"] == 300
        assert stats["failed"]["count"] == 1
        assert stats["failed"]["bytes"] == 300


class TestGetFailedFiles:
    def test_no_failed(self, state_db):
        assert state_db.get_failed_files() == []

    def test_with_failed(self, state_db):
        state_db.upsert_file("a.txt", 100, 1, "verified")
        state_db.upsert_file("b.txt", 200, 2, "failed")
        state_db.upsert_file("c.txt", 300, 3, "failed")
        failed = state_db.get_failed_files()
        assert sorted(failed) == ["b.txt", "c.txt"]


class TestClose:
    def test_close_cleans_up(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        sm = StateManager(db_path, "/src", "/dst")
        sm.close()
        assert not hasattr(sm._local, "conn")


class TestThreadLocalConnections:
    def test_threads_use_separate_connections(self, state_db):
        conns = []

        def worker():
            conn = state_db._get_conn()
            conns.append(id(conn))

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(conns) == 3
        assert len(set(conns)) == 3

    def test_thread_can_write(self, state_db):
        def worker():
            state_db.upsert_file("thread.txt", 50, 111, "pending")

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        result = state_db.get_file_state("thread.txt")
        assert result is not None
        assert result["status"] == "pending"
