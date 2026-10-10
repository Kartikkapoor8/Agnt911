"""Black-box acceptance tests for agnt911.trust, written from the spec alone. No network."""

import asyncio
import copy
import hashlib
import inspect
import math
import re

import pytest

from agnt911 import protocol, trust
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.fake_world import build_fake_world
from agnt911.models import Bid, Claim, Location, Vote

EARTH_RADIUS_M = 6_371_000.0
CONFIRMED, CONTRADICTED, UNVERIFIABLE = "confirmed", "contradicted", "unverifiable"


# ---------------------------------------------------------------- helpers


class ScriptedLLM:
    """The honest fake LLM, plus per-test overrides; records (vehicle, round, task) for every call."""

    def __init__(self, votes=None, extra_claims=None):
        self.calls = []
        self.votes = votes or {}
        self.extra_claims = extra_claims or {}

    def __call__(self, system, user, response_model):
        payload, task = split_user_message(user)
        vid, rnd = payload["you"]["id"], int(task[len("Round ")])
        self.calls.append((vid, rnd, task))
        out = fake_llm(system, user, response_model)
        if rnd in (1, 2):
            out.claims += [c(vid, payload) for c in self.extra_claims.get(vid, [])]
        if rnd == 3 and vid in self.votes:
            out.vote.option_index = self.votes[vid]
        return out


def run(world, llm, incident_ids=None):
    """Run the async protocol to completion against the real trust module."""
    assert protocol.trust.__name__ == "agnt911.trust"
    return asyncio.run(protocol.run_round(world, incident_ids, llm_fn=llm))


def only(world, incident_id):
    """Strip the world down to one incident."""
    world.incidents = {incident_id: world.incidents[incident_id]}
    return world


def north(loc, metres):
    """Return a Location `metres` due north of `loc`."""
    return Location(lat=loc.lat + math.degrees(metres / EARTH_RADIUS_M), lng=loc.lng)


def east(loc, metres):
    """Return a Location `metres` due east of `loc`."""
    dlng = math.degrees(metres / (EARTH_RADIUS_M * math.cos(math.radians(loc.lat))))
    return Location(lat=loc.lat, lng=loc.lng + dlng)


def make_claim(world, vid, ctype, incident=None, location=None, value=None, **extra):
    """Build a claim from `vid`, located at its real position unless `location` is given."""
    if location is None:
        location = world.vehicles[vid].location
    return Claim(
        id=extra.get("id", f"{vid}-r1-c1"), tick=extra.get("tick", world.tick), vehicle=vid,
        type=ctype, incident=incident, location=location, value=value,
        confidence=extra.get("confidence", 0.9), reasoning="test claim", round=extra.get("round", 1),
    )


def lie(ctype, incident_id, at_incident=False):
    """Build a claim factory for a hacked vehicle: at its real position, or pretending to be at the incident."""

    def factory(vid, payload):
        here = Location(**payload["you"]["location"])
        if at_incident:
            here = build_fake_world().incidents[incident_id].location
        return Claim(id="x", tick=0, vehicle=vid, type=ctype, incident=incident_id,
                     location=here, confidence=0.95, reasoning="Handled, close it.")

    return factory


def expected_trust(contradictions):
    """Trust after n contradicted checks per the spec: integer hundredths, 0.3 each, floored at 0."""
    return max(0, 100 - 30 * contradictions) / 100


def assert_no_logged_claim_contradicted(world, vehicles):
    """Every claim in the log from the given vehicles checks as not contradicted against the world."""
    for m in world.recent_messages:
        if m.kind == "claim" and m.vehicle in vehicles:
            assert trust.check_claim(m, world) != CONTRADICTED, m


# ---------------------------------------------------------------- 1. contract

CONTRACT = {
    "should_verify": ["claim", "vehicle", "round_no"],
    "check_claim": ["claim", "world"],
    "update_trust": ["vehicle", "result"],
    "vote_weight": ["vehicle"],
    "should_escalate": ["round_state"],
}

HELPERS = {
    "witness": ["claim", "world"],
    "feed_result": ["result"],
    "check_line": ["checker", "claim", "result"],
    "check_event": ["claim", "checker", "result", "trust_before", "trust_after"],
    "engine_check": ["claim", "checker", "result", "claim_sequence"],
    "track_incidents": ["times", "world"],
    "tick_record": ["world", "times", "mode", "checks_run", "escalated"],
    "metrics": ["records", "times"],
}

CONSTANTS = {
    "CONFIRMED": "confirmed", "CONTRADICTED": "contradicted", "UNVERIFIABLE": "unverifiable",
    "SENSOR_RADIUS_M": 500.0, "ON_SCENE_RADIUS_M": 100.0, "CONTRADICT_DISTANCE_M": 300.0,
    "LOCATION_TOLERANCE_M": 300.0, "EN_ROUTE_SLACK_M": 450.0, "SECTOR_CONTRADICT_M": 450.0,
    "SECTOR_CONFIRM_M": 550.0, "OBSERVATION_MATCH_M": 150.0, "MAX_SPEED_MPS": 30.0,
    "START_TRUST": 1.0, "TRUST_DROP": 0.3, "UNTRUSTED_BELOW": 0.4, "CONSENSUS_SHARE": 0.6,
    "ESCALATE_SEVERITY": 4, "SAMPLE_CUTOFF": 86,
}


def test_protocol_uses_the_real_trust_module():
    """protocol picked agnt911.trust at import time, not the stub."""
    assert protocol.trust is trust and protocol.trust.__name__ == "agnt911.trust"


@pytest.mark.parametrize("name", sorted(CONTRACT))
def test_contract_functions_have_exact_parameters(name):
    """The five contract functions take exactly the spec's parameter names, nothing extra."""
    assert list(inspect.signature(getattr(trust, name)).parameters) == CONTRACT[name]


@pytest.mark.parametrize("name", sorted(HELPERS))
def test_helpers_have_spec_parameters(name):
    """Each public helper exists with the spec's parameter names; engine_check defaults claim_sequence to None."""
    sig = inspect.signature(getattr(trust, name))
    assert list(sig.parameters) == HELPERS[name]
    if name == "engine_check":
        assert sig.parameters["claim_sequence"].default is None


def test_constants_have_spec_values():
    """Every module constant exists with the spec value."""
    for name, value in CONSTANTS.items():
        assert getattr(trust, name) == value, name


def test_public_functions_have_one_line_docstrings():
    """Every public function has a non-empty one-line docstring (hackathon rule 7.4)."""
    for name in [*CONTRACT, *HELPERS]:
        doc = (getattr(trust, name).__doc__ or "").strip()
        assert doc and "\n" not in doc, name


