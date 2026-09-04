from dirsync import _human_size, _format_time


class TestHumanSize:
    def test_zero(self):
        assert _human_size(0) == "0.0B"

    def test_bytes(self):
        assert _human_size(512) == "512.0B"

    def test_one_kib(self):
        assert _human_size(1024) == "1.0KiB"

    def test_kib_range(self):
        assert _human_size(1536) == "1.5KiB"

    def test_one_mib(self):
        assert _human_size(1024**2) == "1.0MiB"

    def test_one_gib(self):
        assert _human_size(1024**3) == "1.0GiB"

    def test_one_tib(self):
        assert _human_size(1024**4) == "1.0TiB"

    def test_one_pib(self):
        assert _human_size(1024**5) == "1.0PiB"

    def test_large_pib(self):
        assert _human_size(1024**6) == "1024.0PiB"

    def test_fractional(self):
        assert _human_size(1234567) == "1.2MiB"


class TestFormatTime:
    def test_negative(self):
        assert _format_time(-1) == "??:??"

    def test_zero(self):
        assert _format_time(0) == "00:00"

    def test_seconds_only(self):
        assert _format_time(45) == "00:45"

    def test_minutes_and_seconds(self):
        assert _format_time(125) == "02:05"

    def test_one_hour(self):
        assert _format_time(3600) == "1h00m"

    def test_hours_and_minutes(self):
        assert _format_time(3661) == "1h01m"

    def test_multiple_hours(self):
        assert _format_time(7200) == "2h00m"

    def test_exact_minute_boundary(self):
        assert _format_time(60) == "01:00"

    def test_exact_hour_boundary(self):
        assert _format_time(3600) == "1h00m"
