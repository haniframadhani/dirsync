import hashlib
import os

from dirsync import file_hash, stream_hash_and_copy, verify_destination_hash, CHUNK_SIZE


class TestFileHash:
    def test_matches_hashlib(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert file_hash(str(f)) == expected

    def test_empty_file(self, tmp_path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        expected = hashlib.sha256(b"").hexdigest()
        assert file_hash(str(f)) == expected

    def test_with_offset(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"world").hexdigest()
        assert file_hash(str(f), offset=6) == expected

    def test_offset_at_end(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello")
        expected = hashlib.sha256(b"").hexdigest()
        assert file_hash(str(f), offset=5) == expected

    def test_offset_zero(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"hello world")
        expected = hashlib.sha256(b"hello world").hexdigest()
        assert file_hash(str(f), offset=0) == expected


class TestStreamHashAndCopy:
    def test_copy_matches_source(self, tmp_path):
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        content = os.urandom(1024)
        src.write_bytes(content)
        src_hash, bytes_copied = stream_hash_and_copy(str(src), str(dst))
        assert dst.read_bytes() == content
        assert src_hash == hashlib.sha256(content).hexdigest()
        assert bytes_copied == len(content)

    def test_empty_file(self, tmp_path):
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        src.write_bytes(b"")
        src_hash, bytes_copied = stream_hash_and_copy(str(src), str(dst))
        assert dst.read_bytes() == b""
        assert src_hash == hashlib.sha256(b"").hexdigest()
        assert bytes_copied == 0

    def test_resume_from_offset(self, tmp_path):
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        content = b"hello world"
        src.write_bytes(content)
        dst.write_bytes(b"hello ")  # partial copy
        src_hash, bytes_copied = stream_hash_and_copy(
            str(src), str(dst), src_offset=6, dst_offset=6
        )
        assert dst.read_bytes() == content
        assert src_hash == hashlib.sha256(content[6:]).hexdigest()
        assert bytes_copied == 5

    def test_creates_destination_dirs(self, tmp_path):
        src = tmp_path / "src.bin"
        dst_dir = tmp_path / "sub" / "dir"
        dst_dir.mkdir(parents=True)
        dst = dst_dir / "dst.bin"
        src.write_bytes(b"data")
        src_hash, _ = stream_hash_and_copy(str(src), str(dst))
        assert dst.exists()
        assert dst.read_bytes() == b"data"


class TestVerifyDestinationHash:
    def test_matches_source(self, tmp_path):
        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        content = b"test content for verification"
        src.write_bytes(content)
        dst.write_bytes(content)
        assert verify_destination_hash(str(dst)) == hashlib.sha256(content).hexdigest()

    def test_mismatch(self, tmp_path):
        dst = tmp_path / "dst.bin"
        dst.write_bytes(b"different content")
        expected = hashlib.sha256(b"different content").hexdigest()
        assert verify_destination_hash(str(dst)) == expected
