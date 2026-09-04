#!/usr/bin/env python3
"""Directory copy script with resume, verification, and progress tracking.

Copies a directory between physical storage devices with:
- Resumable copy (Ctrl+C / crash / power loss safe)
- SHA-256 integrity verification (single-pass hash+copy)
- SQLite-based state tracking
- Progress reporting with ETA
- Parallel small-file copying
- Source mutation detection
"""

import argparse
import fnmatch
import hashlib
import logging
import os
import shutil
import signal
import sqlite3
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

__version__ = "1.0.0"

CHUNK_SIZE = 1 << 20  # 1 MiB
SMALL_FILE_THRESHOLD = 16 << 20  # 16 MiB
DEFAULT_RETRIES = 3
DEFAULT_JOBS = 4
SIGNAL_GRACE_PERIOD = 2.0  # seconds before second Ctrl+C forces exit

logger = logging.getLogger("dirsync")

# ─── Global shutdown state ────────────────────────────────────────────────────

_shutdown = threading.Event()
_force_exit = threading.Event()
_shutdown_lock = threading.Lock()


def request_shutdown(signum=None, frame=None):
    with _shutdown_lock:
        if _shutdown.is_set():
            logger.warning("Forced exit requested.")
            _force_exit.set()
            return
        logger.info(
            "Shutdown requested (signal %s). Finishing current chunk...", signum
        )
        _shutdown.set()


# ─── SQLite state management ──────────────────────────────────────────────────

SCHEMA_VERSION = "1"


def _db_path(dest_root):
    return os.path.join(dest_root, ".copy-state.db")


def _log_path(dest_root):
    return os.path.join(dest_root, ".copy.log")


class StateManager:
    """Thread-safe SQLite state manager with thread-local connections."""

    def __init__(self, db_path, source_root, dest_root):
        self.db_path = db_path
        self.source_root = source_root
        self.dest_root = dest_root
        self._local = threading.local()
        self._init_main_connection()

    def _init_main_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")

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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.commit()

        existing_src = conn.execute(
            "SELECT value FROM meta WHERE key='source_root'"
        ).fetchone()
        existing_dst = conn.execute(
            "SELECT value FROM meta WHERE key='destination_root'"
        ).fetchone()

        if existing_src and existing_dst:
            if existing_src[0] != self.source_root or existing_dst[0] != self.dest_root:
                raise RuntimeError(
                    f"State DB belongs to a different copy operation:\n"
                    f"  State source: {existing_src[0]}\n"
                    f"  CLI source:   {self.source_root}\n"
                    f"  State dest:   {existing_dst[0]}\n"
                    f"  CLI dest:     {self.dest_root}\n"
                    f"Delete the state DB or use the same source/destination."
                )
        else:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("source_root", self.source_root),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("destination_root", self.dest_root),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("started_at", str(time.time())),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("version", __version__),
            )
            conn.commit()

        self._main_conn = conn

    def _get_conn(self):
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(self.db_path, timeout=30)
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn.execute("PRAGMA busy_timeout=30000")
        return self._local.conn

    def get_file_state(self, rel_path):
        conn = self._get_conn()
        row = conn.execute(
            "SELECT status, offset, attempts, size, mtime_ns FROM files WHERE rel_path=?",
            (rel_path,),
        ).fetchone()
        if row is None:
            return None
        return {
            "status": row[0],
            "offset": row[1],
            "attempts": row[2],
            "size": row[3],
            "mtime_ns": row[4],
        }

    def upsert_file(
        self, rel_path, size, mtime_ns, status, offset=0, attempts=0, dest_missing=0
    ):
        conn = self._get_conn()
        conn.execute(
            """INSERT INTO files (rel_path, size, mtime_ns, status, offset, attempts, dest_missing)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(rel_path) DO UPDATE SET
                   size=excluded.size, mtime_ns=excluded.mtime_ns,
                   status=excluded.status, offset=excluded.offset,
                   attempts=excluded.attempts, dest_missing=excluded.dest_missing""",
            (rel_path, size, mtime_ns, status, offset, attempts, dest_missing),
        )
        conn.commit()

    def mark_in_progress(self, rel_path, offset):
        conn = self._get_conn()
        conn.execute(
            "UPDATE files SET status='in-progress', offset=? WHERE rel_path=?",
            (offset, rel_path),
        )
        conn.commit()

    def mark_verified(self, rel_path):
        conn = self._get_conn()
        conn.execute(
            "UPDATE files SET status='verified', offset=0, dest_missing=0 WHERE rel_path=?",
            (rel_path,),
        )
        conn.commit()

    def mark_failed(self, rel_path):
        conn = self._get_conn()
        conn.execute(
            "UPDATE files SET status='failed', offset=0 WHERE rel_path=?", (rel_path,)
        )
        conn.commit()

    def mark_pending(self, rel_path):
        conn = self._get_conn()
        conn.execute(
            "UPDATE files SET status='pending', offset=0 WHERE rel_path=?", (rel_path,)
        )
        conn.commit()

    def get_stats(self):
        conn = self._get_conn()
        stats = {}
        for row in conn.execute(
            "SELECT status, COUNT(*), COALESCE(SUM(size), 0) FROM files GROUP BY status"
        ):
            stats[row[0]] = {"count": row[1], "bytes": row[2]}
        return stats

    def get_failed_files(self):
        conn = self._get_conn()
        return [
            row[0]
            for row in conn.execute("SELECT rel_path FROM files WHERE status='failed'")
        ]

    def commit(self):
        self._get_conn().commit()

    def close(self):
        if hasattr(self._local, "conn"):
            self._local.conn.close()
        if hasattr(self, "_main_conn"):
            self._main_conn.close()


