from dirsync import should_skip


class TestShouldSkip:
    def test_no_patterns(self):
        assert should_skip("file.txt", [], []) is False

    def test_exclude_matches_basename(self):
        assert should_skip("file.txt", [], ["*.txt"]) is True

    def test_exclude_matches_full_path(self):
        assert should_skip("subdir/file.txt", [], ["subdir/*"]) is True

    def test_exclude_no_match(self):
        assert should_skip("file.txt", [], ["*.log"]) is False

    def test_include_matches(self):
        assert should_skip("file.txt", ["*.txt"], []) is False

    def test_include_no_match(self):
        assert should_skip("file.txt", ["*.log"], []) is True

    def test_include_matches_full_path(self):
        assert should_skip("data/file.csv", ["data/*"], []) is False

    def test_exclude_overrides_include(self):
        assert should_skip("file.txt", ["*.txt"], ["file.txt"]) is True

    def test_exclude_wildcard_partial(self):
        assert should_skip("backup.tar.gz", [], ["*.gz"]) is True

    def test_multiple_exclude_patterns(self):
        assert should_skip("file.log", [], ["*.txt", "*.log"]) is True
        assert should_skip("file.txt", [], ["*.txt", "*.log"]) is True
        assert should_skip("file.csv", [], ["*.txt", "*.log"]) is False

    def test_multiple_include_patterns(self):
        assert should_skip("file.txt", ["*.txt", "*.csv"], []) is False
        assert should_skip("file.csv", ["*.txt", "*.csv"], []) is False
        assert should_skip("file.log", ["*.txt", "*.csv"], []) is True

    def test_empty_basename(self):
        assert should_skip("", [], []) is False

    def test_directory_path_exclude(self):
        assert should_skip("output", [], ["output"]) is True
