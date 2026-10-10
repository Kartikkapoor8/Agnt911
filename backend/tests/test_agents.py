"""VehicleAgent behaviour with a mocked LLM function."""

from agnt911.agents import VIEW_RADIUS_M, VehicleAgent, haversine_m, load_system_prompt
from agnt911.fake_world import build_fake_world
from agnt911.models import AgentOutput, Bid, Claim, Location


def make_bid(vehicle="A1"):
    return Bid(vehicle=vehicle, incident="I1", eta_seconds=60, capability_match=1.0,
               cost_to_district=0.1, confidence=0.9, reasoning="closest")


def test_haversine_known_distance():
    """One degree of latitude is about 111 km."""
    d = haversine_m(Location(lat=37, lng=-122), Location(lat=38, lng=-122))
    assert 110_000 < d < 112_000


def test_view_filters_by_500m():
    """A1 sees only the vehicles and incidents within 500 m, never itself."""
    w = build_fake_world()
    a1 = VehicleAgent("A1")
    vehicles, incidents = a1.view(w)
    me = w.vehicles["A1"]
    assert all(haversine_m(me.location, v.location) <= VIEW_RADIUS_M for v in vehicles)
    assert all(haversine_m(me.location, i.location) <= VIEW_RADIUS_M for i in incidents)
    ids = {v.id for v in vehicles}
    assert "A1" not in ids
    assert {"F1", "P1"} <= ids          # downtown neighbours are close
    assert "F2" not in ids and "A2" not in ids  # Diridon is more than 500 m away
    assert "I1" in {i.id for i in incidents}
    assert "I4" not in {i.id for i in incidents}


def test_view_excludes_everything_far_away():
    """A vehicle moved far away sees nothing."""
    w = build_fake_world()
    w.vehicles["A1"].location = Location(lat=37.0, lng=-122.0)
    vehicles, incidents = VehicleAgent("A1").view(w)
    assert vehicles == [] and incidents == []


def test_system_prompt_uses_shared_plus_type():
    """The system prompt holds the shared skeleton and the type file for that vehicle."""
    w = build_fake_world()
    shared = load_system_prompt("ambulance")
    assert "Never claim to be somewhere you are not" in shared
    assert "fastest possible patient outcome" in shared
    assert "scene safety first" in VehicleAgent("P1").system_prompt(w)
    assert "containment" in VehicleAgent("F1").system_prompt(w)


def test_user_message_contents():
    """The user message has the filtered view, message log, and task, but not far-away objects."""
    w = build_fake_world()
    w.recent_messages.append(
        Claim(id="c1", tick=0, vehicle="P1", type="observation", incident="I1",
              location=w.vehicles["P1"].location, confidence=0.7, reasoning="saw smoke"))
    msg = VehicleAgent("A1").build_user_message(w, task="Do the thing.")
    assert '"id": "F1"' in msg and '"id": "I1"' in msg
    assert '"id": "F2"' not in msg and '"id": "I4"' not in msg
    assert "saw smoke" in msg and "distance_m" in msg and msg.endswith("Task: Do the thing.")


def test_act_returns_validated_output_via_mock():
    """act passes system, user, and AgentOutput to the LLM function and returns its result."""
    w = build_fake_world()
    calls = []

    def fake_llm(system, user, model):
        calls.append((system, user, model))
        return AgentOutput(bid=make_bid())

    out = VehicleAgent("A1", llm_fn=fake_llm).act(w)
    assert isinstance(out, AgentOutput) and out.bid.vehicle == "A1"
    system, user, model = calls[0]
    assert model is AgentOutput and "ambulance" in system and '"you"' in user