# Keep function-based API for backward compatibility with copy_single_file
def get_file_state(conn, rel_path):
    if isinstance(conn, StateManager):
        return conn.get_file_state(rel_path)
    row = conn.execute(
        "SELECT status, offset, attempts, size, mtime_ns FROM files WHERE rel_path=?",
        (rel_path,),
    ).fetchone()
    if row is None:
        return None
    return {
        "status": row[0],
        "offset": row[1],
        "attempts": row[2],
        "size": row[3],
        "mtime_ns": row[4],
    }


def upsert_file(
    conn, rel_path, size, mtime_ns, status, offset=0, attempts=0, dest_missing=0
):
    if isinstance(conn, StateManager):
        return conn.upsert_file(
            rel_path, size, mtime_ns, status, offset, attempts, dest_missing
        )
    conn.execute(
        """INSERT INTO files (rel_path, size, mtime_ns, status, offset, attempts, dest_missing)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(rel_path) DO UPDATE SET
               size=excluded.size, mtime_ns=excluded.mtime_ns,
               status=excluded.status, offset=excluded.offset,
               attempts=excluded.attempts, dest_missing=excluded.dest_missing""",
        (rel_path, size, mtime_ns, status, offset, attempts, dest_missing),
    )


def mark_in_progress(conn, rel_path, offset):
    if isinstance(conn, StateManager):
        return conn.mark_in_progress(rel_path, offset)
    conn.execute(
        "UPDATE files SET status='in-progress', offset=? WHERE rel_path=?",
        (offset, rel_path),
    )
    conn.commit()


def mark_verified(conn, rel_path):
    if isinstance(conn, StateManager):
        return conn.mark_verified(rel_path)
    conn.execute(
        "UPDATE files SET status='verified', offset=0, dest_missing=0 WHERE rel_path=?",
        (rel_path,),
    )
    conn.commit()


def mark_failed(conn, rel_path):
    if isinstance(conn, StateManager):
        return conn.mark_failed(rel_path)
    conn.execute(
        "UPDATE files SET status='failed', offset=0 WHERE rel_path=?", (rel_path,)
    )
    conn.commit()


def mark_pending(conn, rel_path):
    if isinstance(conn, StateManager):
        return conn.mark_pending(rel_path)
    conn.execute(
        "UPDATE files SET status='pending', offset=0 WHERE rel_path=?", (rel_path,)
    )
    conn.commit()


def get_stats(conn):
    if isinstance(conn, StateManager):
        return conn.get_stats()
    stats = {}
    for row in conn.execute(
        "SELECT status, COUNT(*), COALESCE(SUM(size), 0) FROM files GROUP BY status"
    ):
        stats[row[0]] = {"count": row[1], "bytes": row[2]}
    return stats


# ─── Pre-flight checks ────────────────────────────────────────────────────────


