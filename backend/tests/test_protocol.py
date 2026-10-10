"""run_round with a mocked LLM: propose, counter, vote, resolve. No network."""

import asyncio
from types import SimpleNamespace

import pytest

from agnt911 import protocol, trust_stub
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.fake_world import build_fake_world
from agnt911.models import Bid, Claim, Location


class ScriptedLLM:
    """The honest fake LLM, plus per-test overrides; records (vehicle, round, task) for every call."""

    def __init__(self, votes=None, everyone_bids_on=None, extra_claims=None, fail=()):
        self.calls = []
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
    world = only(build_fake_world(), "I3")
    split = {"A1": 0, "F1": 0, "P1": 0, "A2": 1, "A3": 1, "F2": 1, "P2": 1}
    [plan] = run(world, ScriptedLLM(votes=split))
    assert not plan.escalated and not plan.consensus
    assert plan.assigned == [plan.options[1]]


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
    """Even when all 7 vehicles bid on one incident, round 3 offers at most 4 options."""
    llm = ScriptedLLM(everyone_bids_on="I2")
    plans = run(build_fake_world(), llm)
    assert all(len(p.options) <= protocol.MAX_OPTIONS for p in plans)
    i2 = next(p for p in plans if p.incident == "I2")
    assert len(i2.options) == 4
    i2_tasks = [t for _v, rnd, t in llm.calls if rnd == 3 and "incident I2 " in t]
    assert len(i2_tasks) == 7 and all("3: " in t and "4: " not in t for t in i2_tasks)


def test_full_world_one_vote_call_per_incident_per_vehicle():
    """Five open incidents: rounds 1-2 are 7 calls each, round 3 is 7 per incident that has options."""
    llm = ScriptedLLM()
    plans = run(build_fake_world(), llm)
    assert [p.incident for p in plans] == ["I1", "I2", "I3", "I4", "I5"]
    with_options = sum(1 for p in plans if p.options)
    assert len(llm.calls) == 14 + 7 * with_options
    assert all(p.consensus for p in plans if p.options)


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
