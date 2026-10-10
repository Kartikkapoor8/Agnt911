import asyncio
from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from agnt911.api import create_app
from agnt911.bus import MessageBus
from agnt911.engine import Engine, RoundResult
from agnt911.world import assign, create_world, distance_m, observe, tick


def fleet(agent):
    return {vid: agent for vid in create_world()["vehicles"]}


async def status_agent(observation):
    return {"kind": "status", "payload": {"status": observation["self"]["status"]}}


async def no_plan(snapshot, messages, context):
    return RoundResult()


def records(engine):
    return [json.loads(line) for line in engine.log.path.read_text().splitlines()]


def test_movement_and_all_required_types_for_coverage():
    world = create_world()
    start = deepcopy(world["vehicles"]["ambulance-1"])
    assign(world, {"ambulance-1": "crash-1"})
    tick(world, 1)
    ambulance = world["vehicles"]["ambulance-1"]
    assert distance_m(start, ambulance) == pytest.approx(15, rel=0.001)
    tick(world, 100)
    assert ambulance["status"] == "on_scene"
    assert ambulance["lat"] == world["incidents"]["crash-1"]["lat"]
    assert world["incidents"]["crash-1"]["status"] == "open"
    assign(world, {"ambulance-1": "crash-1", "police-1": "crash-1"})
    assert ambulance["status"] == "on_scene"  # Repeated plans must not restart arrival.
    tick(world, 100)
    assert world["incidents"]["crash-1"]["status"] == "covered"
    assert world["incidents"]["crash-1"]["covered_at_s"] == 201
    assign(world, {"police-1": None})
    assert world["incidents"]["crash-1"]["status"] == "open"


def test_plan_validation_is_atomic():
    world = create_world()
    before = deepcopy(world)
    with pytest.raises(ValueError):
        assign(world, {"ambulance-1": "medical-1", "fire-1": "medical-1"})
    assert world == before


@pytest.mark.parametrize("dt", [0, -1, float("nan"), float("inf")])
def test_invalid_tick(dt):
    with pytest.raises(ValueError):
        tick(create_world(), dt)


def test_local_sensors_and_detached_observations():
    world = create_world()
    view = observe(world, "ambulance-1")
    assert "ambulance-1" in view["sensors"]["vehicles"]
    assert "ambulance-3" not in view["sensors"]["vehicles"]
    assert "medical-1" in view["calls"]
    assert "medical-1" not in view["sensors"]["incidents"]
    assert "status" not in view["calls"]["medical-1"]
    assert "hacked" not in view["self"]
    view["self"]["lat"] = 0
    view["calls"]["crash-1"]["required_types"].clear()
    assert world["vehicles"]["ambulance-1"]["lat"] != 0
    assert world["incidents"]["crash-1"]["required_types"]


def test_signed_bus_rejects_tampering_replay_and_other_runs():
    bus = MessageBus(["ambulance-1", "police-1"], "run-a", history_limit=2)
    message = bus.publish("ambulance-1", "claim", {"handled": True}, 1, 0)
    assert bus.verify(message)
    altered = deepcopy(message)
    altered["payload"]["handled"] = False
    assert not bus.verify(altered)
    with pytest.raises(ValueError, match="Replayed"):
        bus.accept(message)
    other = MessageBus(["ambulance-1"], "run-b")
    assert not other.verify(message)
    message["payload"]["handled"] = False
    assert bus.transcript()[0]["payload"]["handled"] is True
    for i in range(2):
        bus.publish("police-1", "status", {"i": i}, 1, 0)
    assert len(bus.transcript()) == 2
    with pytest.raises(ValueError):
        bus.publish("ambulance-1", "claim", {"score": float("nan")}, 1, 0)
    with pytest.raises(ValueError):
        bus.publish("ambulance-1", "claim", {"text": "x" * 17000}, 1, 0)