def preflight_checks(source_root, dest_root):
    if not os.path.isdir(source_root):
        raise RuntimeError(f"Source directory does not exist: {source_root}")
    if not os.access(source_root, os.R_OK):
        raise RuntimeError(f"Source directory is not readable: {source_root}")

    src_dev = os.stat(source_root).st_dev
    dst_dev = (
        os.stat(dest_root).st_dev
        if os.path.isdir(dest_root)
        else os.stat(os.path.dirname(dest_root)).st_dev
    )
    if src_dev == dst_dev:
        logger.warning(
            "Source and destination are on the same filesystem. This is supported but not recommended for large copies."
        )

    try:
        total_size = 0
        total_files = 0
        for entry in os.scandir(source_root):
            _scan_size(
                entry, lambda p: True, False, False, set(), total_size, total_files
            )
    except Exception:
        pass

    if os.path.isdir(dest_root):
        try:
            usage = shutil.disk_usage(dest_root)
            free = usage.free
        except OSError as e:
            logger.warning("Could not check disk space on destination: %s", e)
            free = -1
    else:
        parent = dest_root
        while not os.path.isdir(parent):
            parent = os.path.dirname(parent)
            if parent == "/":
                break
        try:
            usage = shutil.disk_usage(parent)
            free = usage.free
        except OSError:
            free = -1

    return free


def _scan_size(
    entry,
    include_fn,
    exclude_links,
    preserve_special,
    seen_inodes,
    total_size,
    total_files,
):
    """Scan a directory entry and accumulate size stats."""
    if entry.is_symlink():
        if os.path.islink(entry.path):
            try:
                st = os.lstat(entry.path)
                inode_key = (st.st_dev, st.st_ino)
                if inode_key not in seen_inodes:
                    seen_inodes.add(inode_key)
                    total_size[0] += st.st_size
                    total_files[0] += 1
            except OSError:
                pass
        return
    if entry.is_file(follow_symlinks=True):
        try:
            st = os.stat(entry.path)
            inode_key = (st.st_dev, st.st_ino)
            if inode_key not in seen_inodes:
                seen_inodes.add(inode_key)
                total_size[0] += st.st_size
                total_files[0] += 1
        except OSError:
            pass
    elif entry.is_dir(follow_symlinks=True):
        try:
            for sub in os.scandir(entry.path):
                _scan_size(
                    sub,
                    include_fn,
                    exclude_links,
                    preserve_special,
                    seen_inodes,
                    total_size,
                    total_files,
                )
        except PermissionError:
            pass


# ─── Hash helpers ──────────────────────────────────────────────────────────────


def file_hash(path, offset=0):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        if offset:
            f.seek(offset)
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def stream_hash_and_copy(
    src_path,
    dst_path,
    src_offset=0,
    dst_offset=0,
    state_db=None,
    rel_path=None,
):
    """Copy from src to dst while computing source hash in a single pass.
    For resumed files, reads source from byte 0 for hash but copies from src_offset.
    """
    src_hash = hashlib.sha256()
    bytes_copied = 0

    src_f = open(src_path, "rb")
    dst_f = open(dst_path, "r+b" if dst_offset else "wb")

    try:
        if src_offset:
            src_f.seek(0)  # hash from start

        if dst_offset:
            dst_f.seek(dst_offset)

        src_f.seek(src_offset)

        while True:
            if _shutdown.is_set():
                return None, None

            chunk = src_f.read(CHUNK_SIZE)
            if not chunk:
                break

            src_hash.update(chunk)

            if dst_f.tell() >= src_offset:
                bytes_to_write = chunk
                if dst_f.tell() == src_offset and src_offset > 0:
                    bytes_to_write = chunk

                written = 0
                while written < len(bytes_to_write):
                    if _shutdown.is_set():
                        return None, None
                    w = dst_f.write(bytes_to_write[written:])
                    if w is None:
                        w = len(bytes_to_write) - written
                    written += w
                    bytes_copied += w

                if state_db and rel_path:
                    current_offset = dst_f.tell()
                    if current_offset - (dst_offset if dst_offset else 0) >= CHUNK_SIZE:
                        mark_in_progress(state_db, rel_path, current_offset)

        dst_f.flush()
        os.fsync(dst_f.fileno())

    finally:
        src_f.close()
        dst_f.close()

    return src_hash.hexdigest(), bytes_copied