def test_check_claim_returns_only_the_three_result_strings():
    """check_claim returns exactly confirmed, contradicted or unverifiable for a mix of claims."""
    world = build_fake_world()
    claims = [
        make_claim(world, "P1", "available"),
        make_claim(world, "A2", "resolved", "I1"),
        make_claim(world, "F2", "available"),
        make_claim(world, "A2", "sector_clear"),
    ]
    results = [trust.check_claim(c, world) for c in claims]
    assert results == [CONFIRMED, CONTRADICTED, UNVERIFIABLE, CONTRADICTED]


# ---------------------------------------------------------------- 2. demo scenarios through protocol


def test_honest_full_world_nobody_loses_trust_and_every_call_gets_a_vehicle():
    """Honest fleet, five incidents: trust stays 1.0, nobody untrusted, nothing escalates, every call is assigned."""
    world = build_fake_world()
    plans = run(world, ScriptedLLM())
    assert all(v.trust == 1.0 and not v.untrusted for v in world.vehicles.values())
    assert all(p.assigned and not p.escalated for p in plans if p.options)
    assert all(c["result"] != CONTRADICTED for p in plans for c in p.checks)
    assert_no_logged_claim_contradicted(world, set(world.vehicles))


@pytest.mark.parametrize("at_incident", [False, True], ids=["says-from-real-spot", "pretends-at-I1"])
@pytest.mark.parametrize("liar", ["A3", "P2"])
def test_hacked_vehicle_lying_about_i1_is_caught_and_not_assigned(liar, at_incident):
    """A hacked vehicle claiming resolved and on_scene for I1 in rounds 1-2 is contradicted 4 times and dropped."""
    world = only(build_fake_world(), "I1")
    lies = [lie("resolved", "I1", at_incident), lie("on_scene", "I1", at_incident)]
    [plan] = run(world, ScriptedLLM(extra_claims={liar: lies}))
    liar_checks = [c for c in plan.checks if c["vehicle"] == liar and c["result"] == CONTRADICTED]
    assert {c["claim_id"] for c in liar_checks} == {
        f"{liar}-r1-c2", f"{liar}-r1-c3", f"{liar}-r2-c1", f"{liar}-r2-c2"}
    assert world.vehicles[liar].trust == expected_trust(len(liar_checks)) == 0.0
    assert world.vehicles[liar].untrusted
    assert all(v.trust == 1.0 and not v.untrusted for vid, v in world.vehicles.items() if vid != liar)
    assert liar not in plan.options and liar not in plan.assigned
    assert plan.consensus and len(plan.assigned) == 1
    assert world.vehicles[plan.assigned[0]].type == "ambulance"


def test_single_lie_repeated_twice_leaves_vehicle_suspected_not_untrusted():
    """A2 lying sector_clear in rounds 1 and 2 drops to exactly 0.4: still trusted, vote weight 0.4."""
    world = only(build_fake_world(), "I3")
    [plan] = run(world, ScriptedLLM(extra_claims={"A2": [lie("sector_clear", None)]}))
    a2 = world.vehicles["A2"]
    assert a2.trust == 0.4 and not a2.untrusted
    assert trust.vote_weight(a2) == 0.4
    assert [c["claim_id"] for c in plan.checks if c["result"] == CONTRADICTED] == ["A2-r1-c2", "A2-r2-c1"]


def test_two_hacked_ambulances_honest_fleet_still_sends_a1():
    """f=2: A2 and A3 both lie about I1 and vote together; the honest five send A1 with consensus."""
    world = only(build_fake_world(), "I1")
    lies = [lie("resolved", "I1"), lie("on_scene", "I1")]
    llm = ScriptedLLM(extra_claims={"A2": lies, "A3": lies}, votes={"A2": 1, "A3": 1})
    [plan] = run(world, llm)
    assert world.vehicles["A2"].untrusted and world.vehicles["A3"].untrusted
    assert plan.consensus and not plan.escalated
    assert plan.assigned == ["A1"]
    assert plan.tally["A1"] == 5.0


def test_two_hacked_mixed_types_honest_fleet_still_assigns_honest_ambulance():
    """f=2: A3 and P2 both lie about I1; the plan assigns an honest ambulance, never a liar."""
    world = only(build_fake_world(), "I1")
    lies = [lie("resolved", "I1", True), lie("on_scene", "I1", True)]
    [plan] = run(world, ScriptedLLM(extra_claims={"A3": lies, "P2": lies}, votes={"A3": 1, "P2": 1}))
    assert world.vehicles["A3"].untrusted and world.vehicles["P2"].untrusted
    assert plan.consensus and len(plan.assigned) == 1
    winner = plan.assigned[0]
    assert winner not in ("A3", "P2") and world.vehicles[winner].type == "ambulance"


def test_dispatched_honest_vehicle_saying_available_is_not_flagged():
    """A1 already en_route to I1 still says 'available' (fake LLM); it is never contradicted and keeps trust 1.0."""
    world = build_fake_world()
    world.incidents["I1"].status = "assigned"
    world.incidents["I1"].assigned = ["A1"]
    a1 = world.vehicles["A1"]
    a1.status, a1.target_incident = "en_route", "I1"
    a1.location = east(a1.location, 80)
    plans = run(world, ScriptedLLM(), incident_ids=["I5"])
    assert world.vehicles["A1"].trust == 1.0 and not world.vehicles["A1"].untrusted
    assert all(c["result"] != CONTRADICTED for p in plans for c in p.checks)
    assert_no_logged_claim_contradicted(world, set(world.vehicles))
    claim = next(m for m in world.recent_messages if m.kind == "claim" and m.vehicle == "A1")
    assert claim.type == "available" and trust.check_claim(claim, world) == UNVERIFIABLE


@pytest.mark.parametrize("status", ["free", "en_route", "on_scene"])
@pytest.mark.parametrize("vid", ["A1", "A2", "A3", "F1", "F2", "P1", "P2"])
def test_fake_llm_available_claim_never_contradicted_in_any_status(vid, status):
    """The fake LLM's honest 'available' claim is never contradicted, whatever the vehicle's status."""
    world = build_fake_world()
    v = world.vehicles[vid]
    if status != "free":
        v.status, v.target_incident = status, "I1"
    claim = Claim(id="x", tick=0, vehicle=vid, type="available", location=v.location,
                  confidence=1.0, reasoning=f"Free in {v.home_district}.")
    result = trust.check_claim(claim, world)
    assert result != CONTRADICTED
    if status != "free":
        assert result == UNVERIFIABLE


def test_baseline_mode_never_changes_trust():
    """baseline=True: even with a liar, no trust changes and no checks are run."""
    world = only(build_fake_world(), "I1")
    world.baseline = True
    lies = [lie("resolved", "I1"), lie("on_scene", "I1")]
    [plan] = run(world, ScriptedLLM(extra_claims={"A3": lies}))
    assert all(v.trust == 1.0 and not v.untrusted for v in world.vehicles.values())
    assert plan.checks == []


# ---------------------------------------------------------------- 3. false-positive guards


