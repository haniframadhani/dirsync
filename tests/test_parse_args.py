import sys
import pytest

from dirsync import parse_args


class TestParseArgs:
    def test_source_and_dest_required(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync"])
        with pytest.raises(SystemExit):
            parse_args()

    def test_source_only(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src"])
        with pytest.raises(SystemExit):
            parse_args()

    def test_basic_args(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst"])
        args = parse_args()
        assert args.source == "src"
        assert args.dest == "dst"

    def test_default_retries(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst"])
        args = parse_args()
        assert args.retries == 3

    def test_custom_retries(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--retries", "5"])
        args = parse_args()
        assert args.retries == 5

    def test_default_jobs(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst"])
        args = parse_args()
        assert args.jobs == 4

    def test_custom_jobs(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--jobs", "8"])
        args = parse_args()
        assert args.jobs == 8

    def test_default_exclude(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst"])
        args = parse_args()
        assert args.exclude == []

    def test_exclude(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["dirsync", "src", "dst", "--exclude", "*.log"]
        )
        args = parse_args()
        assert args.exclude == ["*.log"]

    def test_multiple_exclude(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            ["dirsync", "src", "dst", "--exclude", "*.log", "--exclude", "*.tmp"],
        )
        args = parse_args()
        assert args.exclude == ["*.log", "*.tmp"]

    def test_default_include(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst"])
        args = parse_args()
        assert args.include == []

    def test_include(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["dirsync", "src", "dst", "--include", "*.txt"]
        )
        args = parse_args()
        assert args.include == ["*.txt"]

    def test_multiple_include(self, monkeypatch):
        monkeypatch.setattr(
            sys,
            "argv",
            ["dirsync", "src", "dst", "--include", "*.txt", "--include", "*.csv"],
        )
        args = parse_args()
        assert args.include == ["*.txt", "*.csv"]

    def test_preserve_links(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--preserve-links"])
        args = parse_args()
        assert args.preserve_links is True

    def test_default_preserve_links(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst"])
        args = parse_args()
        assert args.preserve_links is False

    def test_preserve_special(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["dirsync", "src", "dst", "--preserve-special"]
        )
        args = parse_args()
        assert args.preserve_special is True

    def test_force(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--force"])
        args = parse_args()
        assert args.force is True

    def test_verify_only(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--verify-only"])
        args = parse_args()
        assert args.verify_only is True

    def test_no_verify(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--no-verify"])
        args = parse_args()
        assert args.no_verify is True

    def test_verbose(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "-v"])
        args = parse_args()
        assert args.verbose is True

    def test_verbose_long(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--verbose"])
        args = parse_args()
        assert args.verbose is True

    def test_quiet(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "-q"])
        args = parse_args()
        assert args.quiet is True

    def test_quiet_long(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["dirsync", "src", "dst", "--quiet"])
        args = parse_args()
        assert args.quiet is True
