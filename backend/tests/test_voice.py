"""Backend routes the voice dispatcher (frontend/src/voice) uses: caller calls, follow-up updates, the ElevenLabs token."""

import io
import json

from fastapi.testclient import TestClient

from agnt911.engine import api
from agnt911.engine.api import create_app
from agnt911.engine.runtime import Engine
from agnt911.engine.world import load_scenario
from agnt911.fake_llm import fake_llm

SPOT = {"lat": 37.3355, "lng": -121.8889}


def client_for(tmp_path) -> TestClient:
    """A test client over the demo scenario with the fake LLM."""
    return TestClient(create_app(Engine(tmp_path, scenario=load_scenario(), llm_fn=fake_llm)))


def test_caller_call_is_created_then_updated_in_place(tmp_path):
    """POST /incident with source caller makes one J-call; POST /incident/{id} changes it without adding another."""
    with client_for(tmp_path) as client:
        made = client.post("/incident", json={"type": "medical", "severity": 3, "location": SPOT,
                                               "label": "Dana: man collapsed", "source": "caller"})
        assert made.status_code == 200
        iid = made.json()["incident"]
        before = set(client.get("/state").json()["incidents"])
        moved = {"lat": 37.3361, "lng": -121.8853}
        changed = client.post(f"/incident/{iid}", json={"severity": 5, "label": "Dana: not breathing", "location": moved})
        assert changed.status_code == 200 and changed.json()["incident"] == iid
        state = client.get("/state").json()
        assert set(state["incidents"]) == before
        incident = state["incidents"][iid]
        assert incident["source"] == "caller" and incident["severity"] == 5 and incident["location"] == moved
        assert state["labels"][iid] == "Dana: not breathing"


def test_incident_update_rejects_unknown_calls_and_empty_bodies(tmp_path):
    """An unknown id is 404; a body with nothing to change is 422."""
    with client_for(tmp_path) as client:
        assert client.post("/incident/J99", json={"severity": 4}).status_code == 404
        assert client.post("/incident/I1", json={}).status_code == 422


def test_voice_token_needs_both_settings(tmp_path, monkeypatch):
    """Without the key and agent id the route says what to set instead of calling ElevenLabs."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    with client_for(tmp_path) as client:
        response = client.get("/voice/token")
        assert response.status_code == 503 and "ELEVENLABS_API_KEY" in response.json()["detail"]


def test_voice_token_returns_only_the_token(tmp_path, monkeypatch):
    """The route sends the agent id and key to ElevenLabs and passes back just the conversation token."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "xi-test")
    monkeypatch.setenv("ELEVENLABS_AGENT_ID", "agent_1101m4m2jypsfw6scn9fskzn47je")
    seen = {}

    def fake_urlopen(req, timeout):
        seen["url"], seen["key"] = req.full_url, req.get_header("Xi-api-key")
        return io.StringIO(json.dumps({"token": "tok-123", "conversation_id": "c1"}))

    monkeypatch.setattr(api.urllib.request, "urlopen", fake_urlopen)
    with client_for(tmp_path) as client:
        response = client.get("/voice/token")
    assert response.status_code == 200 and response.json() == {"token": "tok-123"}
    assert seen["url"].endswith("/v1/convai/conversation/token?agent_id=agent_1101m4m2jypsfw6scn9fskzn47je")
    assert seen["key"] == "xi-test"