@pytest.mark.parametrize("metres", [50, 150, 250])
@pytest.mark.parametrize("direction", [north, east])
def test_small_position_jitter_on_honest_available_is_confirmed(metres, direction):
    """P1 reporting its position 50-250 m off while free is still confirmed, never contradicted."""
    world = build_fake_world()
    claim = make_claim(world, "P1", "available", location=direction(world.vehicles["P1"].location, metres))
    assert trust.check_claim(claim, world) == CONFIRMED


def test_en_route_vehicle_gets_extra_position_slack():
    """An en_route vehicle 600 m off its reported spot is within the 750 m slack: not contradicted."""
    world = build_fake_world()
    p1 = world.vehicles["P1"]
    p1.status, p1.target_incident = "en_route", "I1"
    claim = make_claim(world, "P1", "available", location=north(p1.location, 600))
    assert trust.check_claim(claim, world) == UNVERIFIABLE


def test_on_scene_150m_away_is_unverifiable_not_contradicted():
    """A1 150 m from I1 claiming on_scene is between 100 and 300 m: unverifiable."""
    world = build_fake_world()
    i1 = world.incidents["I1"].location
    world.vehicles["A1"].location = north(i1, 150)
    assert trust.check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == UNVERIFIABLE
    assert trust.check_claim(make_claim(world, "A1", "on_scene", "I1", location=i1), world) == UNVERIFIABLE


def test_on_scene_confirmed_close_by_or_by_status():
    """on_scene is confirmed within 100 m, or when the sender is on_scene targeting the incident."""
    world = build_fake_world()
    i1 = world.incidents["I1"].location
    world.vehicles["A1"].location = north(i1, 50)
    assert trust.check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == CONFIRMED
    world.vehicles["A1"].location = north(i1, 250)
    world.vehicles["A1"].status, world.vehicles["A1"].target_incident = "on_scene", "I1"
    assert trust.check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == CONFIRMED


@pytest.mark.parametrize("at_incident", [False, True], ids=["real-spot", "writes-destination"])
@pytest.mark.parametrize("vid,eta", [("A1", 46), ("A3", 160)])
def test_en_route_right_after_plan_while_still_free_is_unverifiable(vid, eta, at_incident):
    """A vehicle says en_route to I1 with an honest ETA before the engine set its target: unverifiable."""
    world = build_fake_world()
    loc = world.incidents["I1"].location if at_incident else None
    claim = make_claim(world, vid, "en_route", "I1", location=loc, value={"eta_seconds": eta})
    assert trust.check_claim(claim, world) == UNVERIFIABLE


def test_en_route_confirmed_once_target_is_set():
    """en_route is confirmed when the sender targets that incident and is en_route."""
    world = build_fake_world()
    world.vehicles["A1"].status, world.vehicles["A1"].target_incident = "en_route", "I1"
    claim = make_claim(world, "A1", "en_route", "I1", value={"eta_seconds": 46})
    assert trust.check_claim(claim, world) == CONFIRMED


def test_observation_of_unknown_new_incident_is_unverifiable():
    """An observation naming a new incident id far from any known incident is unverifiable, not contradicted."""
    world = build_fake_world()
    claim = make_claim(world, "P1", "observation", "I9", value={"type": "fire", "severity": 4})
    assert trust.check_claim(claim, world) == UNVERIFIABLE


def test_observation_matching_a_known_incident_is_confirmed_even_with_wrong_severity():
    """An observation within 150 m of a real incident is confirmed; severity is never grounds to contradict."""
    world = build_fake_world()
    i1 = world.incidents["I1"].location
    named = make_claim(world, "P1", "observation", "I1", location=north(i1, 60), value={"severity": 1})
    unnamed = make_claim(world, "P1", "observation", None, location=north(i1, 60))
    assert trust.check_claim(named, world) == CONFIRMED
    assert trust.check_claim(unnamed, world) == CONFIRMED


@pytest.mark.parametrize("vid,iid", [("F1", "I1"), ("A1", "I2"), ("A1", "I4"), ("A3", "I1")])
@pytest.mark.parametrize("with_type", [False, True])
def test_capability_refusal_is_unverifiable(vid, iid, with_type):
    """Saying 'I can't handle it' is policy, not a lie: unverifiable even for hard-incapable vehicles."""
    world = build_fake_world()
    value = {"can_handle": False}
    if with_type:
        value["type"] = world.vehicles[vid].type
    assert trust.check_claim(make_claim(world, vid, "capability", iid, value=value), world) == UNVERIFIABLE


@pytest.mark.parametrize("vid", ["P1", "P2"])
@pytest.mark.parametrize("iid", ["I1", "I2", "I3", "I4", "I5"])
@pytest.mark.parametrize("value", [None, {"can_handle": True}, {"type": "police", "can_handle": True},
                                   {"type": "police"}])
def test_police_capability_never_contradicted(vid, iid, value):
    """Police capability claims are never contradicted; confirmed for crash and hazard, else unverifiable."""
    world = build_fake_world()
    result = trust.check_claim(make_claim(world, vid, "capability", iid, value=value), world)
    expected = CONFIRMED if world.incidents[iid].type in ("crash", "hazard") else UNVERIFIABLE
    assert result == expected


def test_capability_confirmed_for_acceptable_type():
    """A3 saying it can handle the medical call I1 is confirmed."""
    world = build_fake_world()
    claim = make_claim(world, "A3", "capability", "I1", value={"type": "ambulance", "can_handle": True})
    assert trust.check_claim(claim, world) == CONFIRMED


def test_honest_or_overstated_numbers_are_not_lies():
    """Honest distance/ETA, modest understatement, overstatement and bool values are never contradicted."""
    world = build_fake_world()
    for value in ({"distance_m": 388}, {"distance_m": 250}, {"distance_m": 5000},
                  {"eta_seconds": 62}, {"eta_seconds": 900}, {"distance_m": True}, {"eta_seconds": False}):
        claim = make_claim(world, "F1", "observation", "I1", location=world.incidents["I1"].location,
                           value=value)
        assert trust.check_claim(claim, world) != CONTRADICTED, value


def test_sector_clear_with_only_assigned_incident_nearby_is_not_contradicted():
    """F1 sector_clear while I1 (388 m) is assigned, not open: unverifiable, not a lie."""
    world = build_fake_world()
    world.incidents["I1"].status = "assigned"
    assert trust.check_claim(make_claim(world, "F1", "sector_clear"), world) == UNVERIFIABLE


def test_sector_clear_confirmed_when_nothing_unresolved_within_550m():
    """F1 sector_clear once I1 is resolved (next incident 631 m away) is confirmed by its witness."""
    world = build_fake_world()
    world.incidents["I1"].status = "resolved"
    assert trust.check_claim(make_claim(world, "F1", "sector_clear"), world) == CONFIRMED


