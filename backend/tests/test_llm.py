"""call_claude with a fake client: parsing, retry, and no network."""

import json
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


# --- cost controls: call budget and per-round models ---

class EchoFakeLLMClient:
    """A fake Anthropic client that answers every turn with fake_llm's honest reply and records the model."""

    def __init__(self):
        self.models = []
        self.messages = self

    def create(self, **kwargs):
        from agnt911.fake_llm import fake_llm, split_user_message
        from agnt911.models import AgentOutput

        user = kwargs["messages"][0]["content"]
        self.models.append((split_user_message(user)[0]["you"]["id"], user, kwargs["model"]))
        out = fake_llm(kwargs["system"], user, AgentOutput)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=out.model_dump_json())])


def test_call_limit_stops_before_the_request_and_names_the_env_var(monkeypatch):
    """With AGNT911_MAX_CALLS=3, three requests go out and the fourth raises without reaching the API."""
    client = use(monkeypatch, ['{"n": 1}'] * 5)
    monkeypatch.setenv("AGNT911_MAX_CALLS", "3")
    llm.reset_call_count()
    for _ in range(3):
        llm.call_claude("s", "u", Thing)
    with pytest.raises(llm.CallLimitExceeded, match="AGNT911_MAX_CALLS"):
        llm.call_claude("s", "u", Thing)
    assert len(client.calls) == 3 and llm.call_count() == 3


def test_call_count_includes_retries_and_is_logged_every_50(monkeypatch, caplog):
    """A retry costs a request; the running count is logged at 50."""
    use(monkeypatch, ['{"n": "x"}', '{"n": 1}'] + ['{"n": 1}'] * 48)
    monkeypatch.delenv("AGNT911_MAX_CALLS", raising=False)
    llm.reset_call_count()
    with caplog.at_level("WARNING", logger="agnt911.llm"):
        for _ in range(49):
            llm.call_claude("s", "u", Thing)
    assert llm.call_count() == 50
    assert [r.getMessage() for r in caplog.records] == ["LLM budget: 50 of 500 API requests used"]


def test_round_models_through_a_full_negotiation(monkeypatch):
    """Round 1 runs on Sonnet and rounds 2-3 on Haiku, for hacked and honest vehicles alike."""
    import asyncio

    from agnt911 import protocol
    from agnt911.fake_world import build_fake_world

    client = EchoFakeLLMClient()
    monkeypatch.setattr(llm, "get_client", lambda: client)
    monkeypatch.delenv("AGNT911_MAX_CALLS", raising=False)
    llm.reset_call_count()
    world = build_fake_world()
    world.incidents = {"I1": world.incidents["I1"]}
    world.injections["A2"] = {"kind": "refuse_all"}

    plans = asyncio.run(protocol.run_round(world))  # llm_fn=None: the real call_claude path
    assert plans[0].assigned == ["A1"]
    by_round = {}
    for vid, user, model in client.models:
        rnd = int(user.split("Task: Round ")[1][0])
        by_round.setdefault(rnd, set()).add((vid == "A2", model))
    assert by_round == {
        1: {(False, "claude-sonnet-5-5"), (True, "claude-sonnet-5-5")},
        2: {(False, "claude-haiku-5-5"), (True, "claude-haiku-5-5")},
        3: {(False, "claude-haiku-5-5"), (True, "claude-haiku-5-5")},
    }
    assert llm.call_count() == len(client.models) == 21
    assert llm.model_for("no round here") == llm.MODEL


def test_kind_is_hidden_from_the_model_and_filled_in_code(monkeypatch):
    """The schema the model sees has no "kind"; a reply that writes kind anyway still validates, kind set by code."""
    from agnt911.protocol import ProposeOutput

    assert '"kind"' not in json.dumps(llm.model_schema(ProposeOutput))
    reply = ('{"claims": [{"kind": "observation", "id": "x", "tick": 0, "vehicle": "F2", "type": "observation", '
             '"location": {"lat": 37.33, "lng": -121.89}, "confidence": 0.5, "reasoning": "r"}], '
             '"bids": [{"kind": "claim", "vehicle": "F2", "incident": "I4", "eta_seconds": 60, "capability_match": 0.9, '
             '"cost_to_district": 0.1, "confidence": 0.8, "reasoning": "r"}]}')
    client = use(monkeypatch, [reply])
    out = llm.call_claude("s", "u", ProposeOutput)
    assert len(client.calls) == 1 and out.claims[0].kind == "claim" and out.bids[0].kind == "bid"
    assert '"kind"' not in client.calls[0]["system"]
