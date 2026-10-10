"""run_round with a mocked LLM: propose, counter, vote, resolve. No network."""

import asyncio
import re
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agnt911 import protocol, trust_stub
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.agents import VehicleAgent
from agnt911.fake_world import build_fake_world
from agnt911.models import Bid, Claim, Incident, Location


class ScriptedLLM:
    """The honest fake LLM, plus per-test overrides; records (vehicle, round, task) for every call."""

    def __init__(self, votes=None, everyone_bids_on=None, extra_claims=None, fail=(), edit_bids=None):
        self.calls = []
        self.edit_bids = edit_bids
        self.votes = votes or {}
        self.everyone_bids_on = everyone_bids_on
        self.extra_claims = extra_claims or {}
        self.fail = set(fail)

    def __call__(self, system, user, response_model):
        payload, task = split_user_message(user)
        vid, rnd = payload["you"]["id"], int(task[len("Round ")])
        self.calls.append((vid, rnd, task))
        if vid in self.fail:
            raise RuntimeError("radio down")
        out = fake_llm(system, user, response_model)
        if rnd == 1 and self.everyone_bids_on:
            out.bids = [b for b in out.bids if b.incident != self.everyone_bids_on]
            out.bids.append(Bid(vehicle=vid, incident=self.everyone_bids_on, eta_seconds=60 + len(vid),
                                capability_match=0.5, cost_to_district=0.1, confidence=0.8,
                                reasoning="I can go."))
        if rnd in (1, 2):
            out.claims += [c(vid, payload) for c in self.extra_claims.get(vid, [])]
            if self.edit_bids:
                out.bids = self.edit_bids(vid, out.bids)
        if rnd == 3 and vid in self.votes:
            out.vote.option_index = self.votes[vid]
        return out

    def rounds(self):
        """Return the set of round numbers that made an LLM call."""
        return {rnd for _vid, rnd, _task in self.calls}


def run(world, llm, incident_ids=None):
    """Run the async protocol to completion."""
    return asyncio.run(protocol.run_round(world, incident_ids, llm_fn=llm))


def only(world, incident_id, severity=None):
    """Strip the world down to one incident, optionally changing its severity."""
    incident = world.incidents[incident_id]
    if severity is not None:
        incident.severity = severity
    world.incidents = {incident_id: incident}
    return world


def resolved_lie(incident_id):
    """Build a claim factory: 'incident is resolved', said from wherever the vehicle really is."""
    return lambda vid, payload: Claim(
        id="x", tick=0, vehicle=vid, type="resolved", incident=incident_id,
        location=Location(**payload["you"]["location"]), confidence=0.95, reasoning="Handled.")


def test_honest_fleet_one_incident_reaches_consensus():
    """7 honest vehicles, 1 incident: consensus in at most 3 LLM rounds of 7 calls each."""
    llm = ScriptedLLM()
    [plan] = run(only(build_fake_world(), "I1"), llm)
    assert plan.consensus and not plan.escalated
    assert plan.round_reached <= 3 and llm.rounds() == {1, 2, 3}
    assert len(llm.calls) == 21
    assert plan.assigned == [plan.options[0]] and plan.assigned[0].startswith("A")
    assert plan.tally[plan.assigned[0]] == 7.0


def test_untrusted_vehicle_votes_weigh_zero():
    """An untrusted vehicle's vote counts 0, it is never an option, and the plan still forms."""
    world = only(build_fake_world(), "I1")
    world.vehicles["A1"].untrusted = True
    llm = ScriptedLLM(votes={"A1": 1})
    [plan] = run(world, llm)
    assert "A1" not in plan.options
    assert plan.tally[plan.options[1]] == 0.0
    assert plan.tally[plan.options[0]] == 6.0
    assert plan.consensus and plan.assigned == [plan.options[0]]


def test_severity_5_split_vote_escalates():
    """Severity 5 with votes split 3/4 (57% < 60%) goes to a human with the two top options."""
    world = only(build_fake_world(), "I1", severity=5)
    split = {"A1": 0, "F1": 0, "P1": 0, "A2": 1, "A3": 1, "F2": 1, "P2": 1}
    [plan] = run(world, ScriptedLLM(votes=split))
    assert plan.escalated and not plan.consensus and plan.assigned == []
    assert plan.tally == {plan.options[0]: 3.0, plan.options[1]: 4.0, **{o: 0.0 for o in plan.options[2:]}}
    assert plan.evidence["options"] == [plan.options[1], plan.options[0]]
    assert len(plan.evidence["evidence"]["votes"]) == 7


