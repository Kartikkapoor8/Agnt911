"""Greg's core behavior migrated to the team's shared models and real protocol."""

import asyncio
import json
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from agnt911 import protocol, trust_stub
from agnt911.agents import haversine_m
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.fake_world import build_fake_world
from agnt911.models import Claim, Incident, Location, Plan
from agnt911.engine.api import create_app
from agnt911.engine.bus import MessageBus
from agnt911.engine.context import current_rng
from agnt911.engine.runtime import Engine
from agnt911.engine.world import assign, covered, load_scenario, tick


def one_incident(iid="I1"):
    """Keep the real team's fleet and one call for focused negotiation tests."""
    world = build_fake_world()
    world.incidents = {iid: world.incidents[iid]}
    return world


def records(engine):
    """Read flushed JSONL events from this engine's current run."""
    return [json.loads(line) for line in engine.log.path.read_text().splitlines()]


def test_stepped_clock_commits_slow_rounds_at_scenario_ticks(tmp_path):
    """Slow agent calls cannot advance the stepped clock or discard scheduled decisions."""
    async def scenario():
        def slow(system, user, response_model):
            time.sleep(0.2)
            return fake_llm(system, user, response_model)
        engine = Engine(tmp_path, llm_fn=slow, tick_interval_s=0.01)
        await engine.start()
        try:
            assert len(engine._tasks) == 1
            async with asyncio.timeout(15):
                while engine.world.tick < 61:
                    await asyncio.sleep(0.01)
            log = records(engine)
            assert not [r for r in log if r["event"] == "round_discarded"]
            assert [r["tick"] for r in log if r["event"] == "round_completed"][:5] == [0, 5, 20, 40, 60]
            assert any(r["event"] == "dispatch" and r["tick"] == 0 for r in log)
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_invalid_clock_rejected_before_creating_log(tmp_path):
    """Reject unsupported clock settings before opening a run log."""
    with pytest.raises(ValueError, match="Clock"):
        Engine(tmp_path, clock="invalid")
    assert not list(tmp_path.glob("*.jsonl"))


def test_api_reads_clock_and_service_settings(tmp_path, monkeypatch):
    """App startup passes the selected clock and simulation service duration to the engine."""
    monkeypatch.setenv("AGNT911_LLM", "fake")
    monkeypatch.setenv("AGNT911_CLOCK", "wall")
    monkeypatch.setenv("AGNT911_SERVICE_SECONDS", "7.5")
    monkeypatch.setenv("AGNT911_LOG_DIR", str(tmp_path))
    app = create_app()
    with TestClient(app) as client:
        assert client.get("/state").status_code == 200
        assert app.state.engine.clock == "wall"
        assert app.state.engine.service_s == 7.5
        assert len(app.state.engine._tasks) == 2


def test_incident_negotiation_dispatch_movement_and_api_end_to_end(tmp_path, monkeypatch):
    """A newly created incident runs actual agents/protocol, commits, moves, and appears via HTTP."""
    monkeypatch.setattr(protocol, "trust", trust_stub)  # this test pins the stub; trust.py has its own tests
    world = build_fake_world()
    world.incidents = {}
    engine = Engine(tmp_path, world=world, tick_interval_s=0.005, round_interval_s=0.01)
    incident = Incident(id="E1", type="medical", severity=5,
                        location=Location(lat=37.3355, lng=-121.8860))
    asyncio.run(engine.add_incident(incident.model_dump()))
    original = {vid: v.location.model_dump() for vid, v in engine.world.vehicles.items()}
    with TestClient(create_app(engine)) as client:
        deadline = time.monotonic() + 3
        while True:
            response = client.get("/state")
            assert response.status_code == 200
            state = response.json()
            if "E1" in state["plans"] and state["vehicles"]["A1"]["location"] != original["A1"]:
                break
            assert time.monotonic() < deadline, state["integration"]
            time.sleep(0.005)
        plan = state["plans"]["E1"]
        assert plan["consensus"] and not plan["escalated"]
        assert plan["assigned"] == ["A1"]
        assert state["incidents"]["E1"]["assigned"] == ["A1"]
        assert state["incidents"]["E1"]["status"] == "assigned"
        assert state["vehicles"]["A1"]["target_incident"] == "E1"
        assert state["vehicles"]["A1"]["status"] == "en_route"
        assert state["vehicles"]["A1"]["location"] != original["A1"]
        assert {m["kind"] for m in state["recent_messages"]} == {"claim", "bid", "vote"}
        assert all(engine.bus.verify(m) for m in engine.world.recent_messages)
        assert state["integration"]["trust_stub"] is True
        assert not state["integration"]["metrics_available"]
        assert "injections" not in state
    events = records(engine)
    assert {"incident_created", "claim", "bid", "vote", "plan_committed", "dispatch"} <= {e["event"] for e in events}
    assert all("tick" in e and "decision" in e for e in events)
    assert events[-1]["event"] == "run_stopped"


