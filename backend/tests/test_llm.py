"""call_claude with a fake client: parsing, retry, and no network."""

from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from agnt911 import llm


class Thing(BaseModel):
    n: int


class FakeClient:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.texts.pop(0))])


def use(monkeypatch, texts):
    client = FakeClient(texts)
    monkeypatch.setattr(llm, "get_client", lambda: client)
    return client


def test_extracts_json_from_fence(monkeypatch):
    """JSON inside a code fence with prose around it is accepted."""
    client = use(monkeypatch, ['Sure:\n```json\n{"n": 3}\n```'])
    assert llm.call_claude("sys", "user", Thing) == Thing(n=3)
    assert client.calls[0]["model"] == "claude-sonnet-5-5"
    assert "properties" in client.calls[0]["system"]


def test_retries_once_with_error(monkeypatch):
    """A validation failure triggers one retry that includes the error."""
    client = use(monkeypatch, ['{"n": "x"}', '{"n": 5}'])
    assert llm.call_claude("s", "u", Thing).n == 5
    assert len(client.calls) == 2
    assert "failed validation" in client.calls[1]["messages"][-1]["content"]


def test_second_failure_raises(monkeypatch):
    """Two bad replies raise ValidationError."""
    client = use(monkeypatch, ["nope", "still nope"])
    with pytest.raises(ValidationError):
        llm.call_claude("s", "u", Thing)
    assert len(client.calls) == 2
