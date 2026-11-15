import time
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.workers.email_worker as ew


class FakePipe:
    def __init__(self, redis):
        self.redis = redis
        self.calls = []
        self._results = [1, 1, 1]

    def xadd(self, *a, **k):
        self.calls.append(("xadd", a, k))

    def xack(self, *a, **k):
        self.calls.append(("xack", a, k))

    def delete(self, *a, **k):
        self.calls.append(("delete", a, k))

    async def execute(self):
        self.redis.executed = True
        return self._results


class FakeRedis:
    def __init__(self):
        self.executed = False
        self.pipe = None

    def pipeline(self, transaction=True):
        self.tx = transaction
        self.pipe = FakePipe(self)
        return self.pipe


@pytest.mark.asyncio
async def test_move_to_dead_letter_uses_transaction(monkeypatch):
    fr = FakeRedis()
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    # refresh module const not needed due to _dlq_retention_seconds reads env
    await ew.move_to_dead_letter("e1", {"spool_path": "/tmp/x.eml", "recipient": "a@b"}, 3, "boom")
    assert fr.executed is True
    assert fr.tx is True
    ops = [c[0] for c in fr.pipe.calls]
    assert ops == ["xadd", "xack", "delete"]
    # xadd includes retention timestamps
    xadd_kwargs = fr.pipe.calls[0][2] if fr.pipe.calls[0][2] else {}
    # xadd called as positional? In code pipe.xadd(DLQ, dlq_data) -> a=(DLQ, dict)
    data = fr.pipe.calls[0][1][1]
    assert "failed_at" in data
    assert "spool_expires_at" in data
    assert float(data["spool_expires_at"]) - float(data["failed_at"]) == 3600
    assert data["original_event_id"] == "e1"


@pytest.mark.asyncio
async def test_transaction_failure_propagates(monkeypatch):
    class FailingPipe(FakePipe):
        async def execute(self):
            raise RuntimeError("tx failed")

    fr = FakeRedis()
    fr.pipeline = lambda transaction=True: FailingPipe(fr)
    monkeypatch.setattr(ew, "redis_client", fr)
    with pytest.raises(RuntimeError):
        await ew.move_to_dead_letter("e2", {"spool_path": "/tmp/y.eml"}, 1, "err")