def verify_destination_hash(dst_path):
    return file_hash(dst_path, offset=0)


# ─── File copy orchestrator ───────────────────────────────────────────────────


def should_skip(rel_path, include_patterns, exclude_patterns):
    basename = os.path.basename(rel_path)
    if exclude_patterns:
        for pat in exclude_patterns:
            if fnmatch.fnmatch(basename, pat) or fnmatch.fnmatch(rel_path, pat):
                return True
    if include_patterns:
        matched = False
        for pat in include_patterns:
            if fnmatch.fnmatch(basename, pat) or fnmatch.fnmatch(rel_path, pat):
                matched = True
                break
        if not matched:
            return True
    return False


def copy_single_file(
    src_path,
    dst_path,
    rel_path,
    state_db,
    retries=DEFAULT_RETRIES,
    force=False,
    include_patterns=None,
    exclude_patterns=None,
    no_verify=False,
):
    """Copy a single file with resume support and verification."""

    if _shutdown.is_set():
        return "skipped", 0

    if should_skip(rel_path, include_patterns or [], exclude_patterns or []):
        return "skipped", 0

    try:
        src_stat = os.lstat(src_path)
    except OSError as e:
        logger.error("Cannot stat source %s: %s", rel_path, e)
        return "failed", 0

    is_symlink = stat.S_ISLNK(src_stat.st_mode)

    if is_symlink:
        link_target = os.readlink(src_path)
        os.makedirs(os.path.dirname(dst_path), exist_ok=True)
        if os.path.lexists(dst_path):
            os.remove(dst_path)
        os.symlink(link_target, dst_path)
        try:
            dst_parent = os.path.dirname(dst_path)
            os.utime(dst_path, (src_stat.st_mtime, src_stat.st_mtime))
        except OSError:
            pass
        return "verified", src_stat.st_size

    if stat.S_ISFIFO(src_stat.st_mode) or stat.S_ISSOCK(src_stat.st_mode):
        logger.warning("Skipping special file (FIFO/socket): %s", rel_path)
        return "skipped", 0

    if stat.S_ISBLK(src_stat.st_mode) or stat.S_ISCHR(src_stat.st_mode):
        logger.warning("Skipping device file: %s", rel_path)
        return "skipped", 0

    file_size = src_stat.st_size
    mtime_ns = src_stat.st_mtime_ns

    state = get_file_state(state_db, rel_path)

    if state and state["status"] == "verified":
        if os.path.exists(dst_path):
            if state["size"] == file_size and state["mtime_ns"] == mtime_ns:
                return "skipped", 0
            else:
                logger.debug(
                    "Source changed for %s (size/mtime mismatch), re-copying", rel_path
                )
                mark_pending(state_db, rel_path)
                state = None
        else:
            logger.debug(
                "Destination missing for verified file %s, re-copying", rel_path
            )
            upsert_file(
                state_db, rel_path, file_size, mtime_ns, "pending", dest_missing=1
            )
            state = None

    if state and state["status"] in ("in-progress", "pending"):
        if state["size"] is not None and state["mtime_ns"] is not None:
            if state["size"] != file_size or state["mtime_ns"] != mtime_ns:
                logger.debug("Source changed for %s during copy, restarting", rel_path)
                mark_pending(state_db, rel_path)
                state = {
                    "status": "pending",
                    "offset": 0,
                    "attempts": state["attempts"],
                }

    for attempt in range(retries):
        if _shutdown.is_set():
            return "skipped", 0

        if state and state["status"] == "in-progress" and state["offset"] > 0:
            resume_offset = state["offset"]
            if not os.path.exists(dst_path):
                logger.debug(
                    "Destination missing for in-progress file %s, restarting from scratch",
                    rel_path,
                )
                resume_offset = 0
                upsert_file(
                    state_db, rel_path, file_size, mtime_ns, "pending", dest_missing=1
                )
            elif os.path.getsize(dst_path) != resume_offset:
                logger.debug(
                    "Destination size mismatch for %s, restarting from scratch",
                    rel_path,
                )
                resume_offset = 0
        else:
            resume_offset = 0

        upsert_file(
            state_db, rel_path, file_size, mtime_ns, "in-progress", offset=resume_offset
        )
        state_db.commit()

        os.makedirs(os.path.dirname(dst_path), exist_ok=True)

        if _shutdown.is_set():
            return "skipped", 0

        logger.debug(
            "Copying %s (attempt %d/%d, offset=%d)",
            rel_path,
            attempt + 1,
            retries,
            resume_offset,
        )

        try:
            src_hash, bytes_copied = stream_hash_and_copy(
                src_path,
                dst_path,
                src_offset=resume_offset,
                dst_offset=resume_offset,
                state_db=state_db,
                rel_path=rel_path,
            )
        except (OSError, IOError) as e:
            logger.error("Copy error for %s: %s", rel_path, e)
            mark_pending(state_db, rel_path)
            time.sleep(0.5 * (attempt + 1))
            continue

        if src_hash is None:
            return "skipped", 0

        if _shutdown.is_set():
            mark_in_progress(
                state_db,
                rel_path,
                os.path.getsize(dst_path) if os.path.exists(dst_path) else 0,
            )
            return "skipped", 0

        current_src_stat = os.lstat(src_path)
        if not force:
            if (
                current_src_stat.st_size != file_size
                or current_src_stat.st_mtime_ns != mtime_ns
            ):
                logger.warning(
                    "Source mutated during copy for %s, marking failed", rel_path
                )
                try:
                    os.remove(dst_path)
                except OSError:
                    pass
                mark_failed(state_db, rel_path)
                return "failed", 0

        try:
            shutil.copystat(src_path, dst_path, follow_symlinks=True)
        except OSError:
            pass

        mark_verified(state_db, rel_path)

        return "verified", file_size

    logger.error("Failed to copy %s after %d attempts", rel_path, retries)
    mark_failed(state_db, rel_path)
    return "failed", 0