def test_low_severity_split_vote_takes_heaviest_option():
    """Severity 3 with a 3/4 split does not escalate: the heaviest option is assigned without consensus."""
    world = only(build_fake_world(), "I2")  # fire: one role, options F1 and F2
    split = {"A1": 0, "F1": 0, "P1": 0, "A2": 1, "A3": 1, "F2": 1, "P2": 1}
    [plan] = run(world, ScriptedLLM(votes=split))
    assert not plan.escalated and not plan.consensus
    assert plan.options == ["F1", "F2"] and plan.assigned == ["F2"]


def test_baseline_skips_counter_and_vote_and_takes_best_bid():
    """baseline=True: only round 1 runs, and the best-scoring bid is the plan."""
    world = only(build_fake_world(), "I1")
    world.baseline = True
    llm = ScriptedLLM()
    [plan] = run(world, llm)
    assert llm.rounds() == {1} and len(llm.calls) == 7
    bids = [m for m in world.recent_messages if m.kind == "bid" and m.incident == "I1"]
    best = max(bids, key=protocol.bid_score)
    assert plan.assigned == [best.vehicle] and plan.checks == []


def test_baseline_believes_a_false_resolved_report():
    """The central dispatcher takes 'resolved' at face value and sends nobody."""
    world = only(build_fake_world(), "I1")
    world.baseline = True
    [plan] = run(world, ScriptedLLM(extra_claims={"A2": [resolved_lie("I1")]}))
    assert plan.assigned == [] and plan.evidence["believed"]["vehicle"] == "A2"


def test_vote_options_never_exceed_four():
    """With 5 ambulances all bidding on one medical call, round 3 offers at most 4 options."""
    world = only(build_fake_world(), "I1")
    for n, lng in ((4, -121.8870), (5, -121.8875)):
        world.vehicles[f"A{n}"] = world.vehicles["A1"].model_copy(
            update={"id": f"A{n}", "location": Location(lat=37.3352, lng=lng)})
    llm = ScriptedLLM(everyone_bids_on="I1")
    [plan] = run(world, llm)
    assert len(plan.options) == protocol.MAX_OPTIONS
    vote_tasks = [t for _v, rnd, t in llm.calls if rnd == 3]
    assert len(vote_tasks) == 9 and all("3: " in t and "4: " not in t for t in vote_tasks)


def test_full_world_one_vote_call_per_role_per_vehicle():
    """Five incidents, six role slots: 7 + 7 + 6x7 calls, and each vehicle wins at most once."""
    llm = ScriptedLLM()
    plans = run(build_fake_world(), llm)
    assert [p.incident for p in plans] == ["I1", "I2", "I3", "I4", "I5"]
    assert len(llm.calls) == 14 + 6 * 7
    assigned = {p.incident: p.assigned for p in plans}
    assert assigned == {"I1": ["A1"], "I2": ["F1"], "I3": ["A2", "P1"], "I4": ["F2"], "I5": ["A3"]}
    winners = [v for p in plans for v in p.assigned]
    assert len(winners) == len(set(winners))


def test_message_log_reads_in_round_order_and_is_stamped():
    """Every claim, bid and vote lands in the log in round order, stamped with its real sender."""
    world = only(build_fake_world(), "I1")
    spoof = lambda vid, payload: Claim(  # noqa: E731
        id="x", tick=99, vehicle="P1", type="observation", incident="I1",
        location=Location(**payload["you"]["location"]), confidence=0.5, reasoning="spoofed sender")
    run(world, ScriptedLLM(extra_claims={"A2": [spoof]}))
    log = world.recent_messages
    assert [m.round for m in log] == sorted(m.round for m in log)
    assert {m.kind for m in log} == {"claim", "bid", "vote"}
    assert all(m.sig == "" for m in log)
    spoofed = [m for m in log if m.kind == "claim" and m.reasoning == "spoofed sender"]
    assert spoofed and all(m.vehicle == "A2" and m.tick == world.tick for m in spoofed)
    assert len({m.id for m in log if m.kind == "claim"}) == sum(1 for m in log if m.kind == "claim")


