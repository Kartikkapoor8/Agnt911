"""JSON-compatible ground truth. Claims never mutate physical world state."""

from copy import deepcopy
import math

SENSOR_RADIUS_M = 500.0


def distance_m(a: dict, b: dict) -> float:
    """Great-circle distance for lat/lng points, in metres."""
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    dlat = lat2 - lat1
    dlng = math.radians(b["lng"] - a["lng"])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 6_371_000 * 2 * math.asin(math.sqrt(min(1.0, h)))


def create_world() -> dict:
    """Repeatable initial scenario near downtown San Jose."""
    fleet = [
        ("ambulance-1", "ambulance", 37.3382, -121.8863, "central"),
        ("ambulance-2", "ambulance", 37.3430, -121.8910, "north"),
        ("ambulance-3", "ambulance", 37.3300, -121.8800, "south"),
        ("fire-1", "fire", 37.3360, -121.8890, "central"),
        ("fire-2", "fire", 37.3450, -121.8800, "north"),
        ("police-1", "police", 37.3370, -121.8840, "central"),
        ("police-2", "police", 37.3320, -121.8920, "south"),
    ]
    calls = [
        ("crash-1", "crash", 37.3390, -121.8850, ["ambulance", "police"], 3),
        ("fire-call-1", "fire", 37.3420, -121.8880, ["fire"], 3),
        ("medical-1", "medical", 37.3310, -121.8810, ["ambulance"], 2),
        ("security-1", "security", 37.3340, -121.8900, ["police"], 1),
        ("fire-call-2", "fire", 37.3440, -121.8790, ["fire"], 2),
    ]
    return {
        "tick": 0,
        "time_s": 0.0,
        "vehicles": {
            vid: {"id": vid, "type": kind, "lat": lat, "lng": lng,
                  "district": district, "speed_mps": 15.0, "status": "idle",
                  "target_incident_id": None, "trust": 1.0, "hacked": False}
            for vid, kind, lat, lng, district in fleet
        },
        "incidents": {
            iid: {"id": iid, "type": kind, "lat": lat, "lng": lng,
                  "required_types": types, "priority": priority, "status": "open",
                  "created_at_s": 0.0, "covered_at_s": None, "resolved_at_s": None}
            for iid, kind, lat, lng, types, priority in calls
        },
    }


def assign(world: dict, assignments: dict[str, str | None]) -> None:
    """Apply an already committed plan atomically; the engine does not pick it."""
    for vid, iid in assignments.items():
        if vid not in world["vehicles"]:
            raise ValueError(f"Unknown vehicle: {vid}")
        if iid is not None:
            incident = world["incidents"].get(iid)
            if incident is None:
                raise ValueError(f"Unknown incident: {iid}")
            if incident["status"] == "resolved":
                raise ValueError(f"Incident {iid} is resolved")
            if world["vehicles"][vid]["type"] not in incident["required_types"]:
                raise ValueError(f"Vehicle {vid} cannot cover {iid}")
    for vid, iid in assignments.items():
        vehicle = world["vehicles"][vid]
        if vehicle["target_incident_id"] == iid:
            continue
        vehicle["target_incident_id"] = iid
        vehicle["status"] = ("on_scene" if distance_m(vehicle, world["incidents"][iid]) < 0.001
                             else "en_route") if iid else "idle"
    refresh_coverage(world)


def refresh_coverage(world: dict) -> None:
    for incident in world["incidents"].values():
        if incident["status"] == "resolved":
            continue
        present = {
            v["type"] for v in world["vehicles"].values()
            if v["target_incident_id"] == incident["id"] and v["status"] == "on_scene"
        }
        covered = set(incident["required_types"]) <= present
        incident["status"] = "covered" if covered else "open"
        if covered and incident["covered_at_s"] is None:
            incident["covered_at_s"] = world["time_s"]


def tick(world: dict, dt_s: float) -> None:
    if not math.isfinite(dt_s) or dt_s <= 0:
        raise ValueError("Tick duration must be finite and positive")
    world["time_s"] += dt_s
    world["tick"] += 1
    for vehicle in world["vehicles"].values():
        iid = vehicle["target_incident_id"]
        if iid is None:
            continue
        target = world["incidents"][iid]
        remaining = distance_m(vehicle, target)
        travel = vehicle["speed_mps"] * dt_s
        fraction = min(1.0, travel / remaining) if remaining else 1.0
        for coordinate in ("lat", "lng"):
            vehicle[coordinate] += (target[coordinate] - vehicle[coordinate]) * fraction
        vehicle["status"] = "on_scene" if fraction == 1.0 else "en_route"
    refresh_coverage(world)


def add_incident(world: dict, incident: dict) -> None:
    required = {"id", "type", "lat", "lng", "required_types", "priority"}
    if set(incident) != required:
        raise ValueError("New incident requires id, type, lat, lng, required_types, priority")
    if any(not isinstance(incident[k], str) or not incident[k].strip() for k in ("id", "type")):
        raise ValueError("Incident id and type must be nonempty strings")
    if incident["id"] in world["incidents"]:
        raise ValueError("Incident already exists")
    for key, bound in (("lat", 90), ("lng", 180)):
        value = incident[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not -bound <= value <= bound:
            raise ValueError("Invalid coordinates")
    types = incident["required_types"]
    if not isinstance(types, list) or not types or any(t not in ("ambulance", "fire", "police") for t in types):
        raise ValueError("Invalid required vehicle types")
    if type(incident["priority"]) is not int or incident["priority"] not in (1, 2, 3):
        raise ValueError("Priority must be 1, 2, or 3")
    world["incidents"][incident["id"]] = {
        **deepcopy(incident), "status": "open", "created_at_s": world["time_s"],
        "covered_at_s": None, "resolved_at_s": None,
    }


def observe(world: dict, vehicle_id: str) -> dict:
    """Calls are shared; physical observations are limited to the 500 m sensor."""
    own = world["vehicles"][vehicle_id]
    visible_vehicles = {
        vid: {key: v[key] for key in ("id", "type", "lat", "lng", "status")}
        for vid, v in world["vehicles"].items() if distance_m(own, v) <= SENSOR_RADIUS_M
    }
    visible_incidents = {
        iid: deepcopy(i) for iid, i in world["incidents"].items()
        if distance_m(own, i) <= SENSOR_RADIUS_M
    }
    # Reported calls do not leak remotely observed coverage or arrival facts.
    calls = {
        iid: {key: i[key] for key in ("id", "type", "lat", "lng", "required_types", "priority", "created_at_s")}
        for iid, i in world["incidents"].items()
    }
    self_state = {k: v for k, v in own.items() if k not in ("hacked", "trust")}
    return deepcopy({"time_s": world["time_s"], "self": self_state,
                     "calls": calls, "sensors": {"radius_m": SENSOR_RADIUS_M,
                     "vehicles": visible_vehicles, "incidents": visible_incidents}})
