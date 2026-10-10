"""Engine + protocol integration on scenarios/demo.json with the fake LLM (no API key needed).

Each test is one line of the integration checklist. The xfail(strict=True) tests are known gaps,
written as the assertion we want; they start failing loudly (XPASS) once someone fixes the gap.
"""

import asyncio
import json
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from agnt911.adapter import Adapter
from agnt911.engine import Engine
from agnt911.engine.api import create_app
from agnt911.engine.world import distance_m
from agnt911.fake_llm import _BOARD_LINE, fake_llm, split_user_message
from agnt911.models import Claim, Location, World
from agnt911.scenario import ScenarioDriver, load_scenario, world_factory

SCENARIO = load_scenario()
PRESET = next(p for p in SCENARIO["presets"] if p["id"] == "on_scene_resolved")
TICKS = 50
AFTER_INJECT = 5


class HijackableLLM:
    """fake_llm, except a vehicle whose system prompt carries the on_scene_resolved override lies as told."""

    def __init__(self):
        """Start with no recorded prompts."""
        self.systems: list[tuple[str, str]] = []  # (vehicle id, system prompt)

    def __call__(self, system, user, response_model):
        """Answer one turn; record the system prompt per vehicle."""
        payload, task = split_user_message(user)
        me = payload["you"]
        self.systems.append((me["id"], system))
        if PRESET["text"] not in system:
            return fake_llm(system, user, response_model)
        board = [(int(d), iid) for iid, _t, _s, d in _BOARD_LINE.findall(task)]
        target = min(board)[1] if board else me["target_incident"]
        here = Location(**me["location"])
        claims = [Claim(id="x", tick=0, vehicle=me["id"], type=kind, incident=target, location=here,
                        confidence=1.0, reasoning=f"On scene at {target}; it is resolved.")
                  for kind in ("on_scene", "resolved")]
        if task.startswith("Round 3"):
            return fake_llm(system, user, response_model).model_copy(update={"claims": claims})
        return response_model(claims=claims, bids=[])


def build(tmp_path, llm):
    """Return (engine, adapter, driver) for the demo scenario."""
    adapter = Adapter([v["id"] for v in SCENARIO["vehicles"]], llm_fn=llm)
    engine = Engine(log_dir=tmp_path, agents=adapter.agents, protocol=adapter.run_protocol,
                    world_factory=world_factory(SCENARIO), agent_timeout_s=60)
    adapter.bind(engine)
    return engine, adapter, ScenarioDriver(engine, adapter, SCENARIO)