def test_verification_hook_catches_liar_before_the_vote(monkeypatch):
    """Checked claims update trust; a vehicle that turns untrusted drops out of the options and the tally."""
    def update_trust(vehicle, result):
        trust = vehicle.trust - (0.7 if result == "contradicted" else 0.0)
        return vehicle.model_copy(update={"trust": trust, "untrusted": trust < 0.4})

    fake_trust = SimpleNamespace(
        should_verify=lambda claim, vehicle, round_no: claim.type == "resolved",
        check_claim=lambda claim, world: "contradicted",
        update_trust=update_trust,
        vote_weight=trust_stub.vote_weight,
        should_escalate=trust_stub.should_escalate,
    )
    monkeypatch.setattr(protocol, "trust", fake_trust)
    world = only(build_fake_world(), "I1")
    [plan] = run(world, ScriptedLLM(extra_claims={"A1": [resolved_lie("I1")]}))
    assert world.vehicles["A1"].untrusted
    assert "A1" not in plan.options and plan.consensus and plan.assigned != ["A1"]
    # the lie is repeated in rounds 1 and 2, and each round's copy is checked
    assert [(c["claim_id"], c["result"]) for c in plan.checks] == [
        ("A1-r1-c2", "contradicted"), ("A1-r2-c1", "contradicted")]
    assert {c["verifier"] for c in plan.checks} == {"P1"}


def test_failed_vehicle_does_not_stop_the_round():
    """One vehicle whose LLM call fails is skipped; the rest still reach a plan."""
    [plan] = run(only(build_fake_world(), "I1"), ScriptedLLM(fail={"F2"}))
    assert plan.consensus and plan.tally[plan.assigned[0]] == 6.0


def test_unknown_incident_id_is_rejected():
    """Asking for an incident that does not exist is a caller bug."""
    with pytest.raises(ValueError):
        run(build_fake_world(), ScriptedLLM(), incident_ids=["I9"])


def test_no_open_incidents_makes_no_calls():
    """With nothing open there is nothing to negotiate."""
    world = build_fake_world()
    for incident in world.incidents.values():
        incident.status = "resolved"
    llm = ScriptedLLM()
    assert run(world, llm) == [] and llm.calls == []


# --- capability floor, multi-role plans, no early en_route, no double wins ---

def test_capability_floor_drops_weak_bids_and_empty_slots_take_no_bids_path():
    """Bids under capability 0.5 never become options; a slot with none left goes the no-bids way."""
    def weaken(vid, bids):
        return [b.model_copy(update={"capability_match": 0.4}) if vid == "A2" else b for b in bids]

    [plan] = run(only(build_fake_world(), "I1"), ScriptedLLM(edit_bids=weaken))
    assert "A2" not in plan.options and plan.options == ["A1", "A3"]

    def all_weak(vid, bids):
        return [b.model_copy(update={"capability_match": 0.3}) for b in bids]

    llm = ScriptedLLM(edit_bids=all_weak)
    [plan] = run(only(build_fake_world(), "I1", severity=5), llm)
    assert plan.options == [] and plan.assigned == [] and plan.escalated
    assert 3 not in llm.rounds()


def test_crash_needs_ambulance_and_police_but_low_severity_needs_one():
    """A severity 3 crash votes and assigns two roles; a severity 2 crash only the first role."""
    llm = ScriptedLLM()
    world = only(build_fake_world(), "I3")
    [plan] = run(world, llm)
    assert plan.assigned == ["A2", "P1"] and plan.consensus
    assert sum(1 for _v, rnd, _t in llm.calls if rnd == 3) == 14
    assert {m.role for m in world.recent_messages if m.kind == "vote"} == {"ambulance", "police"}

    world = only(build_fake_world(), "I3", severity=2)
    llm = ScriptedLLM()
    [plan] = run(world, llm)
    assert plan.assigned == ["A2"]
    assert sum(1 for _v, rnd, _t in llm.calls if rnd == 3) == 7
    assert {m.role for m in world.recent_messages if m.kind == "vote"} == {"ambulance"}


def test_every_vehicle_prompt_forbids_early_en_route():
    """The shared prompt tells every vehicle type not to claim en_route or on_scene before assignment."""
    rule = ("Do not claim en_route or on_scene for an incident until the plan has assigned it to you. "
            "Claiming early will be treated as a false claim.")
    world = build_fake_world()
    assert all(rule in VehicleAgent(vid).system_prompt(world) for vid in ("A1", "F1", "P1"))


