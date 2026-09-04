import os

import pytest

from dirsync import build_file_list, SMALL_FILE_THRESHOLD


class TestBuildFileList:
    def _make_state_db(self, tmp_path):
        from dirsync import StateManager

        db_path = str(tmp_path / ".copy-state.db")
        src = str(tmp_path / "source")
        dst = str(tmp_path / "dest")
        sm = StateManager(db_path, src, dst)
        return sm

    def test_flat_directory(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "a.txt").write_text("a")
        (src / "b.txt").write_text("b")
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], False, sm)
        names = [f[0] for f in files]
        assert sorted(names) == ["a.txt", "b.txt"]
        sm.close()

    def test_nested_directory(self, tmp_path):
        src = tmp_path / "source"
        sub = src / "subdir"
        sub.mkdir(parents=True)
        (src / "a.txt").write_text("a")
        (sub / "b.txt").write_text("b")
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], False, sm)
        names = [f[0] for f in files]
        assert "a.txt" in names
        assert os.path.join("subdir", "b.txt") in names
        assert "subdir" in dirs
        sm.close()

    def test_symlinks_resolved_by_default(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = src / "link.txt"
        os.symlink(str(target), str(link))
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], False, sm)
        assert len(files) == 1
        assert files[0][0] == "link.txt"
        assert files[0][4] is not None  # link_target
        sm.close()

    def test_symlinks_preserved(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        target = tmp_path / "target.txt"
        target.write_text("target")
        link = src / "link.txt"
        os.symlink(str(target), str(link))
        (src / "real.txt").write_text("real")
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], True, sm)
        names = [f[0] for f in files]
        assert "link.txt" in names
        assert "real.txt" in names
        sm.close()

    def test_exclude_pattern(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "a.txt").write_text("a")
        (src / "b.log").write_text("b")
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], ["*.log"], False, sm)
        names = [f[0] for f in files]
        assert "a.txt" in names
        assert "b.log" not in names
        sm.close()

    def test_include_pattern(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        (src / "a.txt").write_text("a")
        (src / "b.log").write_text("b")
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), ["*.txt"], [], False, sm)
        names = [f[0] for f in files]
        assert "a.txt" in names
        assert "b.log" not in names
        sm.close()

    def test_large_file_classification(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        small = src / "small.bin"
        small.write_bytes(os.urandom(100))
        large = src / "large.bin"
        large.write_bytes(os.urandom(SMALL_FILE_THRESHOLD + 1))
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], False, sm)
        file_map = {f[0]: f[3] for f in files}
        assert file_map["small.bin"] is False
        assert file_map["large.bin"] is True
        sm.close()

    def test_empty_directory(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], False, sm)
        assert files == []
        assert dirs == []
        sm.close()

    def test_fifo_excluded(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        fifo_path = src / "my_fifo"
        os.mkfifo(str(fifo_path))
        sm = self._make_state_db(tmp_path)
        files, dirs = build_file_list(str(src), [], [], False, sm)
        names = [f[0] for f in files]
        assert "my_fifo" not in names
        sm.close()