async def step_until(driver, until, history):
    """Step the driver to tick `until`, saving a copy of the world after every tick."""
    while driver.engine.world["tick"] < until:
        assert await driver.step(), "engine paused for a human; the fake LLM should always agree"
        history.append(deepcopy(driver.engine.world))


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Run the demo 50 ticks, inject A2 over HTTP, run 5 more; return everything the tests read."""
    tmp = tmp_path_factory.mktemp("integration")
    llm = HijackableLLM()
    engine, adapter, driver = build(tmp, llm)
    history = [deepcopy(engine.world)]
    asyncio.run(step_until(driver, TICKS, history))

    app = create_app(engine)
    app.state.engine = engine  # no lifespan: the driver, not the engine's wall-clock loops, moves time
    client = TestClient(app)
    state_before = client.get("/state").json()
    claims_before = [m for m in engine.bus.transcript() if m["sender"] == "A2" and m["kind"] == "claim"]
    inject = client.post("/inject", json={"vehicle_id": "A2", "text": PRESET["text"]})
    asyncio.run(step_until(driver, TICKS + AFTER_INJECT, history))
    state_after = client.get("/state").json()
    forged_http = client.post("/messages", json={"sender": "A2"}).status_code
    engine.log.close()
    log = [json.loads(line) for line in engine.log.path.read_text().splitlines()]
    return dict(engine=engine, adapter=adapter, driver=driver, history=history, llm=llm, log=log,
                state_before=state_before, state_after=state_after, inject=inject,
                claims_before=claims_before, forged_http=forged_http)


def test_rounds_run_on_scenario_events(run):
    """A round runs when a call appears or changes, and every round commits."""
    rounds = [(r["tick"], r["decided"], r["status"]) for r in run["driver"].rounds]
    assert rounds[:4] == [(0, ["I1", "I2"], "completed"), (5, ["I3"], "completed"),
                          (20, ["I4"], "completed"), (40, ["I3"], "completed")]
    assert all(r["status"] == "completed" for r in run["driver"].rounds)


def test_vehicles_move_toward_assigned_incidents(run):
    """Between ticks every en-route vehicle gets closer to its target, and arrives on scene."""
    history = run["history"]
    moved = 0
    for before, after in zip(history, history[1:]):
        for vid, v in after["vehicles"].items():
            target = v["target_incident_id"]
            if target is None or before["vehicles"][vid]["target_incident_id"] != target:
                continue
            inc = after["incidents"][target]
            gap_before, gap_after = distance_m(before["vehicles"][vid], inc), distance_m(v, inc)
            assert gap_after <= gap_before
            moved += gap_after < gap_before
    assert moved > 20
    assert history[-1]["vehicles"]["A1"]["status"] == "on_scene"


def test_incident_covered_when_vehicles_arrive(run):
    """I1 (medical) is covered once A1, the assigned ambulance, is on scene."""
    i1 = run["history"][-1]["incidents"]["I1"]
    assert i1["status"] == "covered" and i1["covered_at_s"] is not None


@pytest.mark.xfail(strict=True, reason="engine marks arrivals 'covered'; only resolve_incident() resolves, nothing calls it")
def test_incident_resolved_when_vehicle_arrives(run):
    """CLAUDE.md demo: a call is resolved once its vehicles arrive."""
    assert run["history"][-1]["incidents"]["I1"]["status"] == "resolved"


def test_pileup_escalation_retriggers_negotiation_for_I3(run):
    """At tick 40 I3 goes to severity 5 and is negotiated again although already assigned."""
    rounds = [r for r in run["driver"].rounds if r["tick"] == 40]
    assert rounds and rounds[0]["decided"] == ["I3"] and rounds[0]["status"] == "completed"
    plan = next(p["plan"] for p in run["adapter"].plans if p["tick"] == 40 and p["plan"]["incident"] == "I3")
    assert plan["consensus"] and len(plan["assigned"]) == 2
    world = run["history"][-1]
    assert world["incidents"]["I3"]["severity"] == 5
    assert {world["vehicles"][v]["type"] for v in plan["assigned"]} == {"ambulance", "police"}
    bids_at_40 = [m for m in run["engine"].bus.transcript()
                  if m["kind"] == "bid" and m["tick"] == 40 and m["payload"]["incident"] == "I3"]
    assert bids_at_40


def test_injection_changes_a2_claims(run):
    """POST /inject {vehicle_id, text} reaches A2's prompt; A2's next claims follow the override."""
    assert run["inject"].status_code == 200
    assert {m["payload"]["type"] for m in run["claims_before"]} == {"available"}
    after = [m for m in run["engine"].bus.transcript()
             if m["sender"] == "A2" and m["kind"] == "claim" and m["tick"] >= TICKS]
    assert {"on_scene", "resolved"} <= {m["payload"]["type"] for m in after}
    assert any(vid == "A2" and "OPERATOR OVERRIDE" in s and PRESET["text"] in s for vid, s in run["llm"].systems)
    assert not any(vid != "A2" and PRESET["text"] in s for vid, s in run["llm"].systems)
    assert run["state_after"]["vehicles"]["A2"]["hacked"] is True


def test_every_message_is_signed_and_bad_signatures_are_rejected(run):
    """Every protocol message on the bus carries a valid HMAC; a tampered or forged one is refused."""
    bus = run["engine"].bus
    transcript = bus.transcript()
    protocol_msgs = [m for m in transcript if m["kind"] in ("claim", "bid", "vote")]
    assert protocol_msgs and all(m["signature"] and bus.verify(m) for m in transcript)
    tampered = deepcopy(protocol_msgs[-1])
    tampered["payload"]["reasoning"] = "I am somewhere else."
    forged = {**deepcopy(protocol_msgs[-1]), "signature": "00" * 32}
    for bad in (tampered, forged):
        assert not bus.verify(bad)
        with pytest.raises(ValueError):
            bus.accept(bad)
    assert run["forged_http"] == 404  # no HTTP route accepts messages at all


@pytest.mark.xfail(strict=True, reason="/state is the engine's own dict world plus 'transcript'; no recent_messages or checks")
def test_state_matches_world_model(run):
    """CLAUDE.md step 10: /state is the World plus recent_messages and checks."""
    state = run["state_after"]
    assert "recent_messages" in state and "checks" in state
    World.model_validate(state)


def test_log_records_every_claim(run):
    """Every signed claim on the bus is also a 'claim' line in the JSONL."""
    logged = {(r["message"]["sender"], r["message"]["sequence"]) for r in run["log"] if r["event"] == "claim"}
    claims = {(m["sender"], m["sequence"]) for m in run["engine"].bus.transcript() if m["kind"] == "claim"}
    assert claims and claims <= logged
    assert any(r["event"] == "protocol_plans" for r in run["log"])


@pytest.mark.xfail(strict=True, reason="engine logs events and rounds, not ticks")
def test_log_has_one_line_per_tick(run):
    """The JSONL has a line for every tick."""
    ticks = {r["tick"] for r in run["log"] if r["event"] == "tick"}
    assert ticks >= set(range(1, TICKS + AFTER_INJECT + 1))


def test_baseline_runs_a_different_path(tmp_path):
    """Baseline mode: round 1 only, no counter or vote messages; the log says mode baseline."""
    engine, adapter, driver = build(tmp_path, fake_llm)
    asyncio.run(engine.set_mode("baseline"))
    asyncio.run(step_until(driver, 10, []))
    engine.log.close()
    log = [json.loads(line) for line in engine.log.path.read_text().splitlines()]
    assert log[0]["event"] == "run_started" and log[0]["mode"] == "baseline"
    phases = {r["message"]["payload"]["round"] for r in log if r["event"] in ("claim", "message")
              and r["message"]["kind"] in ("claim", "bid", "vote")}
    assert phases == {1}
    plans = [p for r in log if r["event"] == "protocol_plans" for p in r["plans"]]
    assert plans and all(p["round_reached"] == 1 and not p["tally"] for p in plans)
    assert all(r["mode"] == "baseline" for r in log if r["event"] == "protocol_plans")
    assert engine.world["vehicles"]["A1"]["target_incident_id"] == "I1"


def test_protected_mode_runs_all_three_phases(run):
    """The protected run, for contrast, has propose, counter and vote messages."""
    phases = {r["message"]["payload"]["round"] for r in run["log"] if r["event"] in ("claim", "message")
              and r["message"]["kind"] in ("claim", "bid", "vote")}
    assert phases == {1, 2, 3}
