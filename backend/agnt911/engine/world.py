"""Physical simulation using the team's shared World, Vehicle, and Incident models."""

from copy import deepcopy
import json
import math
from pathlib import Path

from ..agents import haversine_m
from ..models import Incident, Location, Vehicle, World
from ..protocol import ROLES, required_roles

DEFAULT_SCENARIO = Path(__file__).parents[1] / "scenarios" / "demo.json"


def load_scenario(path: str | Path = DEFAULT_SCENARIO) -> dict:
    """Load and validate the existing demo without changing its event schedule."""
    scenario = json.loads(Path(path).read_text())
    vehicles = [Vehicle.model_validate(v) for v in scenario["vehicles"]]
    if len({v.id for v in vehicles}) != len(vehicles):
        raise ValueError("Duplicate scenario vehicle IDs")
    if type(scenario["seed"]) is not int:
        raise ValueError("Scenario seed must be an integer")
    Location.model_validate(scenario["map_center"])
    incident_ids = set()
    for event in sorted(scenario["events"], key=lambda e: e["tick"]):
        if type(event["tick"]) is not int or event["tick"] < 0:
            raise ValueError("Event ticks must be nonnegative integers")
        if event["type"] == "incident":
            incident = Incident.model_validate(event["incident"])
            if incident.id in incident_ids or incident.status != "open" or incident.assigned:
                raise ValueError("Scenario calls must be new and open")
            incident_ids.add(incident.id)
        elif event["type"] == "inject":
            if event["vehicle"] not in {v.id for v in vehicles}:
                raise ValueError("Injection names an unknown vehicle")
            validate_injection(event["text"])
        elif event["type"] == "escalate":
            if event["incident"] not in incident_ids:
                raise ValueError("Escalation precedes its incident")
            if type(event["severity"]) is not int or not 1 <= event["severity"] <= 5:
                raise ValueError("Severity must be 1–5")
        else:
            raise ValueError("Unknown scenario event")
    return deepcopy(scenario)


def scenario_world(scenario: dict) -> World:
    """Create the fleet; the engine introduces calls according to scenario events."""
    vehicles = [Vehicle.model_validate(v) for v in scenario["vehicles"]]
    return World(tick=0, vehicles={v.id: v for v in vehicles}, incidents={})


def validate_injection(text: str) -> None:
    """Require a nonblank judge instruction bounded to 2000 characters."""
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Injection text must contain 1–2000 characters")


def assign(world: World, assignments: dict[str, str | None]) -> None:
    """Validate all target changes before atomically updating vehicles and call rosters."""
    for vid, iid in assignments.items():
        if vid not in world.vehicles:
            raise ValueError(f"Unknown vehicle: {vid}")
        if iid is not None:
            if iid not in world.incidents or world.incidents[iid].status == "resolved":
                raise ValueError(f"Unknown or resolved incident: {iid}")
            incident = world.incidents[iid]
            allowed = ROLES[incident.type] if incident.severity <= 2 else required_roles(incident)
            if world.vehicles[vid].type not in allowed:
                raise ValueError(f"Vehicle {vid} cannot respond to {iid}")
    for vid, iid in assignments.items():
        vehicle = world.vehicles[vid]
        previous = vehicle.target_incident
        if previous == iid:
            continue
        if previous in world.incidents:
            old = world.incidents[previous]
            old.assigned = [v for v in old.assigned if v != vid]
            if not old.assigned and old.status != "resolved":
                old.status = "open"
        vehicle.target_incident = iid
        if iid is None:
            vehicle.status = "free"
        else:
            incident = world.incidents[iid]
            if vid not in incident.assigned:
                incident.assigned.append(vid)
            incident.status = "assigned"
            vehicle.status = "on_scene" if haversine_m(vehicle.location, incident.location) < 0.01 else "en_route"


def tick(world: World, dt_s: float, speed_mps: float = 12.0) -> list[str]:
    """Advance one tick and move vehicles straight toward targets, returning new arrivals."""
    if not math.isfinite(dt_s) or dt_s <= 0 or not math.isfinite(speed_mps) or speed_mps <= 0:
        raise ValueError("Time step and speed must be positive and finite")
    world.tick += 1
    arrivals = []
    for vehicle in world.vehicles.values():
        if vehicle.target_incident is None:
            continue
        incident = world.incidents[vehicle.target_incident]
        distance = haversine_m(vehicle.location, incident.location)
        fraction = min(1.0, speed_mps * dt_s / distance) if distance else 1.0
        vehicle.location = Location(
            lat=vehicle.location.lat + (incident.location.lat - vehicle.location.lat) * fraction,
            lng=vehicle.location.lng + (incident.location.lng - vehicle.location.lng) * fraction,
        )
        if fraction == 1:
            if vehicle.status != "on_scene":
                arrivals.append(vehicle.id)
            vehicle.status = "on_scene"
        else:
            vehicle.status = "en_route"
    return arrivals


def covered(world: World, incident: Incident, roles: list[str] | None = None) -> bool:
    """Check physical arrival of every required role, independently of agent reports."""
    present = {
        v.type for v in world.vehicles.values()
        if v.target_incident == incident.id and v.status == "on_scene"
        and haversine_m(v.location, incident.location) < 0.01
    }
    return set(roles or required_roles(incident)) <= present