def test_resolved_with_acceptable_vehicle_on_scene_is_not_contradicted():
    """A2 says I1 is resolved while A1 is 60 m from I1: unverifiable, not a lie."""
    world = build_fake_world()
    world.vehicles["A1"].location = north(world.incidents["I1"].location, 60)
    assert trust.check_claim(make_claim(world, "A2", "resolved", "I1"), world) == UNVERIFIABLE


def test_resolved_counts_untrusted_vehicles_as_physically_present():
    """Physical truth: an untrusted ambulance 60 m from I1 still makes 'resolved' unverifiable."""
    world = build_fake_world()
    world.vehicles["A1"].location = north(world.incidents["I1"].location, 60)
    world.vehicles["A1"].untrusted, world.vehicles["A1"].trust = True, 0.1
    assert trust.check_claim(make_claim(world, "A2", "resolved", "I1"), world) == UNVERIFIABLE


def test_resolved_on_resolved_incident_is_confirmed():
    """'resolved' about an incident the world already shows resolved is confirmed."""
    world = build_fake_world()
    world.incidents["I1"].status = "resolved"
    assert trust.check_claim(make_claim(world, "A1", "resolved", "I1"), world) == CONFIRMED


def test_unknown_sender_is_unverifiable():
    """A claim from a vehicle not in the world is unverifiable."""
    world = build_fake_world()
    claim = make_claim(world, "X9", "resolved", "I1", location=world.incidents["I1"].location)
    assert trust.check_claim(claim, world) == UNVERIFIABLE


def test_claim_with_no_witness_is_unverifiable():
    """F2 reporting a position 1 km off, with nobody within 500 m of either point, is unverifiable."""
    world = build_fake_world()
    far = north(world.vehicles["F2"].location, -1000)
    assert trust.check_claim(make_claim(world, "F2", "available", location=far), world) == UNVERIFIABLE


def test_resolved_lie_with_no_witness_is_unverifiable():
    """A2 says I3 is resolved but nobody but A2 is within 500 m of I3 or A2: unverifiable by spec."""
    world = build_fake_world()
    assert trust.check_claim(make_claim(world, "A2", "resolved", "I3"), world) == UNVERIFIABLE


def test_check_claim_ignores_confidence():
    """Changing claim.confidence never changes the result."""
    world = build_fake_world()
    for ctype, iid, vid in [("resolved", "I1", "A2"), ("available", None, "P1"), ("on_scene", "I1", "A3")]:
        results = {trust.check_claim(make_claim(world, vid, ctype, iid, confidence=c), world)
                   for c in (0.0, 0.5, 1.0)}
        assert len(results) == 1, ctype


# ---------------------------------------------------------------- 4. liar catches


def test_sector_clear_lie_caught_from_call_board_without_witness():
    """A2 says sector_clear with I3 open 169 m away and no other vehicle near: contradicted."""
    world = build_fake_world()
    assert trust.check_claim(make_claim(world, "A2", "sector_clear"), world) == CONTRADICTED


def test_sector_clear_lie_caught_from_claimed_location():
    """F2 says sector_clear while placing itself at open I3: contradicted."""
    world = build_fake_world()
    claim = make_claim(world, "F2", "sector_clear", location=world.incidents["I3"].location)
    assert trust.check_claim(claim, world) == CONTRADICTED


@pytest.mark.parametrize("vid", ["A2", "A3", "F2", "P2"])
def test_resolved_lie_with_nobody_on_scene_is_contradicted(vid):
    """'I1 resolved' while I1 is open and no ambulance is within 100 m is contradicted (I1 has witnesses)."""
    world = build_fake_world()
    assert trust.check_claim(make_claim(world, vid, "resolved", "I1"), world) == CONTRADICTED


def test_resolved_lie_with_only_wrong_type_on_scene_is_contradicted():
    """Police 60 m from medical I1 do not make 'resolved' plausible: still contradicted."""
    world = build_fake_world()
    world.vehicles["P1"].location = north(world.incidents["I1"].location, 60)
    assert trust.check_claim(make_claim(world, "A2", "resolved", "I1"), world) == CONTRADICTED


def test_observation_claiming_resolved_is_contradicted():
    """An observation that open I1 is resolved/clear with nobody on scene is contradicted."""
    world = build_fake_world()
    for status in ("resolved", "clear"):
        claim = make_claim(world, "P1", "observation", "I1", location=world.incidents["I1"].location,
                           value={"status": status})
        assert trust.check_claim(claim, world) == CONTRADICTED, status


@pytest.mark.parametrize("ctype", ["available", "en_route", "observation", "capability"])
def test_understated_distance_is_contradicted(ctype):
    """F1 claiming distance_m 20 to I1 (388 m) or I2 (1462 m) is contradicted, whatever the claim type."""
    world = build_fake_world()
    if ctype == "capability":
        # I2 is a fire call F1 is equipped for, so only the distance can make this a lie (really 1462 m).
        claim = make_claim(world, "F1", ctype, "I2", value={"distance_m": 20, "type": "fire", "can_handle": True})
    else:
        claim = make_claim(world, "F1", ctype, "I1", value={"distance_m": 20})
    assert trust.check_claim(claim, world) == CONTRADICTED


def test_understated_distance_to_nearest_incident_is_contradicted():
    """F1 saying distance_m 20 with no incident named is checked against the nearest unresolved incident."""
    world = build_fake_world()
    assert trust.check_claim(make_claim(world, "F1", "available", value={"distance_m": 20}), world) == CONTRADICTED


def test_impossible_eta_is_contradicted():
    """A3 en_route to I1 (1560 m) with eta_seconds 10 would need 156 m/s: contradicted."""
    world = build_fake_world()
    claim = make_claim(world, "A3", "en_route", "I1", value={"eta_seconds": 10})
    assert trust.check_claim(claim, world) == CONTRADICTED


@pytest.mark.parametrize("ctype", ["available", "resolved"])
def test_location_1km_off_is_contradicted(ctype):
    """P1 reporting a position 1 km from where it really is gets contradicted."""
    world = build_fake_world()
    world.incidents["I1"].status = "resolved"
    claim = make_claim(world, "P1", ctype, "I1" if ctype == "resolved" else None,
                       location=north(world.vehicles["P1"].location, 1000))
    assert trust.check_claim(claim, world) == CONTRADICTED


def test_location_1km_off_contradicted_even_while_en_route():
    """1 km off exceeds the 300 + 450 m en_route tolerance: contradicted."""
    world = build_fake_world()
    p1 = world.vehicles["P1"]
    p1.status, p1.target_incident = "en_route", "I1"
    claim = make_claim(world, "P1", "available", location=north(p1.location, 1000))
    assert trust.check_claim(claim, world) == CONTRADICTED