def test_movement_arrival_coverage_and_resolution(tmp_path):
    """Actual crash negotiation dispatches two roles and coverage requires both arrivals."""
    async def scenario():
        engine = Engine(tmp_path, world=one_incident("I3"))
        try:
            await engine.run_round()
            assert engine.plans["I3"].assigned == ["A2", "P1"]
            first = engine.world.vehicles["A2"].location.model_copy()
            await engine.step(1)
            assert haversine_m(first, engine.world.vehicles["A2"].location) == pytest.approx(12, rel=0.001)
            assert not covered(engine.world, engine.world.incidents["I3"])
            await engine.step(1000)
            assert covered(engine.world, engine.world.incidents["I3"])
            assert engine.world.vehicles["A2"].status == "on_scene"
            await engine.resolve_incident("I3")
            assert engine.world.incidents["I3"].status == "resolved"
            assert engine.world.vehicles["A2"].status == "free"
            assert engine.timing["I3"]["covered_at_s"] == 1001
            assert engine.timing["I3"]["resolved_at_s"] == 1001
            with pytest.raises(ValueError):
                assign(engine.world, {"A2": "I3"})
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_service_timer_releases_and_reuses_vehicle(tmp_path):
    """Only physical coverage starts service; completion frees a unit for the next call."""
    async def scenario():
        world = one_incident()
        world.incidents["I1"].location = world.vehicles["A1"].location.model_copy()
        engine = Engine(tmp_path, world=world)
        try:
            await engine.run_round()
            assert engine.timing["I1"]["covered_at_s"] == 0
            await engine.step(19)
            assert engine.world.incidents["I1"].status == "assigned"
            await engine.add_incident({**world.incidents["I1"].model_dump(), "id": "N1"})
            await engine.step(1)
            assert engine.world.incidents["I1"].status == "resolved"
            assert engine.world.vehicles["A1"].status == "free"
            assert engine.world.incidents["I1"].assigned == []
            assert engine.timing["I1"]["resolved_at_s"] == 20
            assert "N1" in engine._pending
            await engine.run_round()
            assert engine.world.vehicles["A1"].target_incident == "N1"
            assert sum(r["event"] == "incident_resolved" for r in records(engine)) == 1
        finally:
            await engine.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("service_s", [0, -1, float("nan"), float("inf")])
def test_invalid_service_time_rejected(tmp_path, service_s):
    """Reject service settings that cannot produce a valid completion time."""
    with pytest.raises(ValueError, match="Service time"):
        Engine(tmp_path, service_s=service_s)


def test_assignment_validation_is_atomic_and_repeated_assignment_keeps_arrival():
    """Invalid plans do not partly apply, and assigning the same target retains on_scene."""
    world = one_incident()
    before = world.model_dump()
    with pytest.raises(ValueError):
        assign(world, {"A1": "I1", "F1": "I1"})
    assert world.model_dump() == before
    assign(world, {"A1": "I1"})
    tick(world, 1000)
    assign(world, {"A1": "I1"})
    assert world.vehicles["A1"].status == "on_scene"


@pytest.mark.parametrize("dt", [0, -1, float("nan"), float("inf")])
def test_invalid_tick(dt):
    """Reject a time step that would corrupt the physical state."""
    with pytest.raises(ValueError):
        tick(one_incident(), dt)


