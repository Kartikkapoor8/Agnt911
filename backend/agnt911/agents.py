"""VehicleAgent: builds a vehicle's prompt from its 500 m view of the world and asks Claude for its move."""

import json
import math
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from . import llm
from .models import AgentOutput, Incident, Location, Vehicle, World

PROMPT_DIR = Path(__file__).parent / "prompts"
VIEW_RADIUS_M = 500.0
EARTH_RADIUS_M = 6_371_000.0
DEFAULT_TASK = "Report any claims you should make, and bid on or vote for the incident you are best suited for."

LLMFn = Callable[[str, str, type[BaseModel]], BaseModel]


def haversine_m(a: Location, b: Location) -> float:
    """Return the great-circle distance in metres between two locations."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def load_system_prompt(vehicle_type: str) -> str:
    """Join the shared prompt skeleton with the prompt file for this vehicle type."""
    shared = (PROMPT_DIR / "shared.md").read_text()
    specific = (PROMPT_DIR / f"{vehicle_type}.md").read_text()
    return f"{shared.strip()}\n\n{specific.strip()}\n"


class VehicleAgent:
    """One vehicle's brain: sees a filtered view of the world and returns an AgentOutput."""

    def __init__(self, vehicle_id: str, llm_fn: LLMFn | None = None):
        """Create an agent for the given vehicle id, optionally with a replacement LLM function."""
        self.vehicle_id = vehicle_id
        self._llm_fn = llm_fn

    def system_prompt(self, world: World) -> str:
        """Return the system prompt (shared skeleton plus type file) for this vehicle."""
        return load_system_prompt(world.vehicles[self.vehicle_id].type)

    def view(self, world: World) -> tuple[list[Vehicle], list[Incident]]:
        """Return the other vehicles and the incidents within 500 m of this vehicle."""
        me = world.vehicles[self.vehicle_id]
        vehicles = [
            v
            for v in world.vehicles.values()
            if v.id != me.id and haversine_m(me.location, v.location) <= VIEW_RADIUS_M
        ]
        incidents = [
            i
            for i in world.incidents.values()
            if haversine_m(me.location, i.location) <= VIEW_RADIUS_M
        ]
        return vehicles, incidents

    def build_user_message(self, world: World, task: str = DEFAULT_TASK) -> str:
        """Build the user message: own state, 500 m view with distances, message log, and the task."""
        me = world.vehicles[self.vehicle_id]
        vehicles, incidents = self.view(world)
        payload = {
            "tick": world.tick,
            "you": me.model_dump(),
            "nearby_vehicles": [
                {**v.model_dump(), "distance_m": round(haversine_m(me.location, v.location))}
                for v in vehicles
            ],
            "nearby_incidents": [
                {**i.model_dump(), "distance_m": round(haversine_m(me.location, i.location))}
                for i in incidents
            ],
            "shared_message_log": [m.model_dump() for m in world.recent_messages],
        }
        return f"{json.dumps(payload, indent=2)}\n\nTask: {task}"

    def act(self, world: World, task: str = DEFAULT_TASK) -> AgentOutput:
        """Ask Claude what this vehicle does this round and return the validated AgentOutput."""
        call = self._llm_fn or llm.call_claude
        return call(self.system_prompt(world), self.build_user_message(world, task), AgentOutput)
