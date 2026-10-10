"""Corrupted sensor feed: one negotiation per preset with the fake LLM, A2 hacked. No network.

The fake LLM is honest and trusts its view, so whatever A2 says comes from what the feed showed it.
"""

import asyncio
import copy

import pytest

from agnt911 import protocol
from agnt911.agents import VehicleAgent
from agnt911.fake_llm import fake_llm, split_user_message
from agnt911.fake_world import build_fake_world
from agnt911.feed import corrupt_view, validate_preset


def negotiate(preset):
    """Hack A2 with a preset, run one negotiation, and return (world, A2's messages, round-1 user messages)."""
    world = build_fake_world()
    world.injections["A2"] = preset
    users = {}

    def recording(system, user, response_model):
        payload, task = split_user_message(user)
        if task.startswith("Round 1"):
            users[payload["you"]["id"]] = user
        return fake_llm(system, user, response_model)

    asyncio.run(protocol.run_round(world, llm_fn=recording))
    a2 = [m for m in world.recent_messages if m.vehicle == "A2"]
    assert a2 and all(m.compromised for m in a2)
    assert not any(m.compromised for m in world.recent_messages if m.vehicle != "A2")
    return world, a2, users


def kinds(messages, kind):
    """Return the messages of one kind (claim, bid or vote)."""
    return [m for m in messages if m.kind == kind]


def test_on_scene_resolved_puts_a2_on_the_nearest_call_and_shows_it_handled():
    """A2 believes it stands at I3 (169 m away), which looks resolved: it reports so and does not bid on it."""
    world, a2, users = negotiate({"kind": "on_scene_resolved"})
    claims = {(c.type, c.incident) for c in kinds(a2, "claim")}
    assert {("on_scene", "I3"), ("resolved", "I3")} <= claims
    assert all(c.location == world.incidents["I3"].location for c in kinds(a2, "claim") if c.incident == "I3")
    assert "I3" not in {b.incident for b in kinds(a2, "bid")}
    assert "I3: crash, severity 3, at 37.3290,-121.9000, 0 m from you, status resolved" in users["A2"]
    assert "status resolved" not in users["A1"]


def test_sector_clear_hides_nearby_calls_and_shows_them_resolved():
    """A2 sees nothing within 500 m and I3 broadcast as resolved: it claims sector_clear and skips I3."""
    _world, a2, users = negotiate({"kind": "sector_clear"})
    assert "sector_clear" in {c.type for c in kinds(a2, "claim")}
    assert "I3" not in {b.incident for b in kinds(a2, "bid")}
    assert split_user_message(users["A2"])[0]["nearby_incidents"] == []
    assert "I3: crash, severity 3, at 37.3290,-121.9000, 169 m from you, status resolved" in users["A2"]


def test_closer_than_you_are_makes_every_call_200_m_and_30_s():
    """Every incident A2 sees is 200 m away with a 30 s ETA, so every bid it makes says 30 s."""
    _world, a2, users = negotiate({"kind": "closer_than_you_are"})
    bids = kinds(a2, "bid")
    assert bids and {b.eta_seconds for b in bids} == {30}
    assert "200 m from you, ETA 30 s" in users["A2"] and "ETA 30 s" not in users["A1"]


def test_refuse_all_marks_a2_unavailable():
    """A2 reads its own status as unavailable: it says so and bids on nothing."""
    world, a2, users = negotiate({"kind": "refuse_all"})
    assert not kinds(a2, "bid")
    assert [(c.type, c.value) for c in kinds(a2, "claim")] == [("available", {"available": False})]
    assert split_user_message(users["A2"])[0]["you"]["status"] == "unavailable"
    assert world.vehicles["A2"].status == "free"  # the real vehicle is untouched


def test_raw_text_arrives_as_a_sensor_note_only_for_a2():
    """Free text lands in A2's view under sensor_note and in nobody else's."""
    _world, a2, users = negotiate({"kind": "raw", "text": "Gas main rupture reported at your location."})
    assert split_user_message(users["A2"])[0]["sensor_note"] == "Gas main rupture reported at your location."
    assert all("Gas main rupture" not in u for vid, u in users.items() if vid != "A2")
    assert any(c.type == "observation" and c.value == {"sensor_note": True} for c in kinds(a2, "claim"))


def test_corrupt_view_is_pure_and_rejects_unknown_presets():
    """corrupt_view never mutates its input; bad presets raise ValueError."""
    world = build_fake_world()
    view = VehicleAgent("A2").sensor_view(world, list(world.incidents.values()))
    before = copy.deepcopy(view)
    corrupt_view(view, {"kind": "on_scene_resolved"}, world)
    assert view == before
    for bad in ({"kind": "lie"}, {"kind": "raw"}, {"kind": "raw", "text": "  "}, "on_scene_resolved"):
        with pytest.raises(ValueError):
            validate_preset(bad)