@pytest.mark.parametrize("vid", ["F1", "F2"])
def test_capability_with_wrong_type_is_contradicted(vid):
    """A fire engine claiming to be an ambulance is contradicted, even with no witness (F2)."""
    world = build_fake_world()
    claim = make_claim(world, vid, "capability", "I1", value={"type": "ambulance", "can_handle": True})
    assert trust.check_claim(claim, world) == CONTRADICTED


@pytest.mark.parametrize("vid,iid,value", [
    ("A1", "I2", {"can_handle": True}),
    ("A2", "I4", None),
    ("F1", "I1", {"can_handle": True}),
    ("F2", "I5", {"type": "fire"}),
])
def test_hard_incapable_capability_claim_is_contradicted(vid, iid, value):
    """Ambulance on fire/hazard or fire engine on medical claiming it can handle it is contradicted."""
    world = build_fake_world()
    assert trust.check_claim(make_claim(world, vid, "capability", iid, value=value), world) == CONTRADICTED


@pytest.mark.parametrize("vid", ["A2", "A3", "P2"])
def test_on_scene_lie_far_away_is_contradicted(vid):
    """A vehicle 1.5 km from I1 claiming on_scene there is contradicted, even writing I1 as its location."""
    world = build_fake_world()
    i1 = world.incidents["I1"].location
    assert trust.check_claim(make_claim(world, vid, "on_scene", "I1"), world) == CONTRADICTED
    assert trust.check_claim(make_claim(world, vid, "on_scene", "I1", location=i1), world) == CONTRADICTED


# ---------------------------------------------------------------- 5. trust math


def test_trust_drops_in_exact_steps_and_turns_untrusted_below_0_4():
    """1.0 -> 0.7 -> 0.4 (trusted, weight 0.4) -> 0.1 (untrusted, weight 0) -> 0.0 -> 0.0, exact floats."""
    v = build_fake_world().vehicles["A3"]
    steps = []
    for _ in range(5):
        v = trust.update_trust(v, CONTRADICTED)
        steps.append((v.trust, v.untrusted, trust.vote_weight(v)))
    assert steps == [(0.7, False, 0.7), (0.4, False, 0.4), (0.1, True, 0.0), (0.0, True, 0.0), (0.0, True, 0.0)]


def test_trust_math_has_no_float_drift():
    """A trust of 0.1 + 0.2 + 0.4 drops to exactly 0.4, then exactly 0.1."""
    v = build_fake_world().vehicles["A1"].model_copy(update={"trust": 0.1 + 0.2 + 0.4})
    v = trust.update_trust(v, CONTRADICTED)
    assert v.trust == 0.4 and not v.untrusted
    v = trust.update_trust(v, CONTRADICTED)
    assert v.trust == 0.1 and v.untrusted


@pytest.mark.parametrize("result", [CONFIRMED, UNVERIFIABLE, "pass", "garbage", ""])
def test_only_contradicted_changes_trust(result):
    """Confirmed, unverifiable and unknown strings return an unchanged copy: no gain, no loss."""
    v = build_fake_world().vehicles["A1"].model_copy(update={"trust": 0.7})
    out = trust.update_trust(v, result)
    assert out is not v
    assert out.model_dump() == v.model_dump()


def test_untrusted_is_sticky():
    """Once untrusted, a vehicle stays untrusted whatever happens next."""
    v = build_fake_world().vehicles["A1"].model_copy(update={"trust": 0.9, "untrusted": True})
    assert trust.update_trust(v, CONFIRMED).untrusted
    after = trust.update_trust(v, CONTRADICTED)
    assert after.untrusted and after.trust == 0.6
    assert trust.vote_weight(after) == 0.0


def test_update_trust_returns_deep_copy_and_never_mutates_input():
    """update_trust leaves its input untouched and shares no nested objects with it."""
    v = build_fake_world().vehicles["A1"]
    before = v.model_dump()
    out = trust.update_trust(v, CONTRADICTED)
    assert v.model_dump() == before
    assert out is not v and out.location is not v.location
    unchanged = trust.update_trust(v, CONFIRMED)
    assert unchanged.location is not v.location


@pytest.mark.parametrize("t,untrusted,expected", [
    (1.0, False, 1.0), (0.7, False, 0.7), (0.4, False, 0.4), (1.5, False, 1.0),
    (-0.2, False, 0.0), (1.0, True, 0.0), (0.7, True, 0.0),
])
def test_vote_weight(t, untrusted, expected):
    """vote_weight is 0 for untrusted vehicles, else trust clamped to [0, 1]."""
    v = build_fake_world().vehicles["P1"].model_copy(update={"trust": t, "untrusted": untrusted})
    assert trust.vote_weight(v) == expected


def test_check_claim_never_mutates_claim_or_world():
    """check_claim leaves the claim and the world exactly as they were."""
    world = build_fake_world()
    world.vehicles["A1"].status, world.vehicles["A1"].target_incident = "en_route", "I1"
    claims = [
        make_claim(world, "A2", "resolved", "I1"),
        make_claim(world, "A3", "on_scene", "I1", location=world.incidents["I1"].location),
        make_claim(world, "F1", "en_route", "I1", value={"distance_m": 20, "eta_seconds": 1}),
        make_claim(world, "A2", "sector_clear"),
        make_claim(world, "F1", "capability", "I1", value={"type": "ambulance"}),
        make_claim(world, "P1", "observation", "I9"),
        make_claim(world, "A1", "available"),
    ]
    world_before = world.model_dump()
    for claim in claims:
        claim_before = claim.model_dump()
        trust.check_claim(claim, world)
        trust.witness(claim, world)
        assert claim.model_dump() == claim_before
    assert world.model_dump() == world_before


def test_should_verify_never_mutates_inputs():
    """should_verify leaves the claim and the vehicle untouched."""
    world = build_fake_world()
    claim, vehicle = make_claim(world, "A1", "available"), world.vehicles["A1"]
    cb, vb = claim.model_dump(), vehicle.model_dump()
    trust.should_verify(claim, vehicle, 1)
    assert claim.model_dump() == cb and vehicle.model_dump() == vb


# ---------------------------------------------------------------- 6. should_verify


def test_suspected_and_untrusted_senders_are_always_verified():
    """A sender with trust below 1.0, or marked untrusted, gets every claim checked."""
    world = build_fake_world()
    suspected = world.vehicles["A1"].model_copy(update={"trust": 0.7})
    untrusted = world.vehicles["A1"].model_copy(update={"untrusted": True})
    for round_no in range(1, 6):
        for ctype in ("available", "capability", "observation", "en_route"):
            for iid in (None, "I1", "I5"):
                claim = make_claim(world, "A1", ctype, iid)
                assert trust.should_verify(claim, suspected, round_no)
                assert trust.should_verify(claim, untrusted, round_no)