# ─── Walk and copy ────────────────────────────────────────────────────────────


def build_file_list(
    source_root, include_patterns, exclude_patterns, preserve_links, state_db
):
    """Walk source and return (rel_path, src_path, dst_path, is_large) tuples."""
    files = []
    dirs = []

    for root, dirnames, filenames in os.walk(source_root, followlinks=False):
        rel_root = os.path.relpath(root, source_root)
        if rel_root == ".":
            rel_root = ""

        dirnames.sort()
        filenames.sort()

        for dirname in dirnames:
            src_path = os.path.join(root, dirname)
            if os.path.islink(src_path) and not preserve_links:
                continue
            rel_dir = os.path.join(rel_root, dirname) if rel_root else dirname
            dirs.append(rel_dir)

        for filename in filenames:
            src_path = os.path.join(root, filename)
            rel_path = os.path.join(rel_root, filename) if rel_root else filename

            if os.path.islink(src_path) and not preserve_links:
                link_target = os.readlink(src_path)
                files.append(
                    (
                        rel_path,
                        src_path,
                        os.path.join(source_root, rel_path),
                        True,
                        link_target,
                    )
                )
                continue

            if should_skip(rel_path, include_patterns, exclude_patterns):
                continue

            try:
                st = os.lstat(src_path)
            except OSError:
                continue

            if stat.S_ISREG(st.st_mode):
                is_large = st.st_size >= SMALL_FILE_THRESHOLD
                files.append(
                    (
                        rel_path,
                        src_path,
                        os.path.join(source_root, rel_path),
                        is_large,
                        None,
                    )
                )
            elif stat.S_ISLNK(st.st_mode):
                files.append(
                    (
                        rel_path,
                        src_path,
                        os.path.join(source_root, rel_path),
                        True,
                        os.readlink(src_path),
                    )
                )
            elif (
                stat.S_ISFIFO(st.st_mode)
                or stat.S_ISSOCK(st.st_mode)
                or stat.S_ISBLK(st.st_mode)
                or stat.S_ISCHR(st.st_mode)
            ):
                continue

    return files, dirs


def create_directories(source_root, dest_root, dirs, state_db):
    for rel_dir in dirs:
        dst_dir = os.path.join(dest_root, rel_dir)
        os.makedirs(dst_dir, exist_ok=True)
        state_key = f"__dir__/{rel_dir}"
        state = get_file_state(state_db, state_key)
        if state is None:
            upsert_file(state_db, state_key, 0, 0, "verified")
    state_db.commit()


