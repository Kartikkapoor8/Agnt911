"""The fake world is deterministic and well formed."""

from agnt911.fake_world import build_fake_world
from agnt911.models import World


def test_counts():
    """Seven vehicles, five incidents, tick zero."""
    w = build_fake_world()
    assert isinstance(w, World)
    assert len(w.vehicles) == 7
    assert len(w.incidents) == 5
    assert w.tick == 0
    types = [v.type for v in w.vehicles.values()]
    assert (types.count("ambulance"), types.count("fire"), types.count("police")) == (3, 2, 2)


def test_ids_match_keys():
    """Dict keys equal the object ids."""
    w = build_fake_world()
    assert all(k == v.id for k, v in w.vehicles.items())
    assert all(k == i.id for k, i in w.incidents.items())


def test_coords_near_downtown_san_jose():
    """Every location is a valid lat/lng within about 5 km of downtown San Jose."""
    w = build_fake_world()
    for obj in [*w.vehicles.values(), *w.incidents.values()]:
        assert -90 <= obj.location.lat <= 90 and -180 <= obj.location.lng <= 180
        assert 37.30 <= obj.location.lat <= 37.37
        assert -121.93 <= obj.location.lng <= -121.86


def test_deterministic_and_dumpable():
    """Two builds are equal and model_dump gives a dict that validates as a World."""
    assert build_fake_world() == build_fake_world()
    assert World.model_validate(build_fake_world().model_dump()) == build_fake_world()
