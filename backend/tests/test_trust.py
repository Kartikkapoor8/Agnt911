"""Trust engine on the fake world: claim checks, witnesses, sampling, trust, votes, escalation, feed helpers."""

import copy
import hashlib
import re

from agnt911.fake_world import build_fake_world
from agnt911.models import Bid, Claim, Location, Vote
from agnt911.trust import (
    CONFIRMED,
    CONTRADICTED,
    SAMPLE_CUTOFF,
    UNVERIFIABLE,
    check_claim,
    check_event,
    check_line,
    engine_check,
    feed_result,
    metrics,
    should_escalate,
    should_verify,
    tick_record,
    track_incidents,
    update_trust,
    vote_weight,
    witness,
)

METRES_PER_DEGREE_LAT = 6_371_000.0 * 3.141592653589793 / 180


def make_claim(world, vehicle, claim_type, incident=None, **overrides):
    """Build a claim sent from where the vehicle really is, unless a test overrides that."""
    fields = {
        "id": f"{vehicle}-r1-c1", "tick": world.tick, "vehicle": vehicle, "type": claim_type,
        "incident": incident, "location": world.vehicles[vehicle].location,
        "confidence": 0.9, "reasoning": "test", "round": 1,
    }
    fields.update(overrides)
    return Claim(**fields)


def north(location, metres):
    """Return the point the given number of metres due north of a location."""
    return Location(lat=location.lat + metres / METRES_PER_DEGREE_LAT, lng=location.lng)


def dispatch(world, vehicle, incident, status="en_route"):
    """Send a vehicle to an incident in place."""
    world.vehicles[vehicle].status = status
    world.vehicles[vehicle].target_incident = incident


def park_at(world, vehicle, incident):
    """Move a vehicle onto an incident and mark it on scene there."""
    world.vehicles[vehicle].location = world.incidents[incident].location
    dispatch(world, vehicle, incident, "on_scene")


# on_scene

def test_on_scene_confirmed_within_100_m_or_parked_at_the_incident():
    """A1 is confirmed standing on I1, and also when the world has it on scene there 189 m out."""
    world = build_fake_world()
    world.vehicles["A1"].location = world.incidents["I1"].location
    assert check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == CONFIRMED
    world = build_fake_world()
    dispatch(world, "A1", "I1", "on_scene")
    assert check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == CONFIRMED


def test_on_scene_contradicted_far_away_even_when_the_destination_is_the_claim_location():
    """A2 is 1531 m from I1: a lie from its real spot and a lie when it writes I1's spot as its location."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A2", "on_scene", "I1"), world) == CONTRADICTED
    at_scene = make_claim(world, "A2", "on_scene", "I1", location=world.incidents["I1"].location)
    assert check_claim(at_scene, world) == CONTRADICTED


def test_on_scene_unverifiable_in_the_grey_zone_or_for_a_closed_or_unknown_incident():
    """189 m away is neither on scene nor a gross lie; resolved or unknown incidents are not judged."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "A2", "on_scene", "I9"), world) == UNVERIFIABLE
    world.incidents["I1"].status = "resolved"
    assert check_claim(make_claim(world, "A2", "on_scene", "I1"), world) == UNVERIFIABLE


# en_route

def test_en_route_confirmed_when_dispatched_and_never_contradicted():
    """En route is confirmed from the world's dispatch; a not-yet-applied plan is only unverifiable."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A1", "en_route", "I1"), world) == UNVERIFIABLE
    dispatch(world, "A1", "I1")
    assert check_claim(make_claim(world, "A1", "en_route", "I1"), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "en_route", "I5"), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "A1", "en_route", "I9"), world) == UNVERIFIABLE


def test_en_route_with_the_destination_as_location_is_not_a_position_lie():
    """A3 is about 1.5 km from I1; writing I1's spot is fine on en_route but a lie on available."""
    world = build_fake_world()
    dispatch(world, "A3", "I1")
    spot = world.incidents["I1"].location
    assert check_claim(make_claim(world, "A3", "en_route", "I1", location=spot), world) == CONFIRMED
    assert check_claim(make_claim(world, "A3", "available", location=spot), world) == CONTRADICTED


# resolved