def test_bus_authentication_replay_other_run_and_history_isolation():
    """Shared claims are signed, mutation/replay rejected, and history detached and bounded."""
    claim = Claim(id="C1", tick=0, vehicle="A1", type="available",
                  location=Location(lat=37.33, lng=-121.88), confidence=1, reasoning="Free")
    bus = MessageBus(["A1"], "run-a", history_limit=2)
    bus.publish(claim)
    assert bus.verify(claim)
    altered = claim.model_copy(update={"reasoning": "Changed"})
    assert not bus.verify(altered)
    with pytest.raises(ValueError, match="Replayed"):
        bus.accept(claim)
    assert not MessageBus(["A1"], "run-b").verify(claim)
    claim.reasoning = "Caller mutation"
    assert bus.history()[0].reasoning == "Free"
    for i in range(3):
        bus.publish(claim.model_copy(update={"id": str(i)}))
    assert len(bus.history()) == 2


def test_claims_are_signed_before_trust_checks_and_changes_are_applied(tmp_path, monkeypatch):
    """A test-only trust module receives signed claims and its updates reach live state and logs."""
    async def scenario():
        def injected_llm(system, user, response_model):
            output = fake_llm(system, user, response_model)
            payload, task = split_user_message(user)
            if payload["you"]["id"] == "A1" and task.startswith(("Round 1", "Round 2")):
                output.claims.append(Claim(id="x", tick=0, vehicle="spoof", type="resolved", incident="I1",
                    location=Location(**payload["you"]["location"]), confidence=1, reasoning="Handled"))
            return output

        def verify(claim, world):
            assert engine.bus.verify(claim)
            assert claim.vehicle == "A1"
            return "contradicted"

        def update(vehicle, result):
            score = max(0, vehicle.trust - 0.7)
            return vehicle.model_copy(update={"trust": score, "untrusted": score < 0.4})

        fake_trust = SimpleNamespace(should_verify=lambda c, v, r: c.type == "resolved",
            check_claim=verify, update_trust=update, vote_weight=trust_stub.vote_weight,
            should_escalate=trust_stub.should_escalate)
        monkeypatch.setattr(protocol, "trust", fake_trust)
        engine = Engine(tmp_path, world=one_incident(), llm_fn=injected_llm)
        try:
            await engine.run_round()
            assert engine.last_round_status == "completed"
            assert engine.world.vehicles["A1"].untrusted
            assert "A1" not in engine.plans["I1"].assigned
            assert len(engine.checks) == 2
            assert {e["event"] for e in records(engine)} >= {"verification", "trust_changed"}
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_injection_reaches_only_own_prompt_and_stub_does_not_claim_detection(tmp_path, monkeypatch):
    """Free-text injection reaches only A2's sensor view (its system prompt is unchanged); the stub detects nothing."""
    monkeypatch.setattr(protocol, "trust", trust_stub)  # this test pins the stub; trust.py has its own tests
    async def scenario():
        systems, users = {}, {}
        def recording(system, user, response_model):
            payload, task = split_user_message(user)
            if task.startswith("Round 1"):
                systems[payload["you"]["id"]], users[payload["you"]["id"]] = system, user
            return fake_llm(system, user, response_model)
        engine = Engine(tmp_path, world=one_incident(), llm_fn=recording)
        try:
            await engine.inject("A2", "TEST OVERRIDE PRIVATE")
            assert engine.world.injections["A2"] == {"type": "raw", "text": "TEST OVERRIDE PRIVATE"}
            await engine.run_round()
            assert '"sensor_note": "TEST OVERRIDE PRIVATE"' in users["A2"]
            assert all("TEST OVERRIDE PRIVATE" not in u for vid, u in users.items() if vid != "A2")
            assert systems["A2"] == systems["A1"]  # same type, same prompt: the model is not told to lie
            state = await engine.state()
            sent = {m["vehicle"]: m["compromised"] for m in state["recent_messages"]}
            assert sent["A2"] is True and not any(c for vid, c in sent.items() if vid != "A2")
            assert state["compromised"] == ["A2"]
            assert state["integration"]["trust_stub"] is True
            assert state["vehicles"]["A2"]["trust"] == 1
            assert not state["checks"]
            assert "TEST OVERRIDE PRIVATE" not in json.dumps(state)
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_baseline_belief_does_not_falsify_physical_coverage(tmp_path):
    """A false resolved report makes baseline send nobody, while ground truth remains uncovered."""
    async def scenario():
        def liar(system, user, response_model):
            output = fake_llm(system, user, response_model)
            payload, task = split_user_message(user)
            if payload["you"]["id"] == "A2" and task.startswith("Round 1"):
                output.claims.append(Claim(id="x", tick=0, vehicle="A2", type="resolved", incident="I1",
                    location=Location(**payload["you"]["location"]), confidence=1, reasoning="Handled"))
            return output
        engine = Engine(tmp_path, world=one_incident(), llm_fn=liar)
        try:
            await engine.set_mode("baseline")
            await engine.run_round()
            state = await engine.state()
            assert state["plans"]["I1"]["assigned"] == []
            assert "I1" in state["reported_handled"]
            assert not state["coverage"]["I1"]
            assert state["incidents"]["I1"]["status"] != "resolved"
            assert all(v["target_incident"] is None for v in state["vehicles"].values())
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_busy_vehicles_are_not_redispatched_to_new_calls(tmp_path):
    """The fake LLM's unconditional bids cannot take a unit already serving another incident."""
    async def scenario():
        engine = Engine(tmp_path, world=one_incident())
        try:
            await engine.run_round()
            assert engine.world.vehicles["A1"].target_incident == "I1"
            await engine.add_incident({"id": "I6", "type": "medical", "severity": 3,
                                      "location": {"lat": 37.3355, "lng": -121.8860}})
            await engine.run_round()
            assert engine.world.vehicles["A1"].target_incident == "I1"
            assert "A1" not in engine.plans["I6"].assigned
            assert engine.plans["I6"].assigned
        finally:
            await engine.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["inject", "incident", "mode"])
