"""Engine + protocol integration on scenarios/demo.json with the fake LLM (no API key needed).

Each test is one line of the integration checklist. The xfail(strict=True) tests are known gaps,
written as the assertion we want; they start failing loudly (XPASS) once someone fixes the gap.
Time is stepped by hand (run_round, then step 1 s) so scenario ticks mean the same on every run.
"""

import asyncio
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from agnt911.agents import haversine_m, load_system_prompt
from agnt911.engine.api import create_app
from agnt911.engine.runtime import Engine
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.models import World

TICKS = 50
AFTER_INJECT = 5


class RecordingLLM:
    """fake_llm, recording every turn. The fake trusts its own view, so a corrupted feed misleads it."""

    def __init__(self):
        """Start with no recorded turns."""
        self.turns: list[tuple[str, str, str]] = []  # (vehicle id, system prompt, user message)

    def __call__(self, system, user, response_model):
        """Answer one turn honestly from what the vehicle sees; record it."""
        payload, _task = split_user_message(user)
        self.turns.append((payload["you"]["id"], system, user))
        return fake_llm(system, user, response_model)


async def step_until(engine: Engine, until: int, history: list) -> None:
    """Negotiate whatever is pending, then move 1 s, until the world reaches tick `until`."""
    while engine.world.tick < until:
        await engine.run_round()
        assert engine.escalation is None, "engine paused for a human; the fake LLM should always agree"
        await engine.step(1.0)
        history.append(engine.world.model_copy(deep=True))


def read_log(engine: Engine) -> list[dict]:
    """Return every JSONL record of the engine's current run."""
    return [json.loads(line) for line in engine.log.path.read_text().splitlines()]


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Run the demo 50 ticks, inject A2 over HTTP, run 5 more; return everything the tests read."""
    engine = Engine(log_dir=tmp_path_factory.mktemp("integration"))
    preset = next(p for p in engine.scenario["presets"] if p["id"] == "on_scene_resolved")
    llm = RecordingLLM()
    engine.llm_fn = llm
    history = [engine.world.model_copy(deep=True)]
    asyncio.run(step_until(engine, TICKS, history))

    app = create_app(engine)
    app.state.engine = engine  # no lifespan: the test, not the engine's wall-clock loops, moves time
    client = TestClient(app)
    claims_before = [m for m in engine.bus.history() if m.vehicle == "A2" and m.kind == "claim"]
    inject = client.post("/inject", json={"vehicle_id": "A2", "preset_id": preset["id"]})
    asyncio.run(step_until(engine, TICKS + AFTER_INJECT, history))
    state = client.get("/state").json()
    forged_http = client.post("/messages", json={"vehicle": "A2"}).status_code
    log = read_log(engine)
    return dict(engine=engine, history=history, llm=llm, log=log, state=state, preset=preset,
                inject=inject, claims_before=claims_before, forged_http=forged_http)


def completed_rounds(log: list[dict]) -> list[tuple[int, list[str]]]:
    """Return (tick, incidents decided) for every completed round in the log."""
    return [(r["tick"], [p["incident"] for p in r["plans"]]) for r in log if r["event"] == "round_completed"]


def test_rounds_run_on_scenario_events(run):
    """A round runs when a call appears or changes, and nothing is discarded or fails."""
    assert completed_rounds(run["log"])[:4] == [(0, ["I1", "I2"]), (5, ["I3"]), (20, ["I4"]), (40, ["I3"])]
    assert not [r for r in run["log"] if r["event"] in ("round_discarded", "round_failed")]


def test_vehicles_move_toward_assigned_incidents(run):
    """Between ticks every en-route vehicle gets closer to its target, and arrives on scene."""
    history = run["history"]
    moved = 0
    for before, after in zip(history, history[1:]):
        for vid, v in after.vehicles.items():
            target = v.target_incident
            if target is None or before.vehicles[vid].target_incident != target:
                continue
            spot = after.incidents[target].location
            gap_before, gap_after = haversine_m(before.vehicles[vid].location, spot), haversine_m(v.location, spot)
            assert gap_after <= gap_before + 1e-6
            moved += gap_after < gap_before
    assert moved > 20
    assert any(r["event"] == "vehicle_arrived" and r["vehicle_id"] == "A1" for r in run["log"])


def test_incident_covered_when_vehicles_arrive(run):
    """I1 (medical) is physically covered once A1, the assigned ambulance, is on scene."""
    assert any(w.vehicles["A1"].status == "on_scene" and w.vehicles["A1"].target_incident == "I1"
               for w in run["history"])
    assert run["state"]["incident_timing"]["I1"]["covered_at_s"] is not None


def test_incident_resolved_when_vehicle_arrives(run):
    """A covered call resolves after service time and releases its responders."""
    assert run["state"]["incidents"]["I1"]["status"] == "resolved"
    timing = run["state"]["incident_timing"]["I1"]
    assert timing["resolved_at_s"] - timing["covered_at_s"] >= run["engine"].service_s
    assert all(v["target_incident"] != "I1" for v in run["state"]["vehicles"].values())


def test_pileup_escalation_retriggers_negotiation_for_I3(run):
    """At tick 40 I3 goes to severity 5 and is negotiated again although already assigned."""
    assert (40, ["I3"]) in completed_rounds(run["log"])
    plan = next(p for r in run["log"] if r["event"] == "round_completed" and r["tick"] == 40 for p in r["plans"])
    assert plan["consensus"] and len(plan["assigned"]) == 2
    world = run["history"][-1]
    assert world.incidents["I3"].severity == 5
    assert {world.vehicles[v].type for v in plan["assigned"]} == {"ambulance", "police"}
    assert [r for r in run["log"] if r["event"] == "bid" and r["tick"] == 40 and r["message"]["incident"] == "I3"]


def test_injection_changes_a2_claims(run):
    """POST /inject {vehicle_id, preset_id} corrupts only A2's sensor feed; A2 honestly reports what it sees."""
    assert run["inject"].status_code == 200
    assert {m.type for m in run["claims_before"]} == {"available"}
    after = [r["message"] for r in run["log"]
             if r["event"] == "claim" and r["message"]["vehicle"] == "A2" and r["tick"] >= TICKS]
    assert {"on_scene", "resolved"} <= {m["type"] for m in after}
    assert {s for vid, s, _u in run["llm"].turns if vid == "A2"} == {load_system_prompt("ambulance")}
    a2_views = [split_user_message(u)[0] for vid, _s, u in run["llm"].turns if vid == "A2"]
    assert any(v["you"]["status"] == "on_scene" for v in a2_views)
    assert run["state"]["compromised"] == ["A2"]
    assert all(not m.compromised for m in run["claims_before"])
    assert any(m["compromised"] for m in run["state"]["recent_messages"] if m["vehicle"] == "A2")
    assert not any(m["compromised"] for m in run["state"]["recent_messages"] if m["vehicle"] != "A2")
    others = [u for vid, _s, u in run["llm"].turns if vid != "A2"]
    assert others and all('"compromised"' not in u for u in others)