def test_resolved_contradicted_while_open_with_nobody_suitable_on_scene():
    """The demo lie: A2 says I1 is handled, but no ambulance is within 100 m of it."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A2", "resolved", "I1"), world) == CONTRADICTED
    world.vehicles["F1"].location = world.incidents["I1"].location
    assert check_claim(make_claim(world, "A2", "resolved", "I1"), world) == CONTRADICTED


def test_resolved_confirmed_when_resolved_and_unverifiable_with_an_ambulance_on_scene():
    """Any suitable vehicle on scene, even an untrusted one, makes the claim plausible."""
    world = build_fake_world()
    world.vehicles["A1"].location = world.incidents["I1"].location
    world.vehicles["A1"].untrusted = True
    assert check_claim(make_claim(world, "A2", "resolved", "I1"), world) == UNVERIFIABLE
    world.incidents["I1"].status = "resolved"
    assert check_claim(make_claim(world, "A2", "resolved", "I1"), world) == CONFIRMED
    assert check_claim(make_claim(world, "A2", "resolved", "I9"), world) == UNVERIFIABLE


def test_a_vehicle_the_engine_parks_on_scene_counts_as_at_the_scene_everywhere():
    """The engine has A1 on scene at I1 189 m out: its on_scene, resolved and served all agree."""
    world = build_fake_world()
    dispatch(world, "A1", "I1", "on_scene")
    world.incidents["I1"].assigned = ["A1"]
    assert check_claim(make_claim(world, "A1", "on_scene", "I1"), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "resolved", "I1"), world) == UNVERIFIABLE
    spot = world.incidents["I1"].location
    handled = make_claim(world, "P1", "observation", "I1", location=spot, value={"status": "resolved"})
    assert check_claim(handled, world) == CONFIRMED
    assert track_incidents({}, world)["I1"]["served"] == world.tick


# sector_clear

def test_sector_clear_contradicted_by_the_call_board_without_any_witness():
    """A2 and F2 have nobody within 500 m, yet an open call near them or near their claimed spot disproves it."""
    world = build_fake_world()
    assert witness(make_claim(world, "A2", "sector_clear"), world) is None
    assert check_claim(make_claim(world, "A2", "sector_clear"), world) == CONTRADICTED
    far_spot = make_claim(world, "F2", "sector_clear", location=world.incidents["I4"].location)
    assert witness(far_spot, world) is None
    assert check_claim(far_spot, world) == CONTRADICTED
    assert check_claim(make_claim(world, "F2", "sector_clear"), world) == UNVERIFIABLE


def test_sector_clear_is_measured_from_the_real_position_so_rounding_costs_nothing():
    """F2 sits 520 m from I3, out of its view; a claimed spot rounded 120 m toward I3 is not a lie."""
    world = build_fake_world()
    world.vehicles["F2"].location = north(world.incidents["I3"].location, -520)
    rounded = north(world.vehicles["F2"].location, 120)
    assert check_claim(make_claim(world, "F2", "sector_clear", location=rounded), world) == UNVERIFIABLE


def test_sector_clear_confirmed_or_unverifiable_with_a_witness():
    """A1 is 189 m from I1: clear once I1 is resolved, unverifiable while I1 is assigned but not open."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A1", "sector_clear"), world) == CONTRADICTED
    world.incidents["I1"].status = "assigned"
    assert check_claim(make_claim(world, "A1", "sector_clear"), world) == UNVERIFIABLE
    world.incidents["I1"].status = "resolved"
    assert check_claim(make_claim(world, "A1", "sector_clear"), world) == CONFIRMED


# available

def test_available_confirmed_when_free_and_never_contradicted_after_dispatch():
    """The honest fake LLM says available even after dispatch, so that must not cost trust."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A1", "available"), world) == CONFIRMED
    dispatch(world, "A1", "I1")
    assert check_claim(make_claim(world, "A1", "available"), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "F2", "available"), world) == UNVERIFIABLE


# capability

def test_capability_lies_are_contradicted_without_a_witness():
    """F2 and A2 are alone, but the roster shows F2 is no ambulance and A2 carries no fire gear."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "F2", "capability", value={"type": "ambulance"}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "A2", "capability", "I2", value={"can_handle": True}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "A2", "capability", "I4"), world) == CONTRADICTED
    assert check_claim(make_claim(world, "F1", "capability", "I1"), world) == CONTRADICTED


def test_capability_confirmed_unverifiable_and_police_never_contradicted():
    """Suitable types are confirmed; refusing a call is policy; police are never capability liars."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A1", "capability", "I1", value={"type": "ambulance"}), world) == CONFIRMED
    assert check_claim(make_claim(world, "A2", "capability", "I2", value={"can_handle": False}), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "P1", "capability", "I1", value={"can_handle": True}), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "P1", "capability", "I4"), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "capability"), world) == UNVERIFIABLE


def test_capability_type_must_name_another_vehicle_type_to_be_a_lie():
    """Writing the call's type, or the own type capitalised, in "type" is honest; claiming another vehicle is not."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "A1", "capability", "I1", value={"type": "medical"}), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "capability", "I1", value={"type": "Ambulance"}), world) == CONFIRMED
    refusal = make_claim(world, "A3", "capability", "I2", value={"type": "fire", "can_handle": False})
    assert check_claim(refusal, world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "P1", "capability", "I2", value={"type": "fire"}), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "A2", "capability", value={"type": "fire"}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "F2", "capability", "I1", value={"type": "ambulance"}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "A2", "capability", "I2", value={"type": "fire"}), world) == CONTRADICTED