def test_changed_scenarios_discard_in_flight_plans(tmp_path, change):
    """Changes during protocol execution cannot commit an old plan to the live world."""
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(world, incident_ids, llm_fn):
            entered.set()
            await release.wait()
            return [Plan(incident="I1", assigned=["A1"], round_reached=3, consensus=True, escalated=False)]
        engine = Engine(tmp_path, world=one_incident(), protocol_runner=slow)
        try:
            task = asyncio.create_task(engine.run_round())
            await asyncio.wait_for(entered.wait(), 1)
            if change == "inject":
                await engine.inject("A1", "lie")
            elif change == "incident":
                await engine.update_incident("I1", {"severity": 4})
            else:
                await engine.set_mode("baseline")
            release.set()
            await task
            assert engine.world.vehicles["A1"].target_incident is None
            assert engine.last_round_status == "discarded"
        finally:
            await engine.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["invalid_plan", "invalid_trust", "invalid_check", "timeout", "cancel"])
def test_bad_protocol_results_do_not_partially_apply(tmp_path, failure):
    """Bad output or a failed protocol produces an audit failure without committing assignments/trust."""
    async def scenario():
        async def invalid(world, incident_ids, llm_fn):
            if failure == "timeout":
                await asyncio.Event().wait()
            if failure == "cancel":
                raise asyncio.CancelledError()
            world.vehicles["A1"].trust = float("nan") if failure == "invalid_trust" else 0.5
            return [Plan(incident="I1", assigned=["F1"] if failure == "invalid_plan" else ["A1"],
                         round_reached=3, consensus=True, escalated=False,
                         checks=[{}] if failure == "invalid_check" else [])]
        engine = Engine(tmp_path, world=one_incident(), protocol_runner=invalid, decision_timeout_s=0.01)
        try:
            await engine.run_round()
            assert engine.last_round_status == "failed"
            assert engine.world.vehicles["A1"].target_incident is None
            assert engine.world.vehicles["A1"].trust == 1
            assert records(engine)[-1]["event"] == "round_failed"
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_tick_and_api_state_remain_responsive_during_real_agent_interface(tmp_path):
    """A blocked synchronous LLM call runs in protocol's worker threads while the engine ticks."""
    async def scenario():
        entered, release = threading.Event(), threading.Event()
        def slow(system, user, response_model):
            entered.set()
            assert release.wait(2)
            return fake_llm(system, user, response_model)
        engine = Engine(tmp_path, world=one_incident(), llm_fn=slow, tick_interval_s=0.005, clock="wall")
        await engine.start()
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            first = await asyncio.wait_for(engine.state(), 0.1)
            await asyncio.sleep(0.03)
            second = await asyncio.wait_for(engine.state(), 0.1)
            assert second["tick"] > first["tick"]
            assert second["integration"]["last_round_status"] == "running"
        finally:
            release.set()
            await engine.stop()
        assert not engine._tasks
    asyncio.run(scenario())


