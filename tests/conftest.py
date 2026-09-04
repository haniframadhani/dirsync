import hashlib
import os
import sqlite3
import tempfile

import pytest

from dirsync import StateManager


@pytest.fixture
def state_db(tmp_path):
    """Create a fresh StateManager backed by tmp_path."""
    db_path = str(tmp_path / ".copy-state.db")
    src = str(tmp_path / "source")
    dst = str(tmp_path / "dest")
    sm = StateManager(db_path, src, dst)
    yield sm
    sm.close()


@pytest.fixture
def sample_source(tmp_path):
    """Create a source directory with various test files."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "small.txt").write_text("hello world")
    (src / "data.bin").write_bytes(os.urandom(1024))
    sub = src / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested content")
    return src


@pytest.fixture
def dest_dir(tmp_path):
    """Create a destination directory."""
    d = tmp_path / "dest"
    d.mkdir()
    return d