# observation

def test_observation_of_a_named_incident():
    """Confirmed at the incident's spot, unverifiable elsewhere, contradicted when it falsely says it is handled."""
    world = build_fake_world()
    spot = world.incidents["I1"].location
    assert check_claim(make_claim(world, "A1", "observation", "I1", location=spot), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "observation", "I1"), world) == UNVERIFIABLE
    for status in ("resolved", "clear"):
        lie = make_claim(world, "A1", "observation", "I1", location=spot, value={"status": status})
        assert check_claim(lie, world) == CONTRADICTED
    wrong_severity = make_claim(world, "A1", "observation", "I1", location=spot, value={"severity": 1})
    assert check_claim(wrong_severity, world) == CONFIRMED


def test_observation_without_a_known_incident_matches_any_incident_nearby():
    """A new-incident report is confirmed only if some real incident is within 150 m of it."""
    world = build_fake_world()
    near_i2 = north(world.incidents["I2"].location, 100)
    assert check_claim(make_claim(world, "A3", "observation", location=near_i2), world) == CONFIRMED
    assert check_claim(make_claim(world, "A3", "observation", "I9", location=near_i2), world) == CONFIRMED
    assert check_claim(make_claim(world, "A3", "observation"), world) == UNVERIFIABLE


# numbers and position

def test_understated_distance_is_contradicted_and_overstating_is_not():
    """P1 is 403 m from the nearest open call: "200 m" is a lie, "250 m" and "5000 m" are not."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "P1", "available", value={"distance_m": 200}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "P1", "available", value={"distance_m": 250}), world) == CONFIRMED
    assert check_claim(make_claim(world, "P1", "available", value={"distance_m": 5000}), world) == CONFIRMED
    assert check_claim(make_claim(world, "P1", "available", value={"distance_m": True}), world) == CONFIRMED


def test_distance_uses_the_named_incident_and_needs_a_witness():
    """F1 is 388 m from I1 but 1462 m from I2; F2 has no witness, so its lie is unverifiable."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "F1", "en_route", "I2", value={"distance_m": 300}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "F1", "en_route", "I1", value={"distance_m": 300}), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "F2", "available", value={"distance_m": 1}), world) == UNVERIFIABLE


def test_a_new_incident_observation_measures_distance_to_what_it_saw():
    """P1 reports smoke 50 m away while the nearest call on the board is 403 m off: not a distance lie."""
    world = build_fake_world()
    smoke = make_claim(world, "P1", "observation", location=north(world.vehicles["P1"].location, 50),
                       value={"what": "smoke", "distance_m": 50})
    assert check_claim(smoke, world) == UNVERIFIABLE
    named = make_claim(world, "F1", "observation", "I2", value={"distance_m": 300})
    assert check_claim(named, world) == CONTRADICTED


def test_impossible_eta_is_contradicted():
    """F1 is 388 m from I1: 2 s at 30 m/s is impossible, 10 s is plausible, and no incident means no check."""
    world = build_fake_world()
    assert check_claim(make_claim(world, "F1", "en_route", "I1", value={"eta_seconds": 2}), world) == CONTRADICTED
    assert check_claim(make_claim(world, "F1", "en_route", "I1", value={"eta_seconds": 10}), world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "F1", "available", value={"eta_seconds": 0}), world) == CONFIRMED


def test_position_tolerance_is_300_m_plus_450_m_while_en_route():
    """A claim sent from the wrong place is a lie past 300 m, or past 750 m for a moving vehicle."""
    world = build_fake_world()
    here = world.vehicles["A1"].location
    assert check_claim(make_claim(world, "A1", "available", location=north(here, 250)), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "available", location=north(here, 350)), world) == CONTRADICTED
    dispatch(world, "A1", "I1")
    assert check_claim(make_claim(world, "A1", "en_route", "I1", location=north(here, 700)), world) == CONFIRMED
    assert check_claim(make_claim(world, "A1", "en_route", "I1", location=north(here, 800)), world) == CONTRADICTED


# witnesses

def test_witness_prefers_police_then_the_nearest_and_never_the_sender():
    """For A2's I1 claim P1 (403 m) beats A1 (189 m); for P1's own claim the nearest other vehicle is F1."""
    world = build_fake_world()
    claim = make_claim(world, "A2", "on_scene", "I1")
    assert witness(claim, world) == "P1"
    world.vehicles["P1"].untrusted = True
    assert witness(claim, world) == "A1"
    assert witness(make_claim(world, "P1", "available"), world) == "F1"


