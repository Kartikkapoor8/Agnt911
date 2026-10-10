"""VehicleAgent: builds a vehicle's prompt from its 500 m view of the world and asks Claude for its move."""

import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from . import llm
from .feed import corrupt_view
from .geo import EARTH_RADIUS_M, haversine_m  # noqa: F401  (re-exported: other modules import them from here)
from .models import AgentOutput, Incident, Vehicle, World

PROMPT_DIR = Path(__file__).parent / "prompts"
VIEW_RADIUS_M = 500.0
DEFAULT_TASK = "Report any claims you should make, and bid on or vote for the incident you are best suited for."
# Message fields kept for the frontend and logs but never shown to any vehicle.
HIDDEN_MESSAGE_FIELDS = {"compromised"}

LLMFn = Callable[[str, str, type[BaseModel]], BaseModel]


def load_system_prompt(vehicle_type: str) -> str:
    """Join the shared prompt skeleton with the prompt file for this vehicle type."""
    shared = (PROMPT_DIR / "shared.md").read_text()
    specific = (PROMPT_DIR / f"{vehicle_type}.md").read_text()
    return f"{shared.strip()}\n\n{specific.strip()}\n"


def render_calls(calls: list[dict]) -> str:
    """Write the broadcast 911 calls as task lines, including any ETA or resolved status the view carries."""
    lines = []
    for c in calls:
        line = (f"- {c['id']}: {c['type']}, severity {c['severity']}, "
                f"at {c['location']['lat']:.4f},{c['location']['lng']:.4f}, {c['distance_m']} m from you")
        if "eta_s" in c:
            line += f", ETA {c['eta_s']} s"
        if c.get("status") == "resolved":
            line += ", status resolved"
        lines.append(line)
    return "Open 911 calls (broadcast to every vehicle):\n" + "\n".join(lines)


class VehicleAgent:
    """One vehicle's brain: sees a filtered view of the world and returns an AgentOutput."""

    def __init__(self, vehicle_id: str, llm_fn: LLMFn | None = None):
        """Create an agent for the given vehicle id, optionally with a replacement LLM function."""
        self.vehicle_id = vehicle_id
        self._llm_fn = llm_fn

    def system_prompt(self, world: World) -> str:
        """Return the system prompt (shared skeleton plus type file); the same for hacked and honest vehicles."""
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

    def sensor_view(self, world: World, calls: list[Incident] | None = None) -> dict:
        """Return what this vehicle perceives: its 500 m view, the message log and the broadcast calls.

        A hacked vehicle (one with a preset in world.injections) gets the view through feed.corrupt_view.
        """
        me = world.vehicles[self.vehicle_id]
        vehicles, incidents = self.view(world)
        view = {
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
            "shared_message_log": [m.model_dump(exclude=HIDDEN_MESSAGE_FIELDS) for m in world.recent_messages],
        }
        if calls:
            view["broadcast_calls"] = [
                {"id": i.id, "type": i.type, "severity": i.severity, "location": i.location.model_dump(),
                 "status": i.status, "distance_m": round(haversine_m(me.location, i.location))}
                for i in calls
            ]
        preset = world.injections.get(self.vehicle_id)
        return corrupt_view(view, preset, world) if preset else view

    def build_user_message(self, world: World, task: str = DEFAULT_TASK, calls: list[Incident] | None = None) -> str:
        """Build the user message: the sensor view as JSON, then the task, then any broadcast calls."""
        view = self.sensor_view(world, calls)
        broadcast = view.pop("broadcast_calls", None)
        message = f"{json.dumps(view, indent=2)}\n\nTask: {task}"
        return f"{message}\n\n{render_calls(broadcast)}" if broadcast else message

    def act(
        self,
        world: World,
        task: str = DEFAULT_TASK,
        response_model: type[AgentOutput] = AgentOutput,
        calls: list[Incident] | None = None,
    ) -> AgentOutput:
        """Ask Claude what this vehicle does this round and return the validated output."""
        call = self._llm_fn or llm.call_claude
        return call(self.system_prompt(world), self.build_user_message(world, task, calls), response_model)