def test_human_escalation_pauses_ticks_and_explicit_choice_resumes(tmp_path):
    """A real split vote produces an escalation, which only an explicit human choice clears."""
    async def scenario():
        def split(system, user, response_model):
            output = fake_llm(system, user, response_model)
            payload, task = split_user_message(user)
            if task.startswith("Round 3"):
                output.vote.option_index = 0 if payload["you"]["id"] in {"A1", "F1", "P1"} else 1
            return output
        engine = Engine(tmp_path, world=one_incident(), llm_fn=split)
        try:
            await engine.run_round()
            assert engine.escalation is not None
            tick_before = engine.world.tick
            await engine.step()
            assert engine.world.tick == tick_before
            chosen = engine.escalation["plans"][0]["evidence"]["options"][0]
            await engine.resolve({chosen: "I1"})
            assert engine.escalation is None
            await engine.step()
            assert engine.world.tick == tick_before + 1
            assert engine.world.vehicles[chosen].target_incident == "I1"
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_seeded_rng_and_unique_claim_ids_reset_by_run(tmp_path):
    """Trust can consume per-decision RNG; resets replay draws while claim IDs stay unique per run."""
    async def scenario():
        draws = []
        async def seeded(world, incident_ids, llm_fn):
            draws.append(current_rng().choice(list(world.vehicles)))
            return await protocol.run_round(world, incident_ids, llm_fn)
        engine = Engine(tmp_path, world=one_incident(), protocol_runner=seeded, seed=42)
        try:
            await engine.run_round()
            await engine.inject("A1", "test")
            await engine.run_round()
            claims = [m.id for m in engine.world.recent_messages if m.kind == "claim"]
            assert len(claims) == len(set(claims))
            await engine.set_mode("baseline")
            await engine.run_round()
            assert draws[0] == draws[2]
            assert not engine.world.injections
            assert engine.world.baseline is True
            assert len(engine.comparison) == 1
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_api_human_resolution_requires_all_roles_for_a_critical_crash(tmp_path):
    """A partial human response is rejected; selecting ambulance and police clears escalation."""
    def split(system, user, response_model):
        output = fake_llm(system, user, response_model)
        payload, task = split_user_message(user)
        if task.startswith("Round 3"):
            output.vote.option_index = 0 if payload["you"]["id"] in {"A1", "F1", "P1"} else 1
        return output
    world = one_incident("I3")
    world.incidents["I3"].severity = 5
    engine = Engine(tmp_path, world=world, llm_fn=split, tick_interval_s=0.01)
    with TestClient(create_app(engine)) as client:
        deadline = time.monotonic() + 3
        while not client.get("/state").json()["paused"]:
            assert time.monotonic() < deadline
            time.sleep(0.005)
        incomplete = client.post("/resolve", json={"assignments": {"A2": "I3"}})
        assert incomplete.status_code == 409
        assert client.get("/state").json()["paused"]
        complete = client.post("/resolve", json={"assignments": {"A2": "I3", "P1": "I3"}})
        assert complete.status_code == 200
        state = complete.json()
        assert not state["paused"]
        assert state["vehicles"]["A2"]["target_incident"] == "I3"
        assert state["vehicles"]["P1"]["target_incident"] == "I3"