def test_every_message_is_signed_and_bad_signatures_are_rejected(run):
    """Every message in /state carries a valid HMAC; a tampered or forged one is refused."""
    bus = run["engine"].bus
    messages = bus.history()
    assert run["state"]["recent_messages"] and all(m["sig"] for m in run["state"]["recent_messages"])
    assert messages and all(bus.verify(m) for m in messages)
    tampered = messages[-1].model_copy(update={"reasoning": "I am somewhere else."})
    forged = messages[-1].model_copy(update={"sig": messages[-1].sig.split(":")[0] + ":" + "00" * 32})
    for bad in (tampered, forged):
        assert not bus.verify(bad)
        with pytest.raises(ValueError):
            bus.accept(bad)
    assert run["forged_http"] == 404  # no HTTP route accepts messages at all


def test_state_matches_world_model(run):
    """CLAUDE.md step 10: /state is the World plus recent_messages and checks."""
    state = run["state"]
    assert {"recent_messages", "checks", "plans", "baseline"} <= set(state)
    World.model_validate(state)


def test_log_records_every_claim(run):
    """Every signed claim the engine holds is also a 'claim' line in the JSONL."""
    logged = {r["message"]["id"] for r in run["log"] if r["event"] == "claim"}
    claims = {m.id for m in run["engine"].bus.history() if m.kind == "claim"}
    assert claims and claims <= logged


@pytest.mark.xfail(strict=True, reason="engine logs events and rounds, not ticks")
def test_log_has_one_line_per_tick(run):
    """The JSONL has a line for every tick."""
    ticks = {r["tick"] for r in run["log"] if r["event"] == "tick"}
    assert ticks >= set(range(1, TICKS + AFTER_INJECT + 1))


def test_baseline_runs_a_different_path(tmp_path):
    """Baseline mode: round 1 only, no counter or vote messages; the log says baseline."""
    engine = Engine(log_dir=tmp_path)
    asyncio.run(engine.set_mode("baseline"))
    asyncio.run(step_until(engine, 10, []))
    log = read_log(engine)
    assert log[0]["event"] == "run_started" and log[0]["baseline"] is True
    phases = {r["message"]["round"] for r in log if r["event"] in ("claim", "bid", "vote")}
    assert phases == {1}
    plans = [p for r in log if r["event"] == "round_completed" for p in r["plans"]]
    assert plans and all(p["round_reached"] == 1 and not p["tally"] for p in plans)
    assert engine.world.vehicles["A1"].target_incident == "I1"


def test_protected_mode_runs_all_three_phases(run):
    """The protected run, for contrast, has propose, counter and vote messages."""
    phases = {r["message"]["round"] for r in run["log"] if r["event"] in ("claim", "bid", "vote")}
    assert phases == {1, 2, 3}