def test_high_stakes_claims_are_always_verified():
    """resolved, sector_clear and on_scene are verified for every sender, round and incident."""
    world = build_fake_world()
    for vid, v in world.vehicles.items():
        for round_no in range(1, 5):
            for ctype in ("resolved", "sector_clear", "on_scene"):
                for iid in (None, "I1", "I3"):
                    assert trust.should_verify(make_claim(world, vid, ctype, iid), v, round_no)


def test_numeric_payload_claims_are_always_verified():
    """Any claim carrying distance_m or eta_seconds is verified."""
    world = build_fake_world()
    for vid, v in world.vehicles.items():
        for round_no in range(1, 5):
            for value in ({"distance_m": 200}, {"eta_seconds": 60}):
                assert trust.should_verify(make_claim(world, vid, "available", value=value), v, round_no)
                assert trust.should_verify(make_claim(world, vid, "en_route", "I1", value=value), v, round_no)


def _sample_keys(world):
    """Yield (claim, vehicle, round_no) for many distinct low-stakes claim keys."""
    for vid, v in world.vehicles.items():
        for round_no in range(1, 5):
            for ctype in ("available", "capability", "observation", "en_route"):
                for iid in (None, "I1", "I2", "I3", "I4", "I5"):
                    yield make_claim(world, vid, ctype, iid), v, round_no


def test_sampling_is_deterministic_and_about_one_third():
    """Low-stakes claims from trusted senders are sampled deterministically at roughly 1 in 3."""
    world = build_fake_world()
    keys = list(_sample_keys(world))
    assert len(keys) >= 200
    first = [trust.should_verify(c, v, r) for c, v, r in keys]
    assert first == [trust.should_verify(c, v, r) for c, v, r in keys]
    assert first == [trust.should_verify(c, v, r) for c, v, r in keys]
    assert 0.2 <= sum(first) / len(first) <= 0.47


def test_sampling_matches_the_spec_hash():
    """Sampling equals sha256('911|vehicle|round|type|incident')[0] < 86, so replays match across processes."""
    world = build_fake_world()
    for claim, v, r in _sample_keys(world):
        key = f"911|{claim.vehicle}|{r}|{claim.type}|{claim.incident}".encode()
        assert trust.should_verify(claim, v, r) == (hashlib.sha256(key).digest()[0] < 86), key


def test_sampling_ignores_claim_id_tick_confidence_and_location():
    """Changing only id, tick, confidence, reasoning or location never changes the sampling decision."""
    world = build_fake_world()
    for claim, v, r in _sample_keys(world):
        base = trust.should_verify(claim, v, r)
        variant = claim.model_copy(update={
            "id": claim.id + "-other", "tick": claim.tick + 97, "confidence": 0.1,
            "reasoning": "different", "location": north(claim.location, 40),
        })
        assert trust.should_verify(variant, v, r) == base


# ---------------------------------------------------------------- 7. should_escalate

SPLIT = {"A1": 0, "F1": 0, "P1": 0, "A2": 1, "A3": 1, "F2": 1, "P2": 1}


def _bid(vid, eta):
    """Build a bid dict for I1."""
    return Bid(vehicle=vid, incident="I1", eta_seconds=eta, capability_match=1.0, cost_to_district=0.2,
               confidence=0.9, reasoning="r", round=2).model_dump()


def _vote(vid, idx):
    """Build a vote dict for I1."""
    return Vote(vehicle=vid, incident="I1", option_index=idx, reasoning="r", round=3).model_dump()


def protocol_state(**override):
    """The stuck-vote payload protocol.py sends: 3/4 split on I1 between A1 and A3."""
    state = {
        "incident": "I1", "severity": 5, "round": 4,
        "options": [_bid("A1", 46), _bid("A3", 160), _bid("P1", 64)],
        "tally": {"A1": 3.0, "A3": 4.0, "P1": 0.0},
        "honest_total": 7.0, "threshold": 4.2,
        "votes": [_vote(v, i) for v, i in SPLIT.items()],
        "contradicting_claims": [],
    }
    state.update(override)
    return state


def kartik_state(**override):
    """Kartik's described stuck-vote payload: 3/4 split on I1 between A1 and A3."""
    state = {
        "incident": "I1", "severity": 5, "round": 4, "max_rounds": 4,
        "options": [{"assign": "A1"}, {"assign": "A3"}],
        "votes": [{"vehicle": v, "option": i, "weight": 1.0} for v, i in SPLIT.items()],
        "honest_weight_total": 7.0,
        "claims_this_round": [],
    }
    state.update(override)
    return state


def test_escalation_protocol_shape():
    """Protocol payload: top two options by tally, choices with labels/backers/weights, votes passed through."""
    state = protocol_state()
    out = trust.should_escalate(state)
    assert out["incident"] == "I1" and out["severity"] == 5 and out["round"] == 4
    assert out["options"] == ["A3", "A1"]
    assert [(c["vehicle"], c["label"], c["backers"], c["weight"]) for c in out["choices"]] == [
        ("A3", "Send A3 (ETA 160 s)", ["A2", "A3", "F2", "P2"], 4.0),
        ("A1", "Send A1 (ETA 46 s)", ["A1", "F1", "P1"], 3.0),
    ]
    assert out["evidence"]["votes"] == state["votes"]
    assert out["evidence"]["contradicting_claims"] == []
    assert isinstance(out["reason"], str) and "I1" in out["reason"]
    assert re.fullmatch(r"esc-I1-[0-9a-f]{8}", out["id"])


def test_escalation_kartik_shape():
    """Kartik payload: tally built from vote weights, labels without ETA, claims_this_round as evidence."""
    claim = make_claim(build_fake_world(), "A2", "resolved", "I1").model_dump()
    weights = {"A2": 0.4}
    votes = [{"vehicle": v, "option": i, "weight": weights.get(v, 1.0)} for v, i in SPLIT.items()]
    state = kartik_state(votes=votes, honest_weight_total=6.4, claims_this_round=[claim])
    out = trust.should_escalate(state)
    assert out["options"] == ["A3", "A1"]
    choices = {c["vehicle"]: c for c in out["choices"]}
    assert choices["A3"]["label"] == "Send A3" and choices["A1"]["label"] == "Send A1"
    assert choices["A3"]["weight"] == pytest.approx(3.4) and choices["A1"]["weight"] == pytest.approx(3.0)
    assert choices["A3"]["backers"] == ["A2", "A3", "F2", "P2"]
    assert out["evidence"]["votes"] == votes
    assert out["evidence"]["contradicting_claims"] == [claim]
    assert re.fullmatch(r"esc-I1-[0-9a-f]{8}", out["id"])


def test_no_escalation_below_severity_4():
    """Severity 1-3 never escalates, in either payload shape."""
    for sev in (1, 2, 3):
        assert trust.should_escalate(protocol_state(severity=sev)) is None
        assert trust.should_escalate(kartik_state(severity=sev)) is None


