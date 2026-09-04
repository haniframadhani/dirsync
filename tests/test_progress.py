import threading
import time

from dirsync import ProgressReporter, _human_size


class TestProgressReporter:
    def test_file_done_verified(self):
        r = ProgressReporter(10, 1000)
        r.file_done("a.txt", "verified", 500)
        assert r.copied_files == 1
        assert r.copied_bytes == 500

    def test_file_done_skipped(self):
        r = ProgressReporter(10, 1000)
        r.file_done("a.txt", "skipped", 0)
        assert r.skipped_files == 1

    def test_file_done_failed(self):
        r = ProgressReporter(10, 1000)
        r.file_done("a.txt", "failed", 0)
        assert r.failed_files == 1

    def test_multiple_files(self):
        r = ProgressReporter(3, 3000)
        r.file_done("a.txt", "verified", 1000)
        r.file_done("b.txt", "skipped", 0)
        r.file_done("c.txt", "failed", 0)
        assert r.copied_files == 1
        assert r.skipped_files == 1
        assert r.failed_files == 1
        assert r.copied_bytes == 1000

    def test_thread_safety(self):
        r = ProgressReporter(100, 10000)
        errors = []

        def worker(i):
            try:
                r.file_done(f"f{i}.txt", "verified", 100)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert r.copied_files == 100
        assert r.copied_bytes == 10000

    def test_summary_line(self, caplog):
        import logging

        r = ProgressReporter(5, 5000)
        r.file_done("a.txt", "verified", 2000)
        r.file_done("b.txt", "skipped", 0)
        with caplog.at_level(logging.INFO):
            r.summary_line()
        assert "done=1" in caplog.text
        assert "skip=1" in caplog.text

    def test_summary_no_files(self, caplog):
        import logging

        r = ProgressReporter(0, 0)
        with caplog.at_level(logging.INFO):
            r.summary_line()
        assert "done=0" in caplog.text
