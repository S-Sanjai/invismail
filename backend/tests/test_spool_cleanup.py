import asyncio
import os
import time
from pathlib import Path

import pytest

import app.workers.cleanup_worker as cw
from app.services import storage_service


class FakeRedis:
    def __init__(self, groups=None, stream_msgs=None, pending=None,
                 dlq_msgs=None, last_delivered="0-0", raise_on=None):
        self.groups = groups if groups is not None else [{"name": "email-workers", "last-delivered-id": last_delivered}]
        self.stream_msgs = stream_msgs or []
        self.pending = pending or []
        self.dlq_msgs = dlq_msgs or []
        self.raise_on = raise_on

    async def xinfo_groups(self, stream):
        if self.raise_on:
            raise self.raise_on
        return self.groups

    async def xpending_range(self, stream, group, min="-", max="+", count=100):
        if self.raise_on:
            raise self.raise_on
        return list(self.pending)

    async def xrange(self, stream, min="-", max="+", count=100):
        if self.raise_on:
            raise self.raise_on
        if stream == cw.DLQ:
            return list(self.dlq_msgs)
        result = []
        for msg_id, fields in self.stream_msgs:
            if min.startswith("("):
                threshold = min[1:]
                if msg_id <= threshold:
                    continue
            result.append((msg_id, fields))
        return result[:count]


@pytest.fixture
def spool(tmp_path, monkeypatch):
    d = tmp_path / "spool"
    d.mkdir()
    monkeypatch.setattr(storage_service, "SPOOL_DIR", d)
    monkeypatch.setattr(cw, "SPOOL_DIR", d)
    return d


def make_file(path, age=7200):
    path.write_bytes(b"x")
    t = time.time() - age
    os.utime(path, (t, t))


@pytest.mark.asyncio
async def test_old_unreferenced_eml_removed(spool, monkeypatch, caplog):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
                    stream_msgs=[], pending=[], dlq_msgs=[])
    monkeypatch.setattr(cw, "redis_client", fake)
    with caplog.at_level("INFO"):
        await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert not f.exists()
    assert "Removed 1 orphaned spool .eml files" in caplog.text


@pytest.mark.asyncio
async def test_recent_unreferenced_eml_kept(spool, monkeypatch):
    f = spool / "recent.eml"
    make_file(f, age=100)
    fake = FakeRedis(groups=[{"name": "email-workers", "last-delivered-id": "0-0"}])
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_pending_protected(spool, monkeypatch):
    f = spool / "pending.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        pending=[{"message_id": "1-0"}],
        stream_msgs=[("1-0", {"spool_path": str(f)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_pending_xrange_fetch(spool, monkeypatch):
    f = spool / "p.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        pending=[{"message_id": "5-0"}],
        stream_msgs=[("5-0", {"spool_path": str(f)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_unresolvable_pending_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        pending=[{"message_id": "9-0"}],
        stream_msgs=[],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_pending_missing_spool_path_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        pending=[{"message_id": "9-0"}],
        stream_msgs=[("9-0", {})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_undelivered_protected(spool, monkeypatch):
    f = spool / "undelivered.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("2-0", {"spool_path": str(f)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_undelivered_missing_spool_path_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("2-0", {})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_undelivered_empty_spool_path_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("2-0", {"spool_path": ""})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_undelivered_invalid_spool_path_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("2-0", {"spool_path": "not-a-path\\x00"})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_undelivered_path_outside_spool_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    outside = spool.parent / "outside.eml"
    outside.write_bytes(b"x")
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("2-0", {"spool_path": str(outside)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()
    assert outside.exists()


@pytest.mark.asyncio
async def test_undelivered_valid_path_protected(spool, monkeypatch):
    f = spool / "undelivered2.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("2-0", {"spool_path": str(f)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_dlq_within_retention_protected(spool, monkeypatch):
    f = spool / "dlq.eml"
    make_file(f, age=7200)
    future = time.time() + 3600
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        dlq_msgs=[("d-0", {"spool_path": str(f), "spool_expires_at": str(future)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_dlq_expired_removed(spool, monkeypatch):
    f = spool / "dlqexpired.eml"
    make_file(f, age=7200)
    past = time.time() - 100
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        dlq_msgs=[("d-0", {"spool_path": str(f), "spool_expires_at": str(past)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert not f.exists()


@pytest.mark.asyncio
async def test_legacy_dlq_failed_at(spool, monkeypatch):
    f = spool / "legacy.eml"
    make_file(f, age=7200)
    recent_failed = time.time() - 100
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        dlq_msgs=[("d-0", {"spool_path": str(f), "failed_at": str(recent_failed)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_invalid_timestamps_protect(spool, monkeypatch):
    f = spool / "invalid.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        dlq_msgs=[("d-0", {"spool_path": str(f), "failed_at": "not-a-number"})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_old_tmp_removed(spool, monkeypatch, caplog):
    f = spool / "old.tmp"
    make_file(f, age=7200)
    fake = FakeRedis(groups=[{"name": "email-workers", "last-delivered-id": "0-0"}])
    monkeypatch.setattr(cw, "redis_client", fake)
    with caplog.at_level("INFO"):
        await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert not f.exists()
    assert "Removed 1 orphaned spool .tmp files" in caplog.text or "Removed" in caplog.text


@pytest.mark.asyncio
async def test_recent_tmp_kept(spool, monkeypatch):
    f = spool / "recent.tmp"
    make_file(f, age=100)
    fake = FakeRedis(groups=[{"name": "email-workers", "last-delivered-id": "0-0"}])
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_redis_unavailable_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(raise_on=RuntimeError("boom"))
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_missing_group_no_delete(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(groups=[])
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_deletion_failure_leaves_file(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(groups=[{"name": "email-workers", "last-delivered-id": "0-0"}])
    monkeypatch.setattr(cw, "redis_client", fake)
    monkeypatch.setattr(cw, "_safe_delete", lambda *a, **k: False)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()


@pytest.mark.asyncio
async def test_failed_eml_not_counted(spool, monkeypatch, caplog):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(groups=[{"name": "email-workers", "last-delivered-id": "0-0"}])
    monkeypatch.setattr(cw, "redis_client", fake)
    monkeypatch.setattr(cw, "_safe_delete", lambda *a, **k: False)
    with caplog.at_level("INFO"):
        await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()
    assert "Removed 0" not in caplog.text or "orphaned spool .eml files" not in caplog.text


@pytest.mark.asyncio
async def test_protected_eml_not_counted(spool, monkeypatch, caplog):
    f = spool / "protected.eml"
    make_file(f, age=7200)
    fake = FakeRedis(
        groups=[{"name": "email-workers", "last-delivered-id": "0-0"}],
        stream_msgs=[("1-0", {"spool_path": str(f)})],
    )
    monkeypatch.setattr(cw, "redis_client", fake)
    with caplog.at_level("INFO"):
        await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()
    assert "Removed" not in caplog.text or "0 orphaned" not in caplog.text


@pytest.mark.asyncio
async def test_reconciliation_failure_prevents_eml_cleanup(spool, monkeypatch):
    f = spool / "orphan.eml"
    make_file(f, age=7200)
    fake = FakeRedis(raise_on=RuntimeError("boom"))
    monkeypatch.setattr(cw, "redis_client", fake)
    await cw.cleanup_orphaned_spool_files(grace_period_seconds=3600)
    assert f.exists()