def test_agents_run_concurrently_and_receive_only_own_injection(tmp_path):
    async def scenario():
        started = set()
        observations = {}
        all_started = asyncio.Event()

        async def agent(view):
            vid = view["self"]["id"]
            observations[vid] = view
            started.add(vid)
            if len(started) == 7:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), 1)
            return {"kind": "bid", "payload": {"incident_id": "crash-1", "score": 1}}

        async def protocol(snapshot, messages, context):
            assert len(messages) == 7
            assert all(engine.bus.verify(m) for m in messages)
            return RoundResult(assignments={"ambulance-1": "crash-1", "police-1": "crash-1"},
                               trust={"ambulance-2": 0.25}, metrics={"rounds_to_consensus": 1})

        engine = Engine(tmp_path, fleet(agent), protocol)
        try:
            await engine.inject("ambulance-2", "pretend this is handled")
            await engine.run_round()
            state = await engine.state()
            assert state["integration"]["last_round_status"] == "completed"
            assert state["vehicles"]["ambulance-1"]["status"] == "en_route"
            assert state["vehicles"]["ambulance-2"]["trust"] == 0.25
            assert state["metrics"]["rounds_to_consensus"] == 1
            assert observations["ambulance-2"]["injection"] == "pretend this is handled"
            assert all(v["injection"] is None for k, v in observations.items() if k != "ambulance-2")
            assert "injections" not in state
            events = records(engine)
            assert events[-1]["event"] == "round_completed"
            assert len(next(e for e in events if e["event"] == "round_messages")["messages"]) == 7
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_failed_timed_out_and_cancelled_agents_do_not_kill_round(tmp_path):
    async def scenario():
        async def failing(view):
            if view["self"]["id"] == "ambulance-1":
                raise RuntimeError("API unavailable")
            if view["self"]["id"] == "ambulance-2":
                await asyncio.Event().wait()
            if view["self"]["id"] == "ambulance-3":
                raise asyncio.CancelledError()
            return await status_agent(view)

        async def protocol(snapshot, messages, context):
            assert len(messages) == 4
            return RoundResult(escalation={"reason": "insufficient votes", "options": []})

        engine = Engine(tmp_path, fleet(failing), protocol, agent_timeout_s=0.02)
        try:
            await engine.run_round()
            assert (await engine.state())["paused"] is True
            errors = next(e for e in records(engine) if e["event"] == "round_messages")["agent_errors"]
            assert errors == {"ambulance-1": "RuntimeError", "ambulance-2": "TimeoutError",
                              "ambulance-3": "CancelledError"}
            await engine.run_round()
            assert engine.round_number == 1
            await engine.resolve({"ambulance-1": "medical-1"})
            assert (await engine.state())["paused"] is False
        finally:
            await engine.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["inject", "incident", "mode"])