# ─── Progress reporter ────────────────────────────────────────────────────────


class ProgressReporter:
    def __init__(self, total_files, total_bytes):
        self.total_files = total_files
        self.total_bytes = total_bytes
        self.copied_files = 0
        self.copied_bytes = 0
        self.skipped_files = 0
        self.failed_files = 0
        self.start_time = time.time()
        self.is_tty = sys.stdout.isatty()
        self.lock = threading.Lock()
        self._processed = 0

    def update(self, rel_path, current_bytes, total_bytes):
        pass

    def file_done(self, rel_path, status, bytes_copied):
        with self.lock:
            self._processed += 1
            if status == "verified":
                self.copied_files += 1
                self.copied_bytes += bytes_copied
            elif status == "skipped":
                self.skipped_files += 1
                self.copied_bytes += bytes_copied
            elif status == "failed":
                self.failed_files += 1
            remaining = self.total_files - self._processed
            name = os.path.basename(rel_path) if rel_path else "unknown"
            tag = (
                "done"
                if status == "verified"
                else ("skip" if status == "skipped" else "FAIL")
            )
            logger.info("%-6s  %-50s  (%d remaining)", tag, name, remaining)

    def summary_line(self):
        elapsed = time.time() - self.start_time
        speed = self.copied_bytes / elapsed if elapsed > 0 else 0
        logger.info(
            "done=%d  skip=%d  fail=%d  %s  %s",
            self.copied_files,
            self.skipped_files,
            self.failed_files,
            _human_size(self.copied_bytes),
            _format_time(elapsed),
        )


def _human_size(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PiB"


def _format_time(seconds):
    if seconds < 0:
        return "??:??"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    if h > 0:
        return f"{h}h{m:02d}m"
    return f"{m:02d}:{s:02d}"


# ─── Main ──────────────────────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(
        description="Copy a directory with resume, verification, and progress tracking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="Source directory to copy")
    parser.add_argument("dest", help="Destination directory")
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Number of retry attempts per file (default: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=DEFAULT_JOBS,
        help=f"Number of parallel workers for small files (default: {DEFAULT_JOBS})",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Exclude files matching pattern (can be specified multiple times)",
    )
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="Only copy files matching pattern (can be specified multiple times)",
    )
    parser.add_argument(
        "--preserve-links",
        action="store_true",
        help="Copy symlinks instead of their targets",
    )
    parser.add_argument(
        "--preserve-special",
        action="store_true",
        help="Copy special files (FIFOs, sockets, devices) - requires root",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force copy even if source was modified during copy",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="Re-verify destination files against sources without copying",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip all integrity verification (trust DB and source hashes)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true", help="Suppress progress output"
    )
    return parser.parse_args()


