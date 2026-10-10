"""Every model round-trips through JSON."""

import pytest

from agnt911.fake_world import build_fake_world
from agnt911.models import (
    AgentOutput,
    Bid,
    Claim,
    Incident,
    Location,
    Plan,
    Vehicle,
    Vote,
    World,
)

LOC = Location(lat=37.3382, lng=-121.8863)
CLAIM = Claim(
    id="c1", tick=3, vehicle="A1", type="on_scene", incident="I1", location=LOC,
    value={"patients": 2}, confidence=0.9, reasoning="I am at the scene.", sig="abc",
)
BID = Bid(
    vehicle="A1", incident="I1", eta_seconds=120, capability_match=0.9,
    cost_to_district=0.2, confidence=0.8, reasoning="closest",
)

VOTE = Vote(vehicle="A1", incident="I1", option_index=0, reasoning="best plan")

ALL = [
    LOC,
    Vehicle(id="A1", type="ambulance", location=LOC, home_district="Downtown"),
    Incident(id="I1", type="medical", severity=5, location=LOC, assigned=["A1"]),
    CLAIM,
    BID,
    VOTE,
    Plan(incident="I1", assigned=["A1"], round_reached=2, consensus=True, escalated=False),
    AgentOutput(claims=[CLAIM], bids=[BID], vote=None),
    build_fake_world(),
    World(tick=1, vehicles={}, incidents={}, recent_messages=[CLAIM, BID, VOTE]),
]


@pytest.mark.parametrize("model", ALL, ids=lambda m: type(m).__name__)
def test_round_trip(model):
    """Dump to JSON and parse back gives an equal model."""
    assert type(model).model_validate_json(model.model_dump_json()) == model


def test_defaults():
    """Defaults match the spec."""
    v = Vehicle(id="P1", type="police", location=LOC, home_district="X")
    assert (v.status, v.target_incident, v.trust, v.untrusted) == ("free", None, 1.0, False)
    assert CLAIM.model_copy(update={"sig": ""}).sig == ""
    assert World(tick=0, vehicles={}, incidents={}).baseline is False


def test_constraints():
    """Out-of-range values are rejected."""
    with pytest.raises(ValueError):
        Incident(id="I", type="fire", severity=6, location=LOC)
    with pytest.raises(ValueError):
        Incident(id="I", type="fire", severity=0, location=LOC)
    with pytest.raises(ValueError):
        Bid(**{**BID.model_dump(), "confidence": 1.5})
    with pytest.raises(ValueError):
        Claim(**{**CLAIM.model_dump(), "type": "bogus"})
