"""Deterministic fake world: 7 vehicles and 5 incidents near downtown San Jose."""

from .models import Incident, Location, Vehicle, World


def _loc(lat: float, lng: float) -> Location:
    """Build a Location from lat and lng."""
    return Location(lat=lat, lng=lng)


def build_fake_world() -> World:
    """Return the same World every call: 3 ambulances, 2 fire engines, 2 police cars, 5 incidents, tick 0."""
    vehicles = [
        Vehicle(id="A1", type="ambulance", location=_loc(37.3352, -121.8881), home_district="Downtown"),
        Vehicle(id="A2", type="ambulance", location=_loc(37.3297, -121.9017), home_district="Diridon"),
        Vehicle(id="A3", type="ambulance", location=_loc(37.3480, -121.8940), home_district="Japantown"),
        Vehicle(id="F1", type="fire", location=_loc(37.3376, -121.8895), home_district="Downtown"),
        Vehicle(id="F2", type="fire", location=_loc(37.3305, -121.9120), home_district="Diridon"),
        Vehicle(id="P1", type="police", location=_loc(37.3367, -121.8903), home_district="Downtown"),
        Vehicle(id="P2", type="police", location=_loc(37.3490, -121.8920), home_district="Japantown"),
    ]
    incidents = [
        Incident(id="I1", type="medical", severity=5, location=_loc(37.3355, -121.8860)),
        Incident(id="I2", type="fire", severity=3, location=_loc(37.3500, -121.8950)),
        Incident(id="I3", type="crash", severity=3, location=_loc(37.3290, -121.9000)),
        Incident(id="I4", type="hazard", severity=2, location=_loc(37.3250, -121.8780)),
        Incident(id="I5", type="medical", severity=2, location=_loc(37.3420, -121.8850)),
    ]
    return World(
        tick=0,
        vehicles={v.id: v for v in vehicles},
        incidents={i.id: i for i in incidents},
    )
