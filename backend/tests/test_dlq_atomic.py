import os
from unittest.mock import MagicMock

import pytest

import app.workers.email_worker as ew


class FakeRedis:
    def __init__(self, eval_result=1, has_eval=True):
        self.eval_result = eval_result
        self.has_eval = has_eval
        self.eval_called = False
        self.eval_args = None
        self.pipeline_called = False

    async def eval(self, script, numkeys, *args):
        self.eval_called = True
        self.eval_args = (script, numkeys, args)
        if isinstance(self.eval_result, Exception):
            raise self.eval_result
        return self.eval_result

    def pipeline(self, transaction=True):
        self.pipeline_called = True
        raise AssertionError("non-atomic fallback was attempted")


@pytest.mark.asyncio
async def test_move_to_dead_letter_uses_eval(monkeypatch):
    fr = FakeRedis(eval_result=1)
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    await ew.move_to_dead_letter("e1", {"spool_path": "/tmp/x.eml", "recipient": "a@b"}, 3, "boom")
    assert fr.eval_called is True
    script, numkeys, args = fr.eval_args
    assert numkeys == 6
    assert "XPENDING" in script
    assert "XADD" in script
    assert "XACK" in script
    assert fr.pipeline_called is False


@pytest.mark.asyncio
async def test_already_acknowledged_does_not_create_dlq(monkeypatch):
    fr = FakeRedis(eval_result=0)
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    await ew.move_to_dead_letter("e2", {"spool_path": "/tmp/y.eml"}, 1, "err")
    assert fr.eval_called is True
    assert fr.pipeline_called is False


@pytest.mark.asyncio
async def test_script_failure_propagates(monkeypatch):
    fr = FakeRedis(eval_result=RuntimeError("script failed"))
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    with pytest.raises(RuntimeError):
        await ew.move_to_dead_letter("e3", {"spool_path": "/tmp/z.eml"}, 1, "err")
    assert fr.pipeline_called is False


@pytest.mark.asyncio
async def test_eval_unavailable_fails_safely(monkeypatch):
    fr = MagicMock(spec=[])  # no eval attribute
    monkeypatch.setattr(ew, "redis_client", fr)
    monkeypatch.setenv("SPOOL_DLQ_RETENTION_SECONDS", "3600")
    with pytest.raises(RuntimeError):
        await ew.move_to_dead_letter("e4", {"spool_path": "/tmp/w.eml"}, 2, "err")