def test_no_trusted_witness_means_unverifiable():
    """With A1, F1 and P1 untrusted nobody trusted can see I1, so A2's lie is not judged."""
    world = build_fake_world()
    for vehicle in ("A1", "F1", "P1"):
        world.vehicles[vehicle].untrusted = True
    claim = make_claim(world, "A2", "on_scene", "I1")
    assert witness(claim, world) is None
    assert check_claim(claim, world) == UNVERIFIABLE
    assert check_claim(make_claim(world, "A2", "resolved", "I3"), world) == UNVERIFIABLE


def test_unknown_sender_is_unverifiable():
    """A claim from a vehicle not in the world cannot be judged."""
    world = build_fake_world()
    ghost = make_claim(world, "A1", "resolved", "I1").model_copy(update={"vehicle": "X9"})
    assert check_claim(ghost, world) == UNVERIFIABLE


# should_verify

def test_suspects_high_stakes_and_numbers_are_always_checked():
    """Untrusted or lowered-trust senders, resolved/sector_clear/on_scene, and numeric payloads: every round."""
    world = build_fake_world()
    sender = world.vehicles["A1"]
    plain = make_claim(world, "A1", "available")
    always = [
        make_claim(world, "A1", "resolved", "I1"),
        make_claim(world, "A1", "sector_clear"),
        make_claim(world, "A1", "on_scene", "I1"),
        make_claim(world, "A1", "available", value={"distance_m": 9}),
        make_claim(world, "A1", "en_route", "I1", value={"eta_seconds": 60}),
        make_claim(world, "A1", "observation", "I1", value={"status": "resolved"}),
        make_claim(world, "A1", "observation", "I1", value={"status": "clear"}),
    ]
    for claim in always:
        assert all(should_verify(claim, sender, n) for n in range(40))
    lowered = sender.model_copy(update={"trust": 0.7})
    untrusted = sender.model_copy(update={"untrusted": True})
    assert all(should_verify(plain, lowered, n) and should_verify(plain, untrusted, n) for n in range(40))


def test_ordinary_claims_are_sampled_about_one_in_three_by_a_fixed_hash():
    """The sample is the sha256 rule, repeats exactly, and ignores claim id, tick and confidence."""
    world = build_fake_world()
    sender = world.vehicles["A1"]
    claim = make_claim(world, "A1", "available")
    picks = [should_verify(claim, sender, n) for n in range(300)]
    assert picks == [should_verify(claim, sender, n) for n in range(300)]
    assert 0.25 < sum(picks) / len(picks) < 0.42
    expected = [hashlib.sha256(f"911|A1|{n}|available|None".encode()).digest()[0] < SAMPLE_CUTOFF for n in range(300)]
    assert picks == expected
    relabelled = claim.model_copy(update={"id": "other", "tick": 77, "confidence": 0.1})
    assert picks == [should_verify(relabelled, sender, n) for n in range(300)]


# update_trust and vote_weight

def test_trust_falls_by_0_3_and_only_0_1_is_untrusted():
    """1.0, 0.7, 0.4 (still trusted), 0.1 (untrusted), then 0.0; confirmed and unverifiable change nothing."""
    vehicle = build_fake_world().vehicles["A2"]
    seen = []
    for _ in range(4):
        vehicle = update_trust(vehicle, CONTRADICTED)
        seen.append((vehicle.trust, vehicle.untrusted))
    assert seen == [(0.7, False), (0.4, False), (0.1, True), (0.0, True)]
    for result in (CONFIRMED, UNVERIFIABLE, "pass", ""):
        assert update_trust(vehicle, result) == vehicle


def test_untrusted_is_sticky_and_inputs_are_not_mutated():
    """Nothing earns a vehicle back; the caller's vehicle is left alone and a deep copy comes back."""
    original = build_fake_world().vehicles["A2"].model_copy(update={"untrusted": True})
    confirmed = update_trust(original, CONFIRMED)
    lowered = update_trust(original, CONTRADICTED)
    assert confirmed.untrusted and lowered.untrusted and lowered.trust == 0.7
    assert original.trust == 1.0 and original.untrusted
    assert confirmed is not original and confirmed.location is not original.location


def test_vote_weight_is_trust_clamped_and_zero_when_untrusted():
    """A full-trust vote is 1.0, a lowered one counts less, and an untrusted vote counts nothing."""
    vehicle = build_fake_world().vehicles["A1"]
    assert vote_weight(vehicle) == 1.0
    assert vote_weight(vehicle.model_copy(update={"trust": 0.7})) == 0.7
    assert vote_weight(vehicle.model_copy(update={"trust": 1.5})) == 1.0
    assert vote_weight(vehicle.model_copy(update={"trust": -0.2})) == 0.0
    assert vote_weight(vehicle.model_copy(update={"untrusted": True})) == 0.0


