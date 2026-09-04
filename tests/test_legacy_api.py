import sqlite3

from dirsync import (
    get_file_state,
    upsert_file,
    mark_in_progress,
    mark_verified,
    mark_failed,
    mark_pending,
    get_stats,
    StateManager,
)


class TestLegacyWithRawConnection:
    def _make_conn(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                rel_path     TEXT PRIMARY KEY,
                size         INTEGER,
                mtime_ns     INTEGER,
                status       TEXT DEFAULT 'pending',
                offset       INTEGER DEFAULT 0,
                attempts     INTEGER DEFAULT 0,
                dest_missing INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        return conn

    def test_upsert_and_get(self, tmp_path):
        conn = self._make_conn(tmp_path)
        upsert_file(conn, "a.txt", 100, 12345, "pending")
        result = get_file_state(conn, "a.txt")
        assert result is not None
        assert result["status"] == "pending"
        assert result["size"] == 100

    def test_mark_in_progress(self, tmp_path):
        conn = self._make_conn(tmp_path)
        upsert_file(conn, "a.txt", 100, 12345, "pending")
        mark_in_progress(conn, "a.txt", 42)
        result = get_file_state(conn, "a.txt")
        assert result["status"] == "in-progress"
        assert result["offset"] == 42

    def test_mark_verified(self, tmp_path):
        conn = self._make_conn(tmp_path)
        upsert_file(conn, "a.txt", 100, 12345, "pending")
        mark_verified(conn, "a.txt")
        result = get_file_state(conn, "a.txt")
        assert result["status"] == "verified"

    def test_mark_failed(self, tmp_path):
        conn = self._make_conn(tmp_path)
        upsert_file(conn, "a.txt", 100, 12345, "pending")
        mark_failed(conn, "a.txt")
        result = get_file_state(conn, "a.txt")
        assert result["status"] == "failed"

    def test_mark_pending(self, tmp_path):
        conn = self._make_conn(tmp_path)
        upsert_file(conn, "a.txt", 100, 12345, "verified")
        mark_pending(conn, "a.txt")
        result = get_file_state(conn, "a.txt")
        assert result["status"] == "pending"

    def test_get_stats(self, tmp_path):
        conn = self._make_conn(tmp_path)
        upsert_file(conn, "a.txt", 100, 1, "verified")
        upsert_file(conn, "b.txt", 200, 2, "failed")
        stats = get_stats(conn)
        assert stats["verified"]["count"] == 1
        assert stats["failed"]["count"] == 1


class TestLegacyWithStateManager:
    def test_delegates_to_state_manager(self, state_db):
        upsert_file(state_db, "a.txt", 100, 12345, "pending")
        result = get_file_state(state_db, "a.txt")
        assert result is not None
        assert result["status"] == "pending"

    def test_mark_in_progress_delegates(self, state_db):
        upsert_file(state_db, "a.txt", 100, 12345, "pending")
        mark_in_progress(state_db, "a.txt", 42)
        result = get_file_state(state_db, "a.txt")
        assert result["status"] == "in-progress"

    def test_mark_verified_delegates(self, state_db):
        upsert_file(state_db, "a.txt", 100, 12345, "pending")
        mark_verified(state_db, "a.txt")
        result = get_file_state(state_db, "a.txt")
        assert result["status"] == "verified"

    def test_mark_failed_delegates(self, state_db):
        upsert_file(state_db, "a.txt", 100, 12345, "pending")
        mark_failed(state_db, "a.txt")
        result = get_file_state(state_db, "a.txt")
        assert result["status"] == "failed"

    def test_mark_pending_delegates(self, state_db):
        upsert_file(state_db, "a.txt", 100, 12345, "verified")
        mark_pending(state_db, "a.txt")
        result = get_file_state(state_db, "a.txt")
        assert result["status"] == "pending"

    def test_get_stats_delegates(self, state_db):
        upsert_file(state_db, "a.txt", 100, 1, "verified")
        stats = get_stats(state_db)
        assert stats["verified"]["count"] == 1
