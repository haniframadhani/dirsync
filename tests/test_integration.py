import os
import sys

import pytest

from dirsync import main, StateManager, _shutdown


class TestIntegration:
    def _run_main(self, src, dst, extra_args=None):
        args = ["dirsync", str(src), str(dst)]
        if extra_args:
            args.extend(extra_args)
        sys.argv = args
        _shutdown.clear()
        return main()

    def test_copy_small_files(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        (src / "b.txt").write_text("world")
        sub = src / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("nested")
        result = self._run_main(src, dst)
        assert result == 0
        assert (dst / "a.txt").read_text() == "hello"
        assert (dst / "b.txt").read_text() == "world"
        assert (dst / "sub" / "c.txt").read_text() == "nested"

    def test_copy_with_exclude(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        (src / "b.log").write_text("log")
        result = self._run_main(src, dst, ["--exclude", "*.log"])
        assert result == 0
        assert (dst / "a.txt").read_text() == "hello"
        assert not (dst / "b.log").exists()

    def test_copy_with_include(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        (src / "b.log").write_text("log")
        result = self._run_main(src, dst, ["--include", "*.txt"])
        assert result == 0
        assert (dst / "a.txt").read_text() == "hello"
        assert not (dst / "b.log").exists()

    def test_copy_preserves_symlinks(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        target = src / "target.txt"
        target.write_text("target")
        link = src / "link.txt"
        os.symlink(str(target), str(link))
        result = self._run_main(src, dst, ["--preserve-links"])
        assert result == 0
        assert os.path.islink(str(dst / "link.txt"))
        assert os.readlink(str(dst / "link.txt")) == str(target)

    def test_verify_only_passes(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        dst.mkdir()
        (src / "a.txt").write_text("hello")
        (dst / "a.txt").write_text("hello")
        db_path = str(dst / ".copy-state.db")
        sm = StateManager(db_path, str(src), str(dst))
        sm.upsert_file("a.txt", 5, (src / "a.txt").stat().st_mtime_ns, "verified")
        sm.close()
        result = self._run_main(src, dst, ["--verify-only"])
        assert result == 0

    def test_returns_zero_on_success(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "file.txt").write_text("content")
        result = self._run_main(src, dst)
        assert result == 0

    def test_creates_state_db(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "file.txt").write_text("content")
        self._run_main(src, dst)
        assert (dst / ".copy-state.db").exists()

    def test_idempotent_copy(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        self._run_main(src, dst)
        result = self._run_main(src, dst)
        assert result == 0
        assert (dst / "a.txt").read_text() == "hello"

    def test_copy_with_exclude_multiple(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        src.mkdir()
        (src / "a.txt").write_text("hello")
        (src / "b.log").write_text("log")
        (src / "c.tmp").write_text("tmp")
        result = self._run_main(src, dst, ["--exclude", "*.log", "--exclude", "*.tmp"])
        assert result == 0
        assert (dst / "a.txt").read_text() == "hello"
        assert not (dst / "b.log").exists()
        assert not (dst / "c.tmp").exists()

    def test_nested_directories(self, tmp_path):
        src = tmp_path / "source"
        dst = tmp_path / "dest"
        (src / "a" / "b" / "c").mkdir(parents=True)
        (src / "a" / "b" / "c" / "deep.txt").write_text("deep")
        result = self._run_main(src, dst)
        assert result == 0
        assert (dst / "a" / "b" / "c" / "deep.txt").read_text() == "deep"