# should_escalate

def bid(vehicle, eta):
    """Build a protocol-style bid on I1."""
    return Bid(vehicle=vehicle, incident="I1", eta_seconds=eta, capability_match=1.0,
               cost_to_district=0.2, confidence=0.9, reasoning="test").model_dump()


def protocol_state(tally=None, severity=5):
    """Build the stuck-vote payload protocol.py sends: A1 3.0 against A3 4.0 of 7.0 honest weight."""
    world = build_fake_world()
    split = {"A1": 0, "F1": 0, "P1": 0, "A2": 1, "A3": 1, "F2": 1, "P2": 1}
    lie = make_claim(world, "A2", "resolved", "I1", id="A2-r1-c2")
    seen = make_claim(world, "P1", "observation", "I1", id="P1-r2-c1")
    return {
        "incident": "I1", "severity": severity, "round": 4,
        "options": [bid("A1", 46), bid("A3", 160), bid("F1", 62)],
        "tally": tally or {"A1": 3.0, "A3": 4.0, "F1": 0.0},
        "honest_total": 7.0, "threshold": 0.6 * 7.0,
        "votes": [Vote(vehicle=v, incident="I1", option_index=i, reasoning="test").model_dump()
                  for v, i in split.items()],
        "contradicting_claims": [lie.model_dump(), seen.model_dump()],
        "checks": [{"claim_id": "A2-r1-c2", "verifier": "P1", "result": CONTRADICTED}],
    }


def test_split_severe_vote_escalates_with_the_top_two_options_and_evidence():
    """A3 leads 4.0 of 7.0, below the 4.2 bar: the human sees A3 and A1, their backers, and the evidence."""
    state = protocol_state()
    before = copy.deepcopy(state)
    decision = should_escalate(state)
    assert state == before
    assert re.fullmatch(r"esc-I1-[0-9a-f]{8}", decision["id"])
    assert (decision["incident"], decision["severity"], decision["round"]) == ("I1", 5, 4)
    assert decision["reason"] == "I1 (severity 5): A3 leads with 4.0 of 7.0 honest weight; consensus needs more than 4.2"
    assert decision["options"] == ["A3", "A1"]
    assert decision["choices"] == [
        {"vehicle": "A3", "label": "Send A3 (ETA 160 s)", "backers": ["A2", "A3", "F2", "P2"], "weight": 4.0},
        {"vehicle": "A1", "label": "Send A1 (ETA 46 s)", "backers": ["A1", "F1", "P1"], "weight": 3.0},
    ]
    evidence = decision["evidence"]
    assert evidence["votes"] == state["votes"] and evidence["contradicting_claims"] == state["contradicting_claims"]
    assert evidence["lines"] == [
        {"source": "A2", "checker": "P1", "claim_id": "A2-r1-c2", "claim": "A2 resolved I1", "result": CONTRADICTED},
        {"source": "P1", "checker": None, "claim_id": "P1-r2-c1", "claim": "P1 observation I1", "result": "reported"},
    ]


def test_escalation_id_is_deterministic():
    """The same stuck vote always gets the same id; a different tally gets a different one."""
    first = should_escalate(protocol_state())["id"]
    assert should_escalate(protocol_state())["id"] == first
    assert should_escalate(protocol_state({"A1": 2.0, "A3": 4.0, "F1": 0.0}))["id"] != first


def test_no_escalation_on_consensus_or_low_severity():
    """A leader above 60% of honest weight or a severity below 4 means no human."""
    assert should_escalate(protocol_state({"A1": 2.0, "A3": 5.0, "F1": 0.0})) is None
    assert should_escalate(protocol_state(severity=3)) is None


def test_severe_call_with_no_eligible_bidder_goes_to_a_human():
    """A severity 4-5 role nobody can fill escalates with no options and says why, naming the role."""
    out = should_escalate({**protocol_state(), "role": "ambulance", "options": [], "tally": {}, "votes": []})
    assert out["options"] == [] and out["choices"] == []
    assert "no eligible vehicle bid" in out["reason"] and "ambulance" in out["reason"]
    assert out["id"].startswith(f"esc-{out['incident']}-")
    assert should_escalate({**protocol_state(severity=3), "options": [], "tally": {}, "votes": []}) is None


