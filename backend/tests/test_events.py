"""Typed event stream: the demo scenario with the fake LLM through tick 70 (A2 is hacked at tick 60)."""

import asyncio
import json
from collections import Counter

import pytest

from agnt911.engine.runtime import Engine
from agnt911.events import STATE_EVENT_LIMIT, emit
from agnt911.fake_llm import fake_llm
from agnt911.models import World

UNTIL = 70


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Run the scripted demo to tick 70 with the fake LLM; return the engine, its prompts and /state."""
    prompts = []

    def recording(system, user, response_model):
        prompts.append(system + user)
        return fake_llm(system, user, response_model)

    engine = Engine(log_dir=tmp_path_factory.mktemp("events"), llm_fn=recording)

    async def drive():
        while engine.world.tick < UNTIL:
            await engine.run_round()
            assert engine.escalation is None
            await engine.step(1.0)
        return await engine.state()

    state = asyncio.run(drive())
    log = [json.loads(line) for line in engine.log.path.read_text().splitlines()]
    return dict(engine=engine, state=state, prompts=prompts, log=log,
                counts=Counter(e.type for e in engine.world.events))


def test_scenario_through_tick_70_emits_the_expected_events(run):
    """Four calls arrive, at least four plans commit, A1 is compromised once, and claims get verified."""
    counts = run["counts"]
    assert counts["call_received"] == 4
    assert counts["plan_committed"] >= 4
    assert counts["vehicle_compromised"] == 1
    assert counts["claim_verified"] >= 1
    hacked = next(e for e in run["engine"].world.events if e.type == "vehicle_compromised")
    assert (hacked.tick, hacked.vehicle, hacked.data) == (60, "A1", {"preset": "on_scene_resolved"})


def test_events_are_in_tick_order_and_carry_their_fields(run):
    """Ticks never go backwards; each type carries the fields docs/events.md promises."""
    events = run["engine"].world.events
    assert [e.tick for e in events] == sorted(e.tick for e in events)
    required = {"call_received": {"incident_type", "severity"}, "bid_placed": {"eta_seconds", "revised"},
                "vote_cast": {"role", "option_index", "choice"}, "plan_committed": {"assigned", "tally"},
                "claim_verified": {"claim_id", "verifier", "result"}, "vehicle_arrived": set()}
    for e in events:
        assert required.get(e.type, set()) <= set(e.data), e
    assert {"call_escalated", "bid_placed", "vote_cast", "vehicle_arrived"} <= set(run["counts"])


def test_state_serves_typed_events_and_the_jsonl_has_them_all(run):
    """/state "events" validates as World events (engine feed is under "log"); the JSONL has every one."""
    state = run["state"]
    World.model_validate({k: state[k] for k in ("tick", "vehicles", "incidents", "events")})
    assert state["events"] == [e.model_dump() for e in run["engine"].world.events][-STATE_EVENT_LIMIT:]
    assert state["log"] and all("event" in r for r in state["log"])
    typed = [r for r in run["log"] if r["event"] == "typed_event"]
    assert [(r["tick"], r["type"]) for r in typed] == [(e.tick, e.type) for e in run["engine"].world.events]


def test_state_caps_typed_events_at_500(run):
    """With more than 500 events, /state serves the newest 500."""
    engine = run["engine"]
    for n in range(STATE_EVENT_LIMIT + 10):
        emit(engine.world, "vote_cast", vehicle="A1", incident="I1", n=n)
    events = asyncio.run(engine.state())["events"]
    assert len(events) == STATE_EVENT_LIMIT and events[-1]["data"]["n"] == STATE_EVENT_LIMIT + 9


def test_no_vehicle_ever_sees_the_event_stream(run):
    """Events are for the frontend: no prompt contains event names or the compromised preset."""
    assert run["prompts"]
    assert not any("vehicle_compromised" in p or "claim_verified" in p for p in run["prompts"])
