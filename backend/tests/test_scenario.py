"""The redesigned demo: catchable hijacks, renegotiation keeps en-route vehicles, bids are checked, fake calls."""

import asyncio
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from agnt911 import protocol
from agnt911.engine.api import create_app
from agnt911.engine.runtime import Engine
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.fake_world import build_fake_world

BACKEND = Path(__file__).parents[1]


def test_check_scenario_passes_on_the_demo():
    """scripts/check_scenario.py: every vehicle has a witness at every scripted/decision tick; both hijacks catchable."""
    out = subprocess.run([sys.executable, "scripts/check_scenario.py"], cwd=BACKEND, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "PASS" in out.stdout
    # the t140 beat: two severity 5 calls, one free trusted ambulance (A2), and the human is asked about I7
    assert "t140 I7: options ['A2'], contested {'A2': 'I6'}" in out.stdout


def test_renegotiation_keeps_a_vehicle_already_en_route():
    """F1 is driving to I2; a re-run where nobody new bids on I2 keeps F1 instead of dropping it."""
    world = build_fake_world()
    world.incidents = {"I2": world.incidents["I2"]}
    world.vehicles["F1"].status, world.vehicles["F1"].target_incident = "en_route", "I2"
    world.incidents["I2"].status, world.incidents["I2"].assigned = "assigned", ["F1"]

    def busy_fire_engines(system, user, response_model):
        out = fake_llm(system, user, response_model)
        if split_user_message(user)[0]["you"]["type"] == "fire":
            out.bids = []
        return out

    [plan] = asyncio.run(protocol.run_round(world, ["I2"], llm_fn=busy_fire_engines))
    assert plan.assigned == ["F1"] and plan.consensus
    assert plan.evidence == {"kept": {"fire": "F1"}}
    assert [e.type for e in world.events if e.incident == "I2"][-1] == "plan_committed"


def test_lying_bid_is_contradicted_and_not_put_to_the_vote():
    """A hacked ambulance bidding 30 s on a call 1.5 km away is contradicted by the police car next to it."""
    world = build_fake_world()
    world.incidents = {"I4": world.incidents["I4"].model_copy(update={"type": "medical"})}
    world.vehicles["P2"].location = world.vehicles["A3"].location  # a police car can see A3
    world.injections["A3"] = {"kind": "closer_than_you_are"}
    [plan] = asyncio.run(protocol.run_round(world, llm_fn=fake_llm))
    bid_checks = [e for e in world.events if e.type == "claim_verified" and e.data["subject"] == "bid"
                  and e.vehicle == "A3"]
    assert bid_checks and all(e.data["result"] == "contradicted" and e.data["eta_seconds"] == 30 for e in bid_checks)
    assert "A3" not in plan.options and plan.assigned and plan.assigned != ["A3"]
    assert world.vehicles["A3"].trust < 1.0
    honest = [e for e in world.events if e.type == "claim_verified" and e.vehicle != "A3"]
    assert all(e.data["result"] != "contradicted" for e in honest)


def test_fake_call_is_discredited_when_police_arrive(tmp_path):
    """A fake shooting call is dispatched; the first police car on scene finds nothing and the caller loses 0.3."""
    async def scenario():
        engine = Engine(tmp_path, llm_fn=fake_llm)
        try:
            await engine.inject(None, preset_id="fake_call")
            [call] = [i for i in engine.world.incidents.values() if i.source == "operator"]
            assert (call.id, call.type, call.severity, call.source_id) == ("X1", "shooting", 4, "caller-1")
            assert engine.world.callers == {"caller-1": 1.0}
            for _ in range(120):
                await engine.run_round()
                if engine.escalation is not None:  # answer like a judge: keep the plan, take the first option
                    [plan] = engine.escalation["plans"]
                    picks = plan["assigned"] + [plan["evidence"]["options"][0]]
                    await engine.resolve({vid: plan["incident"] for vid in picks})
                await engine.step(1.0)
                if engine.world.incidents["X1"].false_report:
                    break
            call = engine.world.incidents["X1"]
            assert call.false_report and call.status == "resolved"
            assert engine.world.callers == {"caller-1": 0.7}
            [ev] = [e for e in engine.world.events if e.type == "incident_discredited"]
            assert engine.world.vehicles[ev.data["verifier"]].type == "police"
            assert ev.data["credibility_old"] == 1.0 and ev.data["credibility_new"] == 0.7
            assert not [v for v in engine.world.vehicles.values() if v.target_incident == "X1"]
            assert not engine.world.injections  # no vehicle was hacked
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_inject_api_takes_fake_call_without_a_vehicle(tmp_path):
    """/inject: fake_call needs no vehicle_id; a vehicle preset still does."""
    engine = Engine(tmp_path, round_interval_s=100)
    with TestClient(create_app(engine)) as client:
        assert client.post("/inject", json={"preset_id": "fake_call"}).status_code == 200
        assert client.post("/inject", json={"preset_id": "refuse_all"}).status_code == 422
        state = client.get("/state").json()
        assert state["callers"] == {"caller-1": 1.0}
        assert state["incidents"]["X1"]["source"] == "operator" and not state["incidents"]["X1"]["false_report"]
        assert state["compromised"] == []


def test_human_may_move_a_vehicle_off_another_call_and_that_call_is_renegotiated(tmp_path):
    """At the t140 beat the judge sends A2 to I7 instead of I6; I6 goes back to the fleet."""
    async def scenario():
        engine = Engine(tmp_path, llm_fn=fake_llm)
        try:
            while engine.escalation is None:
                await engine.run_round()
                if engine.escalation is None:
                    await engine.step(1.0)
            assert engine.world.tick == 140
            [plan] = engine.escalation["plans"]
            assert plan["incident"] == "I7" and plan["evidence"]["contested"] == {"A2": "I6"}
            await engine.resolve({"A2": "I7"})
            assert engine.world.vehicles["A2"].target_incident == "I7"
            assert engine.world.incidents["I6"].status == "open" and "I6" in engine._pending
            human = [e for e in engine.world.events if e.type == "human_resolved"]
            assert human and human[-1].data["assignments"] == {"A2": "I7"}
        finally:
            await engine.stop()
    asyncio.run(scenario())