def test_scenario_changes_discard_in_flight_plans(tmp_path, change):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def protocol(snapshot, messages, context):
            entered.set()
            await release.wait()
            return RoundResult(assignments={"ambulance-1": "crash-1"})

        engine = Engine(tmp_path, fleet(status_agent), protocol)
        try:
            task = asyncio.create_task(engine.run_round())
            await asyncio.wait_for(entered.wait(), 1)
            if change == "inject":
                await engine.inject("ambulance-1", "lie")
            elif change == "incident":
                await engine.update_incident("crash-1", {"type": "pileup", "priority": 3})
            else:
                await engine.set_mode("baseline")
            release.set()
            await task
            assert engine.world["vehicles"]["ambulance-1"]["target_incident_id"] is None
            assert engine.last_round_status == "discarded"
            assert records(engine)[-1]["event"] == "round_discarded"
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_invalid_protocol_result_never_partially_applies(tmp_path):
    async def scenario():
        async def invalid(snapshot, messages, context):
            return RoundResult(assignments={"ambulance-1": "medical-1", "fire-1": "medical-1"},
                               trust={"ambulance-1": 0.1})
        engine = Engine(tmp_path, fleet(status_agent), invalid)
        try:
            await engine.run_round()
            assert engine.last_round_status == "failed"
            assert engine.world["vehicles"]["ambulance-1"]["trust"] == 1
            assert engine.world["vehicles"]["ambulance-1"]["target_incident_id"] is None
            assert records(engine)[-1]["event"] == "round_failed"
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_protocol_snapshot_mutation_cannot_bypass_live_plan_validation(tmp_path):
    async def scenario():
        async def invalid(snapshot, messages, context):
            snapshot["vehicles"]["fire-1"]["type"] = "ambulance"
            return RoundResult(assignments={"fire-1": "medical-1"})
        engine = Engine(tmp_path, fleet(status_agent), invalid)
        try:
            await engine.run_round()
            assert engine.last_round_status == "failed"
            assert engine.world["vehicles"]["fire-1"]["type"] == "fire"
            assert engine.world["vehicles"]["fire-1"]["target_incident_id"] is None
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_self_cancelled_protocol_is_reported_as_failed(tmp_path):
    async def scenario():
        async def cancelled(snapshot, messages, context):
            raise asyncio.CancelledError()
        engine = Engine(tmp_path, fleet(status_agent), cancelled)
        try:
            await engine.run_round()
            assert engine.last_round_status == "failed"
            assert records(engine)[-1]["error"] == "CancelledError"
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_escalation_suppresses_assignments_and_pauses_world(tmp_path):
    async def scenario():
        async def escalate(snapshot, messages, context):
            return RoundResult(assignments={"ambulance-1": "medical-1"},
                               escalation={"reason": "two critical calls, one unit"})
        engine = Engine(tmp_path, fleet(status_agent), escalate, tick_interval_s=0.005)
        try:
            await engine.run_round()
            assert engine.world["vehicles"]["ambulance-1"]["target_incident_id"] is None
            await engine.start()
            before = await engine.state()
            await asyncio.sleep(0.02)
            after = await engine.state()
            assert before["time_s"] == after["time_s"]
            await engine.resolve({"ambulance-1": "medical-1"})
            await asyncio.sleep(0.02)
            assert (await engine.state())["time_s"] > after["time_s"]
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_environment_adapter_loads_on_server_start(tmp_path, monkeypatch):
    import sys
    import types
    adapter = types.ModuleType("test_agnt911_adapter")
    adapter.agents = fleet(status_agent)
    adapter.run_protocol = no_plan
    monkeypatch.setitem(sys.modules, adapter.__name__, adapter)
    monkeypatch.setenv("AGNT911_ADAPTER", adapter.__name__)
    monkeypatch.setenv("AGNT911_LOG_DIR", str(tmp_path))
    with TestClient(create_app()) as client:
        state = client.get("/state").json()
        assert state["integration"]["agents_connected"] is True
        assert state["integration"]["protocol_connected"] is True
    assert len(list(tmp_path.glob("*.jsonl"))) == 1