def test_kartiks_payload_shape_builds_its_own_tally():
    """Options as {"assign": id} and votes carrying weights: A1 1.7 of 4.2 honest weight is no consensus."""
    world = build_fake_world()
    lie = make_claim(world, "A2", "resolved", "I5").model_dump()
    state = {
        "incident": "I5", "severity": 4, "round": 4, "max_rounds": 4,
        "options": [{"assign": "A1"}, {"assign": "A3"}],
        "votes": [{"vehicle": "A1", "option": 0, "weight": 1.0}, {"vehicle": "P1", "option": 0, "weight": 0.7},
                  {"vehicle": "A3", "option": 1, "weight": 1.0}, {"vehicle": "A2", "option": 1, "weight": 0.0}],
        "honest_weight_total": 4.2,
        "claims_this_round": [lie],
    }
    decision = should_escalate(state)
    assert decision["options"] == ["A1", "A3"]
    assert decision["reason"] == "I5 (severity 4): A1 leads with 1.7 of 4.2 honest weight; consensus needs more than 2.5"
    assert decision["choices"][1] == {"vehicle": "A3", "label": "Send A3", "backers": ["A2", "A3"], "weight": 1.0}
    assert decision["evidence"]["lines"] == [
        {"source": "A2", "checker": None, "claim_id": lie["id"], "claim": "A2 resolved I5", "result": CONTRADICTED}]
    assert should_escalate({**state, "honest_weight_total": 2.0}) is None


# feed and engine helpers

def test_feed_words_and_check_lines():
    """Results map to pass/fail/inconclusive and to TRUE/FALSE/UNVERIFIED feed lines."""
    world = build_fake_world()
    claim = make_claim(world, "A2", "resolved", "I1")
    assert [feed_result(r) for r in (CONFIRMED, CONTRADICTED, UNVERIFIABLE, "x")] == [
        "pass", "fail", "inconclusive", "inconclusive"]
    assert check_line("P1", claim, CONTRADICTED) == "P1 checked A2 resolved: FALSE"
    assert check_line("P1", claim, CONFIRMED) == "P1 checked A2 resolved: TRUE"
    assert check_line(None, claim, UNVERIFIABLE) == "nobody checked A2 resolved: UNVERIFIED"


def test_check_event_and_engine_check_records():
    """The frontend event and the engine record carry the claim, checker, and feed result."""
    world = build_fake_world()
    world.tick = 12
    claim = make_claim(world, "A2", "resolved", "I1", round=2)
    assert check_event(claim, "P1", CONTRADICTED, 1.0, 0.7) == {
        "id": "chk-A2-r1-c1", "tick": 12, "round": 2, "checker": "P1", "target": "A2",
        "claim": claim.model_dump(), "result": "fail", "trust_before": 1.0, "trust_after": 0.7,
    }
    assert engine_check(claim, "P1", CONTRADICTED, 5) == {
        "sender": "A2", "claim_sequence": 5, "claim_id": "A2-r1-c1", "verifier": "P1",
        "result": "fail", "reason": "P1 checked A2 resolved: FALSE",
    }
    assert engine_check(claim, None, UNVERIFIABLE)["claim_sequence"] is None


def test_track_incidents_records_creation_and_first_real_arrival():
    """Served means a trusted, suitable vehicle sent to the call is at it; the first served tick sticks."""
    world = build_fake_world()
    world.tick = 3
    times = {}
    times = track_incidents(times, world)
    assert times == {iid: {"created": 3, "served": None} for iid in world.incidents}

    world.tick = 7
    park_at(world, "F1", "I1")
    park_at(world, "A3", "I2")
    world.vehicles["A1"].location = world.incidents["I1"].location
    world.vehicles["A1"].untrusted = True
    dispatch(world, "A1", "I1", "on_scene")
    world.vehicles["P2"].location = world.incidents["I5"].location
    before = copy.deepcopy(times)
    after = track_incidents(times, world)
    assert times == before
    assert after["I1"]["served"] is None and after["I2"]["served"] is None and after["I5"]["served"] is None

    park_at(world, "A2", "I3")
    world.vehicles["A3"].location = world.incidents["I5"].location
    world.vehicles["A3"].target_incident = None
    world.incidents["I5"].assigned = ["A3"]
    after = track_incidents(after, world)
    assert after["I3"] == {"created": 3, "served": 7} and after["I5"]["served"] == 7
    world.tick = 9
    world.incidents["I6"] = world.incidents["I4"].model_copy(update={"id": "I6"})
    after = track_incidents(after, world)
    assert after["I3"]["served"] == 7 and after["I6"] == {"created": 9, "served": None}


