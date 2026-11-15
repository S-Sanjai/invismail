import time
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.workers.email_worker as ew


class FakeRedis:
    def __init__(self, eval_result=1):
        self.eval_result = eval_result
        self.eval_called = False
        self.eval_args = None
        self.pipe = None
        self.executed = False

    async def eval(self, script, numkeys, *args):
        self.eval_called = True
        self.eval_args = (script, numkeys, args)
        if isinstance(self.eval_result, Exception):
            raise self.eval_result
        return self.eval_result

    def pipeline(self, transaction=True):
        self.tx = transaction
        self.pipe = FakePipe(self)
        return self.pipe


class FakePipe:
    def __init__(self, redis):
        self.redis = redis
        self.calls = []

    def xadd(self, *a, **k):
        self.calls.append(("xadd", a, k))

    def xack(self, *a, **k):
        self.calls.append(("xack", a, k))

    def delete(self, *a, **k):
        self.calls.append(("delete", a, k))

    async def execute(self):
        self.redis.executed = True
        return [1, 1, 1]


@pytest.mark.asyncio
async def test_move_to_dead_letter_uses_eval(monkeypatch):
    fr = FakeRedis(eval_result=1)
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    await ew.move_to_dead_letter("e1", {"spool_path": "/tmp/x.eml", "recipient": "a@b"}, 3, "boom")
    assert fr.eval_called is True
    # eval receives stream/group/dlq keys
    script, numkeys, args = fr.eval_args
    assert numkeys == 6
    assert "XPENDING" in script
    assert "XADD" in script


@pytest.mark.asyncio
async def test_already_acknowledged_does_not_create_dlq(monkeypatch):
    fr = FakeRedis(eval_result=0)
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    await ew.move_to_dead_letter("e2", {"spool_path": "/tmp/y.eml"}, 1, "err")
    assert fr.eval_called is True
    # No pipeline fallback executed
    assert fr.executed is False


@pytest.mark.asyncio
async def test_script_failure_propagates(monkeypatch):
    fr = FakeRedis(eval_result=RuntimeError("script failed"))
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    with pytest.raises(RuntimeError):
        await ew.move_to_dead_letter("e3", {"spool_path": "/tmp/z.eml"}, 1, "err")


@pytest.mark.asyncio
async def test_pipeline_fallback_when_no_eval(monkeypatch):
    class NoEvalRedis:
        def __init__(self):
            self.pipeline_called = False
            self.pipe = FakePipe(self)

        def pipeline(self, transaction=True):
            self.pipeline_called = True
            return self.pipe

    fr = NoEvalRedis()
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    await ew.move_to_dead_letter("e4", {"spool_path": "/tmp/w.eml"}, 2, "err")
    assert fr.pipeline_called is True