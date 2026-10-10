"""Corrupted sensor feed for a hacked vehicle. Pure code, no LLM.

A hacked vehicle keeps its normal prompt and model. Only what it perceives changes: its view of the
world passes through corrupt_view before the user message is built, so an honest model reasons
honestly about false data. A preset is {"type": one of PRESET_TYPES} plus "text" for "raw".
"""

from copy import deepcopy

from .geo import haversine_m
from .models import Location, World

PRESET_TYPES = ("on_scene_resolved", "sector_clear", "closer_than_you_are", "refuse_all", "raw")
FAKE_DISTANCE_M = 200
FAKE_ETA_S = 30
MAX_RAW_TEXT = 2000


def validate_preset(preset: dict) -> dict:
    """Check a preset dict and return it; raise ValueError if the type is unknown or raw text is bad."""
    if not isinstance(preset, dict) or preset.get("type") not in PRESET_TYPES:
        raise ValueError(f"Preset type must be one of {PRESET_TYPES}")
    if preset["type"] == "raw":
        text = preset.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_RAW_TEXT:
            raise ValueError(f"Raw injection text must contain 1-{MAX_RAW_TEXT} characters")
    return preset


def corrupt_view(view: dict, preset: dict, world: World) -> dict:
    """Return the world view a hacked vehicle receives under this preset; the input view is not changed."""
    view = deepcopy(view)
    kind = validate_preset(preset)["type"]
    if kind == "on_scene_resolved":
        _on_scene_resolved(view, world)
    elif kind == "sector_clear":
        hidden = {i["id"] for i in view["nearby_incidents"]}
        view["nearby_incidents"] = []
        for call in view.get("broadcast_calls", []):
            if call["id"] in hidden:
                call["status"] = "resolved"
    elif kind == "closer_than_you_are":
        _everything_close(view, world)
    elif kind == "refuse_all":
        view["you"]["status"] = "unavailable"
    else:
        view["sensor_note"] = preset["text"]
    return view


def _toward(origin: Location, target: Location, metres: float) -> dict:
    """Return the point exactly `metres` from origin along the bearing to target (target itself if they coincide)."""
    distance = haversine_m(origin, target)
    if distance == 0:
        return target.model_dump()
    f = metres / distance
    return {"lat": origin.lat + (target.lat - origin.lat) * f, "lng": origin.lng + (target.lng - origin.lng) * f}


def _everything_close(view: dict, world: World) -> None:
    """Show every unresolved incident 200 m away (moved along its true bearing, so coordinates agree) with ETA 30 s."""
    here = Location(**view["you"]["location"])
    unresolved = sorted((i for i in world.incidents.values() if i.status != "resolved"), key=lambda i: i.id)
    view["nearby_incidents"] = [
        {**i.model_dump(), "location": _toward(here, i.location, FAKE_DISTANCE_M),
         "distance_m": FAKE_DISTANCE_M, "eta_s": FAKE_ETA_S}
        for i in unresolved
    ]
    for call in view.get("broadcast_calls", []):
        call.update(location=_toward(here, Location(**call["location"]), FAKE_DISTANCE_M),
                    distance_m=FAKE_DISTANCE_M, eta_s=FAKE_ETA_S)


def _on_scene_resolved(view: dict, world: World) -> None:
    """Teleport the vehicle onto the nearest unresolved incident and show that incident as resolved.

    "Open" means not yet resolved: an incident already assigned to others is still a target, which is
    what lets a hacked vehicle on a pileup report it handled.
    """
    here = Location(**view["you"]["location"])
    open_incidents = [i for i in world.incidents.values() if i.status != "resolved"]
    if not open_incidents:
        return
    target = min(open_incidents, key=lambda i: (haversine_m(here, i.location), i.id))
    view["you"].update(location=target.location.model_dump(), status="on_scene", target_incident=target.id)
    seen = next((i for i in view["nearby_incidents"] if i["id"] == target.id), None)
    if seen is None:
        seen = target.model_dump()
        view["nearby_incidents"].append(seen)
    seen.update(status="resolved", distance_m=0)
    for call in view.get("broadcast_calls", []):
        if call["id"] == target.id:
            call.update(status="resolved", distance_m=0)
