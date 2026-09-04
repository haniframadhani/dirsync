import os
import stat

import pytest

from dirsync import preflight_checks


class TestPreflightChecks:
    def test_source_does_not_exist(self, tmp_path):
        src = tmp_path / "nonexistent"
        dst = tmp_path / "dest"
        dst.mkdir()
        with pytest.raises(RuntimeError, match="does not exist"):
            preflight_checks(str(src), str(dst))

    def test_source_not_readable(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        os.chmod(str(src), 0o000)
        dst = tmp_path / "dest"
        dst.mkdir()
        try:
            with pytest.raises(RuntimeError, match="not readable"):
                preflight_checks(str(src), str(dst))
        finally:
            os.chmod(str(src), 0o755)

    def test_returns_free_space(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        dst = tmp_path / "dest"
        dst.mkdir()
        free = preflight_checks(str(src), str(dst))
        assert isinstance(free, int)
        assert free >= 0

    def test_dest_does_not_exist(self, tmp_path):
        src = tmp_path / "source"
        src.mkdir()
        dst = tmp_path / "dest_subdir"
        free = preflight_checks(str(src), str(dst))
        assert free >= 0

    def test_same_filesystem_warning(self, tmp_path, caplog):
        import logging

        src = tmp_path / "source"
        src.mkdir()
        dst = tmp_path / "dest"
        dst.mkdir()
        with caplog.at_level(logging.WARNING):
            preflight_checks(str(src), str(dst))
        assert "same filesystem" in caplog.text.lower()