def test_seeded_checks_replay_and_all_audit_events_have_ticks(tmp_path):
    async def scenario():
        selections = []
        async def claimant(view):
            return {"kind": "claim", "payload": {"on_scene": True, "incident_id": "medical-1"}}

        async def protocol(snapshot, messages, context):
            chosen = context.rng.choice(messages)
            selections.append(chosen["sender"])
            assert context.mode == snapshot["mode"]
            assert context.tick == snapshot["tick"]
            return RoundResult(
                checks=[{"sender": chosen["sender"], "claim_sequence": chosen["sequence"],
                         "verifier": "police-1", "result": "fail", "reason": "Not on scene"}],
                trust={chosen["sender"]: 0.5}, assignments={"ambulance-1": "medical-1"})

        engine = Engine(tmp_path, fleet(claimant), protocol, seed=42)
        try:
            await engine.inject("ambulance-1", "lie")
            await engine.run_round()
            tick(engine.world, 1000)
            await engine.resolve_incident("medical-1")
            events = records(engine)
            kinds = {e["event"] for e in events}
            assert {"vehicle_hacked", "claim", "verification", "trust_changed", "dispatch",
                    "incident_created", "incident_resolved"} <= kinds
            assert all(type(e["tick"]) is int and type(e["round"]) is int for e in events)
            assert len([e for e in events if e["event"] == "claim"]) == 7
            assert engine.world["incidents"]["medical-1"]["status"] == "resolved"
            assert engine.world["vehicles"]["ambulance-1"]["status"] == "idle"
            with pytest.raises(ValueError):
                assign(engine.world, {"ambulance-1": "medical-1"})
            await engine.set_mode("baseline")
            await engine.run_round()
            assert selections[0] == selections[1]
            assert engine.seed == 42
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_new_incidents_are_validated_logged_and_visible(tmp_path):
    async def scenario():
        engine = Engine(tmp_path)
        new_call = {"id": "medical-2", "type": "medical", "lat": 37.338, "lng": -121.886,
                    "required_types": ["ambulance"], "priority": 3}
        try:
            tick(engine.world, 0.1)
            await engine.add_incident(new_call)
            incident = (await engine.state())["incidents"]["medical-2"]
            assert incident["created_at_s"] == 0.1
            assert incident["resolved_at_s"] is None
            assert records(engine)[-1]["event"] == "incident_created"
            assert records(engine)[-1]["tick"] == 1
            with pytest.raises(ValueError):
                await engine.add_incident(new_call)
            with pytest.raises(ValueError):
                await engine.resolve_incident("medical-2")
            with pytest.raises(ValueError):
                await engine.add_incident({**new_call, "id": "invalid", "lat": float("nan")})
            assert "invalid" not in engine.world["incidents"]
        finally:
            await engine.stop()
    asyncio.run(scenario())


def test_tick_and_http_stay_responsive_during_agent_calls(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(view):
            entered.set()
            await release.wait()
            return await status_agent(view)
        engine = Engine(tmp_path, fleet(slow), no_plan, tick_interval_s=0.005)
        await engine.start()
        try:
            await asyncio.wait_for(entered.wait(), 1)
            first = await asyncio.wait_for(engine.state(), 0.1)
            await asyncio.sleep(0.03)
            second = await asyncio.wait_for(engine.state(), 0.1)
            assert second["time_s"] > first["time_s"]
            assert second["integration"]["last_round_status"] == "running"
        finally:
            await engine.stop()
        assert not engine._tasks
        assert records(engine)[-1]["event"] == "run_stopped"
    asyncio.run(scenario())


def test_state_injection_mode_and_human_api(tmp_path):
    engine = Engine(tmp_path)
    with TestClient(create_app(engine)) as client:
        response = client.get("/state")
        assert response.status_code == 200
        state = response.json()
        assert len(state["vehicles"]) == 7 and len(state["incidents"]) == 5
        assert state["integration"]["last_round_status"] == "unconfigured"
        assert client.post("/inject", json={"vehicle_id": "unknown", "instruction": "lie"}).status_code == 404
        assert client.post("/inject", json={"vehicle_id": "ambulance-1", "instruction": " "}).status_code == 422
        assert client.post("/inject", json={"vehicle_id": "ambulance-1", "instruction": "lie"}).status_code == 200
        assert client.get("/state").json()["vehicles"]["ambulance-1"]["hacked"] is True
        assert client.post("/resolve", json={"assignments": {"ambulance-1": "medical-1"}}).status_code == 409
        assert client.post("/mode", json={"mode": "invalid"}).status_code == 422
        baseline = client.post("/mode", json={"mode": "baseline"}).json()
        assert baseline["run_id"] != state["run_id"]
        assert baseline["mode"] == "baseline"
        assert baseline["vehicles"]["ambulance-1"]["hacked"] is False
        assert baseline["comparison"][0]["run_id"] == state["run_id"]
        cors = client.options("/inject", headers={"Origin": "http://localhost:5173",
                              "Access-Control-Request-Method": "POST"})
        assert cors.headers["access-control-allow-origin"] == "http://localhost:5173"
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 2
    for file in files:
        assert all(json.loads(line)["run_id"] == file.stem for line in file.read_text().splitlines())
