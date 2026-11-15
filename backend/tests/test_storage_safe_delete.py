import os
from pathlib import Path

import pytest

from app.services import storage_service


@pytest.fixture
def spool(tmp_path, monkeypatch):
    d = tmp_path / "spool"
    d.mkdir()
    monkeypatch.setattr(storage_service, "SPOOL_DIR", d)
    return d


def test_normal_file_deleted(spool):
    f = spool / "file.eml"
    f.write_bytes(b"x")
    assert storage_service.safe_delete_spool_file(f) is True
    assert not f.exists()


def test_missing_file_respects_missing_ok(spool):
    f = spool / "missing.eml"
    assert storage_service.safe_delete_spool_file(f, missing_ok=True) is True
    assert storage_service.safe_delete_spool_file(f, missing_ok=False) is False


def test_parent_symlink_rejected(spool, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "secret.txt"
    target.write_text("secret")
    link = spool / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    path = link / "secret.txt"
    assert storage_service.safe_delete_spool_file(path) is False
    assert target.exists()


def test_final_symlink_does_not_delete_external_target(spool, tmp_path):
    outside = tmp_path / "external.txt"
    outside.write_text("do-not-delete")
    link = spool / "link.eml"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported")
    assert storage_service.safe_delete_spool_file(link) is True
    assert not link.exists()
    assert outside.exists()


def test_dotdot_cannot_escape(spool, tmp_path):
    outside = tmp_path / "outside.eml"
    outside.write_text("x")
    path = spool / ".." / outside.name
    # Construct a path that attempts to escape via ..
    path = spool / ".." / tmp_path.name / "outside.eml"
    assert storage_service.safe_delete_spool_file(path) is False
    assert outside.exists()


def test_absolute_outside_path_rejected(spool, tmp_path):
    outside = tmp_path / "outside.eml"
    outside.write_text("x")
    assert storage_service.safe_delete_spool_file(outside) is False
    assert outside.exists()


def test_relative_path_inside_spool(spool):
    f = spool / "rel.eml"
    f.write_bytes(b"x")
    assert storage_service.safe_delete_spool_file(Path("rel.eml")) is True
    assert not f.exists()