def test_no_escalation_when_leader_has_consensus():
    """A leader above 60% of honest weight is consensus: no escalation."""
    assert trust.should_escalate(protocol_state(tally={"A1": 2.5, "A3": 4.5, "P1": 0.0})) is None
    votes = [{"vehicle": v, "option": 1 if v != "A1" and v != "F1" else 0, "weight": 1.0} for v in SPLIT]
    assert trust.should_escalate(kartik_state(votes=votes)) is None


def test_leader_exactly_at_threshold_still_escalates():
    """Consensus needs strictly more than the threshold: a leader exactly at it escalates."""
    state = protocol_state(tally={"A1": 2.0, "A3": 3.0, "P1": 0.0}, honest_total=5.0, threshold=3.0)
    assert trust.should_escalate(state) is not None


def test_severe_call_without_options_escalates():
    """A severity 4-5 call with no eligible bidder is exactly when a human must decide."""
    for state in (protocol_state(options=[], tally={}, votes=[]), kartik_state(options=[], votes=[])):
        out = trust.should_escalate(state)
        assert out is not None and out["options"] == [] and out["choices"] == []


def test_escalation_ties_keep_option_order():
    """Equal tallies keep the original option order."""
    out = trust.should_escalate(protocol_state(tally={"A1": 3.5, "A3": 3.5, "P1": 0.0}))
    assert out["options"] == ["A1", "A3"]


def test_escalation_id_is_deterministic_and_tracks_the_tally():
    """Same input gives the same id (vote order irrelevant); a different tally or vote gives a different id."""
    a = trust.should_escalate(protocol_state())
    b = trust.should_escalate(protocol_state())
    shuffled = trust.should_escalate(protocol_state(votes=list(reversed(protocol_state()["votes"]))))
    other_tally = trust.should_escalate(protocol_state(tally={"A1": 3.5, "A3": 3.5, "P1": 0.0}))
    votes = protocol_state()["votes"]
    votes[0] = _vote("A1", 2)
    other_votes = trust.should_escalate(protocol_state(votes=votes))
    assert a["id"] == b["id"] == shuffled["id"]
    assert other_tally["id"] != a["id"] and other_votes["id"] != a["id"]


def test_escalation_evidence_lines():
    """Evidence lines name source, checker and claim id; results come from checks, else contradicted/reported."""
    world = build_fake_world()
    lie_claim = make_claim(world, "A2", "resolved", "I1", id="A2-r1-c2").model_dump()
    obs = make_claim(world, "P1", "observation", "I1", id="P1-r2-c1").model_dump()
    unchecked = make_claim(world, "A3", "on_scene", "I1", id="A3-r1-c3").model_dump()
    checks = [{"claim_id": "A2-r1-c2", "verifier": "P1", "result": "contradicted", "vehicle": "A2",
               "incident": "I1", "round": 1}]
    out = trust.should_escalate(protocol_state(contradicting_claims=[lie_claim, obs, unchecked], checks=checks))
    lines = out["evidence"]["lines"]
    assert [(ln["source"], ln["checker"], ln["claim_id"], ln["result"]) for ln in lines] == [
        ("A2", "P1", "A2-r1-c2", "contradicted"),
        ("P1", None, "P1-r2-c1", "reported"),
        ("A3", None, "A3-r1-c3", "contradicted"),
    ]
    assert "A2" in lines[0]["claim"] and "resolved" in lines[0]["claim"]
    assert out["evidence"]["contradicting_claims"] == [lie_claim, obs, unchecked]


def test_should_escalate_never_mutates_input():
    """should_escalate leaves both payload shapes untouched."""
    for state in (protocol_state(), kartik_state()):
        before = copy.deepcopy(state)
        trust.should_escalate(state)
        assert state == before


def test_split_vote_through_protocol_escalates_with_real_trust():
    """Severity 5 with a 3/4 split goes to a human through the real trust module."""
    world = only(build_fake_world(), "I1")
    [plan] = run(world, ScriptedLLM(votes=SPLIT))
    assert plan.escalated and not plan.consensus and plan.assigned == []
    assert plan.evidence["options"] == [plan.options[1], plan.options[0]]
    assert len(plan.evidence["evidence"]["votes"]) == 7
    assert plan.evidence["id"].startswith("esc-I1-")


# ---------------------------------------------------------------- 8. metrics helpers


def test_track_incidents_records_created_and_served_once():
    """Served is set the first tick an acceptable vehicle on scene targets the incident, and never overwritten."""
    world = build_fake_world()
    times0 = trust.track_incidents({}, world)
    assert times0 == {iid: {"created": 0, "served": None} for iid in world.incidents}

    world.tick = 5
    a1 = world.vehicles["A1"]
    a1.location, a1.status, a1.target_incident = north(world.incidents["I1"].location, 40), "on_scene", "I1"
    snapshot = copy.deepcopy(times0)
    times5 = trust.track_incidents(times0, world)
    assert times0 == snapshot and times5 is not times0
    assert times5["I1"] == {"created": 0, "served": 5}

    world.tick = 9
    times9 = trust.track_incidents(times5, world)
    assert times9["I1"]["served"] == 5


def test_track_incidents_adds_new_incidents_at_current_tick():
    """An incident first seen at tick 7 gets created 7."""
    world = build_fake_world()
    times = trust.track_incidents({}, world)
    world.tick = 7
    new = world.incidents["I5"].model_copy(update={"id": "I6"})
    world.incidents["I6"] = new
    out = trust.track_incidents(times, world)
    assert out["I6"] == {"created": 7, "served": None}
    assert out["I1"]["created"] == 0


def test_track_incidents_requires_acceptable_trusted_committed_vehicle():
    """Nearby but uncommitted, wrong type, or untrusted vehicles do not serve; listing in assigned does."""
    world = build_fake_world()
    times = trust.track_incidents({}, world)
    world.tick = 3
    i1 = world.incidents["I1"].location
    world.vehicles["A1"].location = north(i1, 40)
    assert trust.track_incidents(times, world)["I1"]["served"] is None
    world.vehicles["P1"].location, world.vehicles["P1"].target_incident = north(i1, 30), "I1"
    assert trust.track_incidents(times, world)["I1"]["served"] is None
    world.vehicles["A1"].target_incident, world.vehicles["A1"].untrusted = "I1", True
    assert trust.track_incidents(times, world)["I1"]["served"] is None
    world.vehicles["A1"].untrusted, world.vehicles["A1"].target_incident = False, None
    world.incidents["I1"].assigned = ["A1"]
    assert trust.track_incidents(times, world)["I1"]["served"] == 3


