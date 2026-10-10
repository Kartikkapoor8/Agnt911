"""Calls placed by outside callers, and what happens when one turns out to be false.

The fake_call preset creates an incident with source "operator" and the caller's source_id. Whether
anything is really there is ground truth the engine keeps to itself; vehicles only learn it when the
first police car arrives. If the call was fake, the incident is marked false_report and the caller
loses credibility.
"""

from .events import emit
from .models import Event, Incident, Location, World

START_CREDIBILITY = 1.0
CREDIBILITY_DROP = 0.3


def create_fake_call(world: World, preset: dict) -> Incident:
    """Add the incident a fake_call preset describes and register its caller; return the incident."""
    n = 1 + sum(1 for i in world.incidents.values() if i.source == "operator")
    incident = Incident(id=f"X{n}", type=preset["type"], severity=preset["severity"],
                        location=Location.model_validate(preset["location"]),
                        source="operator", source_id=preset["source_id"])
    world.incidents[incident.id] = incident
    world.callers.setdefault(incident.source_id, START_CREDIBILITY)
    return incident


def discredit(world: World, incident_id: str, verifier: str) -> Event:
    """Mark a call false after a police car found nothing there, and drop its caller's credibility by 0.3."""
    incident = world.incidents[incident_id]
    incident.false_report = True
    old = world.callers.get(incident.source_id, START_CREDIBILITY)
    new = max(0.0, round(old - CREDIBILITY_DROP, 2))
    world.callers[incident.source_id] = new
    return emit(world, "incident_discredited", vehicle=verifier, incident=incident_id,
                source_id=incident.source_id, verifier=verifier, credibility_old=old, credibility_new=new)
