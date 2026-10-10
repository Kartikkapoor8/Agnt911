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
