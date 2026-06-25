"""Tests for storage backend."""

import shutil
from pathlib import Path

import pytest

from app.services.storage import LocalStorage


@pytest.fixture()
def storage_dir(tmp_path):
    d = tmp_path / "uploads"
    d.mkdir()
    yield str(d)
    if d.exists():
        shutil.rmtree(d)


@pytest.fixture()
def local_storage(storage_dir):
    return LocalStorage(base_dir=storage_dir, url_prefix="/static/uploads")


class TestLocalStorage:
    def test_save_creates_file(self, local_storage, storage_dir):
        content = b"hello world"
        key = local_storage.save(content, "test.txt", "text/plain")
        assert key
        assert (Path(storage_dir) / key).exists()

    def test_save_content_matches(self, local_storage, storage_dir):
        content = b"binary data \x00\x01\x02"
        key = local_storage.save(content, "data.bin", "application/octet-stream")
        saved = (Path(storage_dir) / key).read_bytes()
        assert saved == content

    def test_get_url(self, local_storage):
        url = local_storage.get_url("abc123_test.txt")
        assert url == "/static/uploads/abc123_test.txt"

    def test_exists_true(self, local_storage, storage_dir):
        content = b"exists"
        key = local_storage.save(content, "exists.txt", "text/plain")
        assert local_storage.exists(key) is True

    def test_exists_false(self, local_storage):
        assert local_storage.exists("nonexistent.txt") is False

    def test_delete_removes_file(self, local_storage, storage_dir):
        content = b"delete me"
        key = local_storage.save(content, "deleteme.txt", "text/plain")
        assert local_storage.exists(key)
        local_storage.delete(key)
        assert not local_storage.exists(key)

    def test_delete_nonexistent_noop(self, local_storage):
        # Should not raise
        local_storage.delete("nonexistent.txt")

    def test_save_long_filename_truncated(self, local_storage, storage_dir):
        long_name = "a" * 100 + ".txt"
        key = local_storage.save(b"data", long_name, "text/plain")
        assert len(key) < len(long_name)
        assert key.endswith(".txt")

    def test_directory_traversal_prevented(self, local_storage):
        result = local_storage._resolve_path("../../etc/passwd")
        assert result is None