def test_scripted_events_use_team_scenario_and_preserve_display_fields(tmp_path):
    """Scenario calls, escalation and injection are applied exactly once at their scheduled ticks."""
    async def scenario():
        config = load_scenario()
        engine = Engine(tmp_path, scenario=config)
        try:
            assert set(engine.world.incidents) == {"I1", "I2"}
            for _ in range(5):
                await engine.step()
            assert "I3" in engine.world.incidents
            for _ in range(35):
                await engine.step()
            assert engine.world.incidents["I3"].severity == 5
            for _ in range(20):
                await engine.step()
            assert config["events"][5]["preset_id"] == "on_scene_resolved"
            assert engine.world.injections["A2"] == {"type": "on_scene_resolved"}
            assert sum(e["event"] == "vehicle_hacked" for e in records(engine)) == 1
            assert (await engine.state())["presets"] == config["presets"]
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_api_payloads_mode_reset_cors_and_errors(tmp_path):
    """API follows team IDs/text naming and validates judge operations without leaking text."""
    engine = Engine(tmp_path, world=one_incident(), round_interval_s=100)
    with TestClient(create_app(engine)) as client:
        first = client.get("/state").json()
        assert client.post("/inject", json={"vehicle_id": "unknown", "text": "lie"}).status_code == 404
        assert client.post("/inject", json={"vehicle_id": "A2", "text": " "}).status_code == 422
        assert client.post("/inject", json={"vehicle_id": "A2", "instruction": "old shape"}).status_code == 422
        assert client.post("/inject", json={"vehicle_id": "A2", "preset_id": "no_such_preset"}).status_code == 422
        assert client.post("/inject", json={"vehicle_id": "A2", "text": "x", "preset_id": "refuse_all"}).status_code == 422
        assert client.post("/inject", json={"vehicle_id": "A2", "text": "PRIVATE OVERRIDE"}).status_code == 200
        assert "A2" in client.get("/state").json()["compromised"]
        assert client.post("/resolve", json={"assignments": {"A1": "I1"}}).status_code == 409
        assert client.post("/mode", json={"mode": "wrong"}).status_code == 422
        second = client.post("/mode", json={"mode": "baseline"}).json()
        assert second["run_id"] != first["run_id"]
        assert second["baseline"] is True and not second["compromised"]
        assert second["comparison"][0]["run_id"] == first["run_id"]
        cors = client.options("/inject", headers={"Origin": "http://localhost:5173",
                                                 "Access-Control-Request-Method": "POST"})
        assert cors.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert len(list(tmp_path.glob("*.jsonl"))) == 2


def test_new_incident_validation_and_snapshot_isolation(tmp_path):
    """Scenario hooks reject duplicates/invalid coordinates, and state mutation is isolated."""
    async def scenario():
        engine = Engine(tmp_path, world=one_incident())
        data = {"id": "N1", "type": "medical", "severity": 3,
                "location": {"lat": 37.33, "lng": -121.88}}
        try:
            await engine.add_incident(data)
            with pytest.raises(ValueError):
                await engine.add_incident(data)
            with pytest.raises(ValueError):
                await engine.add_incident({**data, "id": "bad", "location": {"lat": float("nan"), "lng": 0}})
            with pytest.raises(ValueError):
                await engine.resolve_incident("N1")
            state = await engine.state()
            state["vehicles"]["A1"]["location"]["lat"] = 0
            assert engine.world.vehicles["A1"].location.lat != 0
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_labels_persist_outside_feed_and_reset_with_scenario(tmp_path):
    """Scenario and operator labels survive feed eviction and remain separate from shared models."""
    async def scenario():
        engine = Engine(tmp_path)
        try:
            for _ in range(41):
                await engine.step(1)
            assert (await engine.state())["labels"]["I3"] == "Crash now multi-vehicle pileup"
            data = {"id": "N1", "type": "medical", "severity": 3,
                    "location": {"lat": 37.33, "lng": -121.88}, "label": "New call"}
            await engine.add_incident(data)
            assert data["label"] == "New call"
            await engine.update_incident("N1", {"severity": 5, "label": "Critical call"})
            assert engine.world.incidents["N1"].severity == 5
            engine._pending.clear()
            await engine.update_incident("N1", {"label": "Display edit"})
            assert not engine._pending
            with pytest.raises(ValueError, match="Label"):
                await engine.update_incident("N1", {"severity": 1, "label": 123})
            assert engine.world.incidents["N1"].severity == 5
            engine.events.clear()
            state = await engine.state()
            assert state["labels"]["N1"] == "Display edit"
            state["labels"]["N1"] = "snapshot edit"
            assert engine.labels["N1"] == "Display edit"
            assert "label" not in engine.world.incidents["N1"].model_dump()
            await engine.set_mode("baseline")
            state = await engine.state()
            assert "N1" not in state["labels"] and "I3" not in state["labels"]
            assert state["labels"]["I1"] == "Cardiac arrest, 1st & Santa Clara"
        finally:
            await engine.stop()
    asyncio.run(scenario())
