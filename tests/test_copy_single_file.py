import os
import stat
import threading

import pytest

from dirsync import (
    copy_single_file,
    StateManager,
    _shutdown,
    SMALL_FILE_THRESHOLD,
)


class TestCopySingleFile:
    def _setup(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()
        db_path = str(tmp_path / ".copy-state.db")
        sm = StateManager(db_path, str(src), str(dst))
        return src, dst, sm

    def test_basic_copy(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        (src / "a.txt").write_text("hello")
        status, bytes_copied = copy_single_file(
            str(src / "a.txt"), str(dst / "a.txt"), "a.txt", sm
        )
        assert status == "verified"
        assert bytes_copied == 5
        assert (dst / "a.txt").read_text() == "hello"
        sm.close()

    def test_skip_verified_file(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        f = src / "a.txt"
        f.write_text("hello")
        (dst / "a.txt").write_text("hello")
        sm.upsert_file("a.txt", 5, f.stat().st_mtime_ns, "verified")
        status, _ = copy_single_file(
            str(src / "a.txt"), str(dst / "a.txt"), "a.txt", sm
        )
        assert status == "skipped"
        sm.close()

    def test_resync_source_changed(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        f = src / "a.txt"
        f.write_text("hello")
        (dst / "a.txt").write_text("hello")
        sm.upsert_file("a.txt", 999, 99999, "verified")
        status, bytes_copied = copy_single_file(
            str(src / "a.txt"), str(dst / "a.txt"), "a.txt", sm
        )
        assert status == "verified"
        assert bytes_copied == 5
        sm.close()

    def test_symlink_copy(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        target = src / "target.txt"
        target.write_text("target")
        link = src / "link.txt"
        os.symlink(str(target), str(link))
        status, _ = copy_single_file(
            str(src / "link.txt"), str(dst / "link.txt"), "link.txt", sm
        )
        assert status == "verified"
        assert os.path.islink(str(dst / "link.txt"))
        sm.close()

    def test_skip_shutdown(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        (src / "a.txt").write_text("hello")
        _shutdown.set()
        try:
            status, _ = copy_single_file(
                str(src / "a.txt"), str(dst / "a.txt"), "a.txt", sm
            )
            assert status == "skipped"
        finally:
            _shutdown.clear()
        sm.close()

    def test_skip_by_pattern(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        (src / "a.log").write_text("log")
        status, _ = copy_single_file(
            str(src / "a.log"),
            str(dst / "a.log"),
            "a.log",
            sm,
            exclude_patterns=["*.log"],
        )
        assert status == "skipped"
        sm.close()

    def test_source_not_statable(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        status, _ = copy_single_file(
            str(src / "nonexistent.txt"), str(dst / "a.txt"), "a.txt", sm
        )
        assert status == "failed"
        sm.close()

    def test_in_progress_resume(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        content = os.urandom(1024)
        (src / "a.bin").write_bytes(content)
        dst_f = dst / "a.bin"
        dst_f.write_bytes(content[:512])
        sm.upsert_file(
            "a.bin", 1024, (src / "a.bin").stat().st_mtime_ns, "in-progress", offset=512
        )
        status, bytes_copied = copy_single_file(
            str(src / "a.bin"), str(dst / "a.bin"), "a.bin", sm
        )
        assert status == "verified"
        assert dst_f.read_bytes() == content
        sm.close()

    def test_large_file(self, tmp_path):
        src, dst, sm = self._setup(tmp_path)
        content = os.urandom(SMALL_FILE_THRESHOLD + 100)
        (src / "large.bin").write_bytes(content)
        status, bytes_copied = copy_single_file(
            str(src / "large.bin"), str(dst / "large.bin"), "large.bin", sm
        )
        assert status == "verified"
        assert bytes_copied == len(content)
        assert dst / "large.bin"
        sm.close()
