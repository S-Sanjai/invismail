import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.email_processor as ep
import app.services.storage_service as ss


class FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, existing_email=None):
        self.existing_email = existing_email

    async def execute(self, stmt):
        return FakeResult(self.existing_email)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def rollback(self):
        pass


@pytest.mark.asyncio
async def test_duplicate_does_not_read_spool(tmp_path, monkeypatch):
    spool = tmp_path / "dup.eml"
    spool.write_bytes(b"raw")

    read_called = {"n": 0}
    original_read_bytes = Path.read_bytes

    def tracking_read(self):
        if self == spool:
            read_called["n"] += 1
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", tracking_read)

    existing = MagicMock()
    fake_db = FakeDB(existing_email=existing)
    monkeypatch.setattr(ep, "SessionLocal", lambda: fake_db)

    monkeypatch.setattr(ss, "SPOOL_DIR", tmp_path)

    data = {"recipient": "a@b.com", "spool_path": str(spool)}
    await ep.process_email_event("evt1", data)

    assert read_called["n"] == 0
    assert not spool.exists()


@pytest.mark.asyncio
async def test_duplicate_missing_spool_ok(tmp_path, monkeypatch):
    spool = tmp_path / "missing.eml"

    existing = MagicMock()
    fake_db = FakeDB(existing_email=existing)
    monkeypatch.setattr(ep, "SessionLocal", lambda: fake_db)

    monkeypatch.setattr(ss, "SPOOL_DIR", tmp_path)

    data = {"recipient": "a@b.com", "spool_path": str(spool)}
    await ep.process_email_event("evt2", data)  # should not raise


@pytest.mark.asyncio
async def test_integrity_error_cleans_only_if_exists(tmp_path, monkeypatch):
    spool = tmp_path / "ie.eml"
    spool.write_bytes(b"raw")

    from sqlalchemy.exc import IntegrityError as IE

    class FakeDB2:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(None)  # outer duplicate check
            if self.calls == 2:
                class R:
                    def scalar_one_or_none(self):
                        return MagicMock()  # inbox
                return R()
            if self.calls == 3:
                return FakeResult(None)  # inner duplicate recheck
            return FakeResult(MagicMock())  # verify after IntegrityError

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def rollback(self):
            pass

        def add(self, x):
            pass

        async def flush(self):
            raise IE("dup", None, None)

        async def commit(self):
            pass

    fake_db = FakeDB2()
    monkeypatch.setattr(ep, "SessionLocal", lambda: fake_db)

    monkeypatch.setattr(ss, "SPOOL_DIR", tmp_path)

    data = {"recipient": "a@b.com", "spool_path": str(spool)}
    await ep.process_email_event("evt3", data)
    assert not spool.exists()


@pytest.mark.asyncio
async def test_integrity_error_propagates_if_no_duplicate(tmp_path, monkeypatch):
    spool = tmp_path / "ie2.eml"
    spool.write_bytes(b"raw")

    from sqlalchemy.exc import IntegrityError as IE

    class FakeDB3:
        def __init__(self):
            self.calls = 0

        async def execute(self, stmt):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(None)
            if self.calls == 2:
                class R:
                    def scalar_one_or_none(self):
                        return MagicMock()
                return R()
            if self.calls == 3:
                return FakeResult(None)
            return FakeResult(None)  # verify -> no existing

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def rollback(self):
            pass

        def add(self, x):
            pass

        async def flush(self):
            raise IE("dup", None, None)

        async def commit(self):
            pass

    fake_db = FakeDB3()
    monkeypatch.setattr(ep, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(ss, "SPOOL_DIR", tmp_path)

    data = {"recipient": "a@b.com", "spool_path": str(spool)}
    with pytest.raises(IE):
        await ep.process_email_event("evt4", data)