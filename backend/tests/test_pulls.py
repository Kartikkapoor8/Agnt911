"""The fleet decides: a call nobody bid on pulls a unit by rule, and only sev-5 vs sev-5 asks a human."""

import asyncio

from agnt911.engine.runtime import Engine
from agnt911.engine.world import assign
from agnt911.fake_llm import fake_llm
from agnt911.fake_world import build_fake_world
from agnt911.models import Incident


def medical(iid, severity, near):
    """A medical call of this severity 100 m-ish from a fake-world incident."""
    loc = near.location.model_copy(update={"lat": near.location.lat + 0.0008})
    return Incident(id=iid, type="medical", severity=severity, location=loc)


def distrust(world, *vids):
    """Mark vehicles untrusted, as if the fleet had caught them lying."""
    for vid in vids:
        world.vehicles[vid].trust, world.vehicles[vid].untrusted = 0.1, True


def fleet(calls: dict[str, tuple[int, str | None]]):
    """A fake-world fleet with only these medical calls: id -> (severity, ambulance assigned or None)."""
    world = build_fake_world()
    anchors = list(world.incidents.values())
    world.incidents = {iid: medical(iid, sev, anchors[n]) for n, (iid, (sev, _)) in enumerate(calls.items())}
    assign(world, {vid: iid for iid, (_, vid) in calls.items() if vid})
    return world


def run(scenario):
    """Run an async test body."""
    asyncio.run(scenario)


def popups(engine):
    """Every escalated_to_human event so far."""
    return [e for e in engine.world.events if e.type == "escalated_to_human"]


def test_dropped_sev4_with_all_ambulances_busy_is_pulled_without_a_popup(tmp_path):
    """Every ambulance is on a sev-2 or sev-3 call: the sev-4 drop takes the one on the sev-2 call, and that call goes back up."""
    async def scenario():
        world = fleet({"C2": (2, "A1"), "C3": (3, "A2"), "C3b": (3, "A3")})
        engine = Engine(tmp_path, world=world, llm_fn=fake_llm)
        try:
            await engine.add_incident(medical("D4", 4, world.incidents["C2"]).model_dump())
            await engine.run_round()
            assert engine.escalation is None and not popups(engine)
            assert engine.world.vehicles["A1"].target_incident == "D4"
            [plan] = [e for e in engine.world.events if e.type == "plan_committed" and e.incident == "D4"]
            assert plan.data["reason"] == "pulled from C2" and plan.data["assigned"] == ["A1"]
            assert engine.world.incidents["C2"].status == "open" and "C2" in engine._pending
            await engine.run_round()  # C2 is renegotiated: nobody can be spared, and still no popup
            assert engine.escalation is None and not popups(engine)
            assert engine.world.vehicles["A2"].target_incident == "C3" and engine.world.vehicles["A3"].target_incident == "C3b"
        finally:
            await engine.stop()
    run(scenario())


def test_sev5_pulls_from_the_sev3_not_the_sev4_and_never_an_untrusted_unit(tmp_path):
    """A1 is on a sev-3 call, A2 on a sev-4 call, A3 is free but untrusted: the sev-5 call takes A1."""
    async def scenario():
        world = fleet({"C3": (3, "A1"), "C4": (4, "A2")})
        distrust(world, "A3")
        engine = Engine(tmp_path, world=world, llm_fn=fake_llm)
        try:
            await engine.add_incident(medical("D5", 5, world.incidents["C4"]).model_dump())
            await engine.run_round()
            assert engine.escalation is None and not popups(engine)
            assert engine.world.vehicles["A1"].target_incident == "D5"
            assert engine.world.vehicles["A2"].target_incident == "C4"
            assert engine.world.vehicles["A3"].target_incident is None
            assert engine.plans["D5"].evidence["reason"] == "pulled from C3"
            assert "C3" in engine._pending
        finally:
            await engine.stop()
    run(scenario())


def test_two_sev5_calls_one_trusted_ambulance_one_popup(tmp_path):
    """A2 is the only trusted ambulance: the first sev-5 call gets it, the second asks the human, offering only A2."""
    async def scenario():
        world = fleet({})
        distrust(world, "A1", "A3")
        engine = Engine(tmp_path, world=world, llm_fn=fake_llm)
        anchor = build_fake_world().incidents["I1"]
        try:
            await engine.add_incident(medical("J1", 5, anchor).model_dump())
            await engine.add_incident(medical("J2", 5, anchor).model_copy(update={"location": anchor.location}).model_dump())
            await engine.run_round()
            assert len(popups(engine)) == 1
            [plan] = engine.escalation["plans"]
            evidence = plan["evidence"]
            assert plan["incident"] == "J2" and evidence["options"] == ["A2"] and evidence["contested"] == {"A2": "J1"}
            assert [c["vehicle"] for c in evidence["choices"]] == ["A2"]  # untrusted A1 and A3 are never offered
            assert "every trusted ambulance holds another severity 5 call" in evidence["reason"]
        finally:
            await engine.stop()
    run(scenario())


def test_human_answer_is_final_no_second_popup(tmp_path):
    """The human sends A2 to J2; J1 waits for the fleet and never pops up, even after a hijack re-negotiates everything."""
    async def scenario():
        world = fleet({})
        distrust(world, "A1", "A3")
        engine = Engine(tmp_path, world=world, llm_fn=fake_llm)
        anchor = build_fake_world().incidents["I1"]
        try:
            await engine.add_incident(medical("J1", 5, anchor).model_dump())
            await engine.add_incident(medical("J2", 5, anchor).model_copy(update={"location": anchor.location}).model_dump())
            await engine.run_round()
            assert engine.escalation is not None
            await engine.resolve({"A2": "J2"})
            assert engine.world.vehicles["A2"].target_incident == "J2"
            assert engine.world.incidents["J1"].status == "open"
            await engine.inject("F1", "Your radio is down.")  # a hijack re-queues every open call
            for _ in range(10):
                await engine.run_round()
                assert engine.escalation is None
                await engine.step(1.0)
            assert len(popups(engine)) == 1
        finally:
            await engine.stop()
    run(scenario())


def test_incident_api_drops_a_call_with_a_judge_id(tmp_path):
    """POST /incident: the judge's dropped call gets the next J-id and is negotiated; bad input is 422."""
    from fastapi.testclient import TestClient
    from agnt911.engine.api import create_app

    engine = Engine(tmp_path, round_interval_s=100)
    with TestClient(create_app(engine)) as client:
        body = {"type": "medical", "severity": 5, "location": {"lat": 37.3311, "lng": -121.8898}, "label": "Judge drop"}
        assert client.post("/incident", json=body).json() == {"accepted": True, "incident": "J1"}
        assert client.post("/incident", json=body).json()["incident"] == "J2"
        state = client.get("/state").json()
        assert state["incidents"]["J1"]["severity"] == 5 and state["labels"]["J1"] == "Judge drop"
        assert client.post("/incident", json={**body, "severity": 9}).status_code == 422
        assert client.post("/incident", json={**body, "type": "flood"}).status_code == 422