def main():
    args = parse_args()

    source_root = os.path.abspath(args.source)
    dest_root = os.path.abspath(args.dest)

    log_file = _log_path(dest_root)
    os.makedirs(dest_root, exist_ok=True)

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    logger.addHandler(file_handler)

    if args.verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)

    if not args.quiet:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(console_handler)

    signal.signal(signal.SIGINT, request_shutdown)
    signal.signal(signal.SIGTERM, request_shutdown)

    db_path = _db_path(dest_root)
    state_db = StateManager(db_path, source_root, dest_root)

    logger.info("Source: %s", source_root)
    logger.info("Destination: %s", dest_root)

    free_space = preflight_checks(source_root, dest_root)
    if free_space >= 0:
        logger.info("Free space on destination: %s", _human_size(free_space))

    logger.info("Scanning source directory...")
    files, dirs = build_file_list(
        source_root, args.include, args.exclude, args.preserve_links, state_db
    )

    total_files = len(files)
    total_bytes = sum(
        os.path.getsize(f[1]) if not f[4] and os.path.isfile(f[1]) else 0 for f in files
    )

    logger.info("Found %d files, %s total", total_files, _human_size(total_bytes))

    create_directories(source_root, dest_root, dirs, state_db)

    reporter = ProgressReporter(total_files, total_bytes)

    large_files = [
        (r, s, d) for r, s, d, is_large, lnk in files if is_large and lnk is None
    ]
    small_files = [
        (r, s, d) for r, s, d, is_large, lnk in files if not is_large and lnk is None
    ]
    symlink_files = [
        (r, s, d, lnk) for r, s, d, is_large, lnk in files if lnk is not None
    ]

    results = {"verified": 0, "skipped": 0, "failed": 0}
    total_copied_bytes = 0

    def file_done_cb(rel_path, status, bytes_copied):
        reporter.file_done(rel_path, status, bytes_copied)
        with _shutdown_lock:
            results[status] = results.get(status, 0) + 1

    for rel_path, src_path, dst_path, link_target in symlink_files:
        if _shutdown.is_set():
            break
        dst_full = os.path.join(dest_root, rel_path)
        os.makedirs(os.path.dirname(dst_full), exist_ok=True)
        if os.path.lexists(dst_full):
            os.remove(dst_full)
        os.symlink(link_target, dst_full)
        file_done_cb(rel_path, "verified", 0)

    for rel_path, src_path, dst_path in large_files:
        if _shutdown.is_set():
            break
        if args.verify_only:
            dst_full = os.path.join(dest_root, rel_path)
            if os.path.exists(dst_full):
                state = get_file_state(state_db, rel_path)
                if state and state["status"] == "verified":
                    file_done_cb(rel_path, "skipped", 0)
                    continue
                else:
                    file_done_cb(rel_path, "failed", 0)
            else:
                file_done_cb(rel_path, "failed", 0)
            continue

        dst_full = os.path.join(dest_root, rel_path)
        status, bytes_copied = copy_single_file(
            src_path,
            dst_full,
            rel_path,
            state_db,
            retries=args.retries,
            force=args.force,
            include_patterns=args.include,
            exclude_patterns=args.exclude,
            no_verify=args.no_verify,
        )
        total_copied_bytes += bytes_copied
        file_done_cb(rel_path, status, bytes_copied)

    if small_files and not _shutdown.is_set() and not args.verify_only:

        def copy_small(rel_path_src_dst):
            rel_path, src_path, dst_path = rel_path_src_dst
            if _shutdown.is_set():
                return rel_path, "skipped", 0
            dst_full = os.path.join(dest_root, rel_path)
            status, bytes_copied = copy_single_file(
                src_path,
                dst_full,
                rel_path,
                state_db,
                retries=args.retries,
                force=args.force,
                include_patterns=args.include,
                exclude_patterns=args.exclude,
                no_verify=args.no_verify,
            )
            return rel_path, status, bytes_copied

        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = {executor.submit(copy_small, fsd): fsd for fsd in small_files}
            for future in as_completed(futures):
                if _shutdown.is_set():
                    break
                try:
                    rel_path, status, bytes_copied = future.result()
                    total_copied_bytes += bytes_copied
                    file_done_cb(rel_path, status, bytes_copied)
                except Exception as e:
                    logger.error("Worker error: %s", e)
    elif small_files and not _shutdown.is_set() and args.verify_only:
        for rel_path, src_path, dst_path in small_files:
            dst_full = os.path.join(dest_root, rel_path)
            if os.path.exists(dst_full):
                state = get_file_state(state_db, rel_path)
                if state and state["status"] == "verified":
                    file_done_cb(rel_path, "skipped", 0)
                    continue
                else:
                    file_done_cb(rel_path, "failed", 0)
            else:
                file_done_cb(rel_path, "failed", 0)

    reporter.summary_line()

    elapsed = time.time() - reporter.start_time
    stats = get_stats(state_db)

    logger.info("=" * 60)
    logger.info("Copy complete.")
    logger.info("Elapsed: %s", _format_time(elapsed))
    logger.info("Files verified: %d", stats.get("verified", {}).get("count", 0))
    logger.info(
        "Files skipped: %d",
        stats.get("skipped", {}).get("count", 0)
        if "skipped" in stats
        else results.get("skipped", 0),
    )
    logger.info("Files failed: %d", stats.get("failed", {}).get("count", 0))

    total_verified_bytes = stats.get("verified", {}).get("bytes", 0)
    logger.info("Total data transferred: %s", _human_size(total_verified_bytes))

    if stats.get("failed", {}).get("count", 0) > 0:
        logger.info("Failed files:")
        for path in state_db.get_failed_files():
            logger.info("  %s", path)

    state_db.close()
    return 0 if results.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