def test_tick_record_classifies_open_served_and_false_handled():
    """Resolved with nobody there is false_handled (the baseline lie); physically served is served; rest open."""
    world = build_fake_world()
    world.tick = 12
    world.incidents["I1"].status = "resolved"
    world.incidents["I2"].status = "assigned"
    world.vehicles["F2"].status, world.vehicles["F2"].target_incident = "en_route", "I2"
    world.incidents["I4"].status = "assigned"
    world.vehicles["P1"].status, world.vehicles["P1"].target_incident = "en_route", "I4"
    world.incidents["I5"].status = "assigned"
    world.vehicles["A1"].status, world.vehicles["A1"].target_incident = "en_route", "I5"
    world.vehicles["A1"].untrusted, world.vehicles["A1"].trust = True, 0.1
    times = {iid: {"created": 0, "served": None} for iid in world.incidents}
    times["I3"]["served"] = 10
    world_before, times_before = world.model_dump(), copy.deepcopy(times)

    rec = trust.tick_record(world, times, "protocol", 4, True)
    assert rec["tick"] == 12 and rec["mode"] == "protocol"
    assert rec["incidents_served"] == ["I3"]
    assert rec["incidents_false_handled"] == ["I1", "I5"]
    assert rec["incidents_open"] == ["I2", "I4"]
    assert rec["checks"] == 4 and rec["escalation"] is True
    assert rec["trust"] == {vid: v.trust for vid, v in world.vehicles.items()}
    assert rec["untrusted"] == ["A1"]
    assert world.model_dump() == world_before and times == times_before


def test_tick_record_baseline_false_resolved_report():
    """Baseline believed 'I1 resolved' and sent nobody: I1 counts as falsely handled."""
    world = only(build_fake_world(), "I1")
    world.baseline = True
    times = trust.track_incidents({}, world)
    world.incidents["I1"].status = "resolved"
    world.tick = 1
    times = trust.track_incidents(times, world)
    rec = trust.tick_record(world, times, "baseline", 0, False)
    assert rec["incidents_false_handled"] == ["I1"] and rec["incidents_served"] == []
    assert rec["untrusted"] == []


def test_metrics_headline_numbers():
    """metrics sums tick records: served and false_handled from the last record, mean response, escalations."""
    def rec(tick, served, false_handled, checks, untrusted, escalation):
        return {"tick": tick, "mode": "protocol", "incidents_open": [], "incidents_served": served,
                "incidents_false_handled": false_handled, "checks": checks, "trust": {},
                "untrusted": untrusted, "escalation": escalation}

    records = [
        rec(0, [], [], 2, [], False),
        rec(1, ["I1"], ["I2"], 3, ["A3"], True),
        rec(2, ["I1", "I3"], ["I2"], 1, ["A3", "P2"], False),
    ]
    times = {"I1": {"created": 0, "served": 4}, "I2": {"created": 0, "served": None},
             "I3": {"created": 1, "served": 7}, "I4": {"created": 2, "served": None}}
    records_before, times_before = copy.deepcopy(records), copy.deepcopy(times)
    m = trust.metrics(records, times)
    assert m["incidents_served"] == 2
    assert m["incidents_false_handled"] == 1
    assert m["mean_response_ticks"] == 5.0
    assert m["unserved"] == 2
    assert m["checks_total"] == 6
    assert m["escalations"] == 1
    assert m["first_untrusted_tick"] == {"A3": 1, "P2": 2}
    assert records == records_before and times == times_before


def test_metrics_empty():
    """No records and no incidents give zeros, None and empty dicts."""
    m = trust.metrics([], {})
    assert m["incidents_served"] == 0 and m["incidents_false_handled"] == 0
    assert m["mean_response_ticks"] is None
    assert m["checks_total"] == 0 and m["escalations"] == 0
    assert m["first_untrusted_tick"] == {}


def test_metrics_mean_is_none_when_nothing_served():
    """Incidents exist but none served: mean_response_ticks is None."""
    times = {"I1": {"created": 0, "served": None}}
    rec = {"tick": 0, "mode": "baseline", "incidents_open": ["I1"], "incidents_served": [],
           "incidents_false_handled": [], "checks": 0, "trust": {}, "untrusted": [], "escalation": False}
    assert trust.metrics([rec], times)["mean_response_ticks"] is None


# ---------------------------------------------------------------- feed helpers


def test_feed_result_mapping():
    """confirmed -> pass, contradicted -> fail, anything else -> inconclusive."""
    assert trust.feed_result(CONFIRMED) == "pass"
    assert trust.feed_result(CONTRADICTED) == "fail"
    assert trust.feed_result(UNVERIFIABLE) == "inconclusive"
    assert trust.feed_result("whatever") == "inconclusive"


def test_check_line_text():
    """check_line renders 'P1 checked A2 resolved: FALSE', with 'nobody' for a missing checker."""
    claim = make_claim(build_fake_world(), "A2", "resolved", "I1")
    assert trust.check_line("P1", claim, CONTRADICTED) == "P1 checked A2 resolved: FALSE"
    assert trust.check_line("P1", claim, CONFIRMED) == "P1 checked A2 resolved: TRUE"
    assert trust.check_line(None, claim, UNVERIFIABLE) == "nobody checked A2 resolved: UNVERIFIED"


def test_check_event_shape():
    """check_event builds Matthew's feed event with a stable id and pass/fail result."""
    claim = make_claim(build_fake_world(), "A2", "resolved", "I1", id="A2-r1-c2", tick=33, round=1)
    ev = trust.check_event(claim, "P1", CONTRADICTED, 1.0, 0.7)
    assert ev == {"id": "chk-A2-r1-c2", "tick": 33, "round": 1, "checker": "P1", "target": "A2",
                  "claim": claim.model_dump(), "result": "fail", "trust_before": 1.0, "trust_after": 0.7}


def test_engine_check_shape():
    """engine_check builds Greg's check record with pass/fail/inconclusive and a short reason."""
    claim = make_claim(build_fake_world(), "A2", "resolved", "I1", id="A2-r1-c2")
    rec = trust.engine_check(claim, "P1", CONTRADICTED, 17)
    assert {k: rec[k] for k in ("sender", "claim_sequence", "claim_id", "verifier", "result")} == {
        "sender": "A2", "claim_sequence": 17, "claim_id": "A2-r1-c2", "verifier": "P1", "result": "fail"}
    assert isinstance(rec["reason"], str) and rec["reason"]
    assert trust.engine_check(claim, None, UNVERIFIABLE)["claim_sequence"] is None


def test_witness_prefers_police_and_skips_sender_and_untrusted():
    """The witness is a trusted non-sender in range, police first; None when nobody is in range."""
    world = build_fake_world()
    resolved = make_claim(world, "A2", "resolved", "I1")
    assert trust.witness(resolved, world) == "P1"
    world.vehicles["P1"].untrusted = True
    assert trust.witness(resolved, world) == "A1"
    world = build_fake_world()
    assert trust.witness(make_claim(world, "P1", "available"), world) == "F1"
    assert trust.witness(make_claim(world, "A2", "sector_clear"), world) is None