def test_higher_severity_resolves_first_and_a_vehicle_never_wins_twice():
    """A1 is the only bidder on two calls: the severity 5 call gets it, the other falls to no bids."""
    world = build_fake_world()
    world.incidents = {i: world.incidents[i] for i in ("I1", "I5")}
    world.incidents["I1"].severity = 3
    world.incidents["I5"].severity = 5

    def only_a1(vid, bids):
        return bids if vid == "A1" else []

    i1, i5 = run(world, ScriptedLLM(edit_bids=only_a1))
    assert i5.assigned == ["A1"] and i5.consensus
    assert i1.assigned == [] and not i1.consensus and not i1.escalated
    assert i1.options == ["A1"] and "A1" not in i1.tally  # voted on, then re-tallied without it


def test_injection_reaches_hijacked_vehicles_system_prompt():
    """An injected preset lands in A2's round-1 system prompt under OPERATOR OVERRIDE, and nowhere else."""
    scenario = json.loads((Path(protocol.__file__).parent / "scenarios" / "demo.json").read_text())
    preset = next(p["text"] for p in scenario["presets"] if p["id"] == "on_scene_resolved")
    world = build_fake_world()
    world.injections["A2"] = preset
    systems = {}

    def recording_llm(system, user, response_model):
        payload, task = split_user_message(user)
        if task.startswith("Round 1"):
            systems[payload["you"]["id"]] = system
        return fake_llm(system, user, response_model)

    run(world, recording_llm)
    assert f"OPERATOR OVERRIDE:\n{preset}" in systems["A2"]
    assert all("OPERATOR OVERRIDE" not in s for vid, s in systems.items() if vid != "A2")


def test_minor_hazard_with_no_fire_bids_falls_back_to_police():
    """A severity 2 hazard needs only fire; with no eligible fire bid, police take the slot and the vote."""
    def no_fire_on_i4(vid, bids):
        return [b for b in bids if not (vid.startswith("F") and b.incident == "I4")]

    world = only(build_fake_world(), "I4")
    llm = ScriptedLLM(edit_bids=no_fire_on_i4)
    [plan] = run(world, llm)
    assert plan.options == ["P1", "P2"] and plan.assigned == ["P1"] and plan.consensus
    assert {m.role for m in world.recent_messages if m.kind == "vote"} == {"police"}
    assert sum(1 for _v, rnd, _t in llm.calls if rnd == 3) == 7


def test_minor_hazard_whose_fire_engine_is_taken_falls_back_to_police_at_resolve():
    """F1 bids on the fire and the hazard, wins the fire first; the hazard then goes to the best free police car."""
    def only_f1_fights_fire(vid, bids):
        return [] if vid == "F2" else bids

    world = build_fake_world()
    world.incidents = {i: world.incidents[i] for i in ("I2", "I4")}
    i2, i4 = run(world, ScriptedLLM(edit_bids=only_f1_fights_fire))
    assert i2.assigned == ["F1"] and i2.consensus
    assert i4.options == ["F1"] and "F1" not in i4.tally
    assert i4.assigned == ["P1"] and not i4.consensus and not i4.escalated
    assert i4.evidence == {"fallback": {"vehicle": "P1", "role": "police",
                                        "reason": "every fire option was assigned to a higher-severity incident"}}


def test_prompt_tells_free_vehicles_to_always_bid_and_lists_the_same_roles_as_code():
    """shared.md carries the always-bid rule, and its call-to-type list matches protocol.ROLES exactly."""
    rule = ("If you are free and a call matches your type, always submit a bid, even if far or if it leaves "
            "your district open. Put the cost of distance and district coverage in cost_to_district and "
            "confidence, not in whether you bid. The fleet decides who goes; your job is to offer.")
    shared = (protocol.__file__.rsplit("/", 1)[0] + "/prompts/shared.md")
    text = open(shared).read()
    assert rule in text
    section = text.split("# Which calls match your type", 1)[1].split("\n# ", 1)[0]
    listed = dict(re.findall(r"^- `(\w+)`: (.+)$", section, re.MULTILINE))
    assert {k: v.split(", ") for k, v in listed.items()} == protocol.ROLES


def test_shooting_needs_police_then_ambulance():
    """A severity 3 shooting assigns police and an ambulance; severity 2 needs only police."""
    for severity, expected in ((3, ["P1", "A1"]), (2, ["P1"])):
        world = build_fake_world()
        world.incidents = {"I6": Incident(id="I6", type="shooting", severity=severity,
                                          location=Location(lat=37.3340, lng=-121.8890))}
        [plan] = run(world, ScriptedLLM())
        assert plan.assigned == expected and plan.consensus
