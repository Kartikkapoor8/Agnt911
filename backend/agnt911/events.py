"""Typed event stream for frontend animations. emit() appends one Event to world.events.

Protocol events are emitted into the snapshot a round runs on; the engine copies them into the
live world only when that round commits, so a discarded round leaves no trace in the stream.
"""

from .models import Event, EventType, World

STATE_EVENT_LIMIT = 500


def emit(world: World, type: EventType, *, vehicle: str | None = None, incident: str | None = None,
         **data) -> Event:
    """Record one event at the world's current tick and return it."""
    event = Event(tick=world.tick, type=type, vehicle=vehicle, incident=incident, data=data)
    world.events.append(event)
    return event


def check_events(events: list[Event], start_trust: dict[str, float]) -> list[dict]:
    """Replay claim_verified and trust_changed into one row per check: pass/fail and the target's trust before and after."""
    trust, rows = dict(start_trust), []
    for e in events:
        if e.type == "claim_verified":
            result = {"confirmed": "pass", "contradicted": "fail"}.get(e.data["result"], "inconclusive")
            rows.append({"id": f"chk-{e.data['claim_id']}", "tick": e.tick, "checker": e.data["verifier"],
                         "target": e.vehicle, "incident": e.incident, "subject": e.data.get("subject", "claim"),
                         "result": result, "trust_before": trust.get(e.vehicle, 1.0), "trust_after": trust.get(e.vehicle, 1.0)})
        elif e.type == "trust_changed":
            trust[e.vehicle] = e.data["new"]
            if rows and rows[-1]["target"] == e.vehicle:
                rows[-1]["trust_after"] = e.data["new"]
    return rows