def test_tick_record_splits_incidents_into_served_false_handled_and_open():
    """Closed without a trusted responder on the way is falsely handled; everything unserved else is open."""
    world = build_fake_world()
    world.tick = 8
    world.incidents["I2"].status = "assigned"
    world.incidents["I3"].status = "assigned"
    dispatch(world, "A2", "I3")
    world.incidents["I4"].status = "resolved"
    world.incidents["I5"].status = "assigned"
    dispatch(world, "A3", "I5")
    world.vehicles["A3"].untrusted = True
    world.vehicles["A3"].trust = 0.1
    times = {iid: {"created": 0, "served": 6 if iid == "I1" else None} for iid in world.incidents}
    record = tick_record(world, times, "protocol", 4, True)
    assert record == {
        "tick": 8, "mode": "protocol",
        "incidents_open": ["I3"],
        "incidents_served": ["I1"],
        "incidents_false_handled": ["I2", "I4", "I5"],
        "checks": 4,
        "trust": {"A1": 1.0, "A2": 1.0, "A3": 0.1, "F1": 1.0, "F2": 1.0, "P1": 1.0, "P2": 1.0},
        "untrusted": ["A3"],
        "escalation": True,
    }


def test_metrics_sum_the_run():
    """Served and false-handled come from the last tick; response time, checks, escalations and catches from all."""
    times = {"I1": {"created": 0, "served": 4}, "I2": {"created": 2, "served": 8}, "I3": {"created": 1, "served": None}}
    base = {"mode": "protocol", "incidents_open": [], "trust": {}}
    records = [
        {**base, "tick": 1, "incidents_served": [], "incidents_false_handled": [], "checks": 3,
         "untrusted": [], "escalation": False},
        {**base, "tick": 2, "incidents_served": ["I1"], "incidents_false_handled": [], "checks": 2,
         "untrusted": ["A2"], "escalation": True},
        {**base, "tick": 3, "incidents_served": ["I1", "I2"], "incidents_false_handled": ["I3"], "checks": 1,
         "untrusted": ["A2", "F2"], "escalation": False},
    ]
    assert metrics(records, times) == {
        "incidents_served": 2, "incidents_false_handled": 1, "mean_response_ticks": 5.0, "unserved": 1,
        "checks_total": 6, "escalations": 1, "first_untrusted_tick": {"A2": 2, "F2": 3},
    }
    assert metrics([], {}) == {
        "incidents_served": 0, "incidents_false_handled": 0, "mean_response_ticks": None, "unserved": 0,
        "checks_total": 0, "escalations": 0, "first_untrusted_tick": {},
    }


def test_checks_never_mutate_the_world_or_the_claim():
    """Every check and helper reads its inputs and leaves them exactly as they were."""
    world = build_fake_world()
    dispatch(world, "A1", "I1")
    claims = [make_claim(world, "A2", t, "I1", value={"distance_m": 5, "eta_seconds": 1, "status": "resolved"})
              for t in ("on_scene", "en_route", "resolved", "sector_clear", "available", "capability", "observation")]
    world_before, claims_before = world.model_dump(), [c.model_dump() for c in claims]
    for claim in claims:
        check_claim(claim, world)
        witness(claim, world)
    times = track_incidents({}, world)
    tick_record(world, times, "protocol", 0, False)
    assert world.model_dump() == world_before
    assert [c.model_dump() for c in claims] == claims_before


# --- observations about other vehicles; resolved needs every role; lenient status (chris-next items 1-3) ---

LIVE_P1_T120 = Location(lat=37.33385, lng=-121.89318)  # P1's real position at t120, run f2823f3c (scenario 5b12021)
LIVE_I5 = Location(lat=37.33025, lng=-121.88245)         # I5 in that run


def live_t120_world():
    """P1 parked where it really was at t120, I5 where it really was, A2 driving to I5 and 240 m short of it."""
    world = build_fake_world()
    world.incidents = {"I5": world.incidents["I5"].model_copy(update={"location": LIVE_I5, "status": "assigned",
                                                                      "assigned": ["A2"]})}
    world.vehicles["P1"].location = LIVE_P1_T120
    world.vehicles["A2"].location = north(LIVE_I5, 240)
    world.vehicles["A2"].status, world.vehicles["A2"].target_incident = "en_route", "I5"
    return world


def test_observation_about_another_vehicle_is_checked_against_that_vehicle():
    """The exact live claim: P1 reports A2 240 m out on I5. P1 is about 1 km from I5, A2 really is 240 m: confirmed."""
    world = live_t120_world()
    claim = make_claim(world, "P1", "observation", "I5", id="d7-P1-r2-c1", round=2,
                       value={"vehicle": "A2", "status_seen": "en_route", "distance_m": 240, "target": "I5"},
                       reasoning="A2 is within my 500 m sensor range at about 240 m, showing en_route with target I5.")
    assert 900 < _metres(world.vehicles["P1"].location, LIVE_I5) < 1100
    assert check_claim(claim, world) == CONFIRMED


def test_observation_naming_another_vehicle_in_reasoning_only_is_about_that_vehicle():
    """No vehicle key, but the reasoning names one other vehicle: its position is the one checked."""
    world = live_t120_world()
    claim = make_claim(world, "P1", "observation", "I5", value={"distance_m": 240},
                       reasoning="A2 is about 240 m from I5.")
    assert check_claim(claim, world) == CONFIRMED


def test_observation_with_an_ambiguous_subject_is_unverifiable_not_contradicted():
    """Two other vehicles named and an understated distance: nobody is blamed."""
    world = live_t120_world()
    claim = make_claim(world, "P1", "observation", "I5", value={"distance_m": 50},
                       reasoning="A2 and A3 are both heading for I5.")
    assert check_claim(claim, world) == UNVERIFIABLE


def test_bid_observation_naming_the_sender_is_still_checked_against_the_sender():
    """The protocol's bid-as-observation names the bidder itself: an understated distance or ETA is a lie."""
    world = live_t120_world()
    world.vehicles["A3"].location = north(LIVE_P1_T120, 300)  # about 1.2 km from I5, P1 300 m away as witness
    assert _metres(world.vehicles["A3"].location, LIVE_I5) > 1100  # a 30 s ETA is impossible even at 30 m/s
    for value in ({"vehicle": "A3", "distance_m": 200}, {"vehicle": "A3", "eta_seconds": 30}):
        claim = make_claim(world, "A3", "observation", "I5", value=value, reasoning="A2 is closer, but I can go.")
        assert check_claim(claim, world) == CONTRADICTED, value


def severe_crash_with_ambulance_only():
    """I3 as a severity 5 crash, A2 parked on it, P1 300 m away (a witness, but not on scene)."""
    world = build_fake_world()
    world.incidents = {"I3": world.incidents["I3"].model_copy(update={"severity": 5, "status": "assigned",
                                                                      "assigned": ["A2", "P1"]})}
    crash = world.incidents["I3"].location
    world.vehicles["A2"].location = crash
    world.vehicles["A2"].status, world.vehicles["A2"].target_incident = "on_scene", "I3"
    world.vehicles["P1"].location = north(crash, 300)
    world.vehicles["P1"].status, world.vehicles["P1"].target_incident = "en_route", "I3"
    return world


def test_resolved_at_severe_crash_with_only_the_ambulance_is_contradicted():
    """A crash needs ambulance and police on scene to be handled; the ambulance alone cannot call it resolved."""
    world = severe_crash_with_ambulance_only()
    assert check_claim(make_claim(world, "A2", "resolved", "I3"), world) == CONTRADICTED
    said = make_claim(world, "A2", "observation", "I3", value={"status": "resolved"})
    assert check_claim(said, world) == CONTRADICTED
    world.vehicles["P1"].location = world.incidents["I3"].location  # police arrives: now plausible
    assert check_claim(make_claim(world, "A2", "resolved", "I3"), world) == UNVERIFIABLE


def test_resolved_read_from_any_status_key():
    """The exact live value A2 sent ({"I3_status_in_my_state": "resolved"}) is always verified and, here, contradicted."""
    world = severe_crash_with_ambulance_only()
    claim = make_claim(world, "A2", "observation", "I3", value={"I3_status_in_my_state": "Resolved"})
    assert should_verify(claim, world.vehicles["A2"], 1)
    assert check_claim(claim, world) == CONTRADICTED


def _metres(a, b):
    """Great-circle distance in metres, for test geometry checks."""
    from agnt911.geo import haversine_m
    return haversine_m(a, b)


def test_vehicle_mentioned_in_passing_does_not_turn_own_numbers_into_a_lie():
    """Live e2e false positives (run 46da8ee0): A2's own distance to I5, with another vehicle named in the reasoning."""
    world = build_fake_world()
    i5 = Location(lat=37.33205, lng=-121.89318)
    world.incidents = {"I5": world.incidents["I5"].model_copy(update={"location": i5, "status": "assigned", "assigned": ["A2"]})}
    world.vehicles["P1"].location = Location(lat=37.33385, lng=-121.89318)  # parked at I3, 200 m north of I5
    world.vehicles["A3"].location = north(i5, 1256)
    world.vehicles["A2"].location = Location(lat=37.33385, lng=-121.89318)  # t100: 200 m from I5
    t100 = make_claim(world, "A2", "observation", "I5", value={"status": "open", "distance_m": 200},
                      reasoning="I5 is open, 200 m from me. A3's bid is at about 1256 m, so it is farther.")
    assert check_claim(t100, world) != CONTRADICTED
    world.vehicles["A2"].location = i5  # t120: on scene at I5
    t120 = make_claim(world, "A2", "observation", "I5", value={"status": "assigned", "distance_m": 0},
                      reasoning="I5 is at 0 m, assigned to A2. P1 is the only other vehicle I can see.")
    assert check_claim(t120, world) != CONTRADICTED
