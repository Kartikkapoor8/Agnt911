"""Loads scenarios/demo.json into Greg's Engine and steps it tick by tick, applying events on their tick.

The engine's own loops run on wall-clock time (10 ticks a second, a round every 2 s); here the
tick only advances between rounds, so scenario ticks mean the same thing on every replay.
"""

import asyncio
import json
from pathlib import Path

from .adapter import PRIORITY_FOR_SEVERITY, Adapter, required_types
from .engine.world import tick as advance

DEMO = Path(__file__).parent / "scenarios" / "demo.json"
SPEED_MPS = 15.0  # the engine's default vehicle speed


def load_scenario(path: str | Path = DEMO) -> dict:
    """Read a scenario JSON file."""
    return json.loads(Path(path).read_text())


def world_factory(scenario: dict):
    """Return a function that builds the engine's tick-0 world: the scenario's vehicles, no incidents yet."""

    def build() -> dict:
        """Build a fresh engine world for one run."""
        return {
            "tick": 0,
            "time_s": 0.0,
            "vehicles": {
                v["id"]: {"id": v["id"], "type": v["type"], "lat": v["location"]["lat"],
                          "lng": v["location"]["lng"], "district": v["home_district"],
                          "speed_mps": SPEED_MPS, "status": "idle", "target_incident_id": None,
                          "trust": 1.0, "hacked": False}
                for v in scenario["vehicles"]
            },
            "incidents": {},
        }

    return build


def engine_incident(incident: dict) -> dict:
    """Convert a scenario incident into the exact dict Engine.add_incident accepts."""
    return {"id": incident["id"], "type": incident["type"], "lat": incident["location"]["lat"],
            "lng": incident["location"]["lng"], "required_types": required_types(incident["type"]),
            "priority": PRIORITY_FOR_SEVERITY[incident["severity"]]}


class ScenarioDriver:
    """Steps one Engine through a scenario: apply due events, run a round when needed, advance one tick."""

    def __init__(self, engine, adapter: Adapter, scenario: dict, dt_s: float = 1.0, retry_every: int = 10):
        """Drive engine with adapter; each tick moves vehicles dt_s simulated seconds."""
        self.engine, self.adapter, self.scenario = engine, adapter, scenario
        self.dt_s, self.retry_every = dt_s, retry_every
        self.events = sorted(scenario["events"], key=lambda e: e["tick"])
        self.run_id, self.cursor = None, 0
        self.last_attempt: tuple[int, tuple] | None = None
        self.rounds: list[dict] = []  # tick, round, status, incidents decided
        self._injected: set[str] = set()
        original = engine.inject

        async def inject(vehicle_id: str, instruction: str) -> None:
            """Engine.inject, then renegotiate the hijacked vehicle's call so its new prompt is used."""
            await original(vehicle_id, instruction)
            self._injected.add(vehicle_id)

        engine.inject = inject  # covers POST /inject as well as scenario inject events

    async def apply_events(self) -> None:
        """Apply every scenario event whose tick has been reached."""
        engine = self.engine
        while self.cursor < len(self.events) and self.events[self.cursor]["tick"] <= engine.world["tick"]:
            event = self.events[self.cursor]
            self.cursor += 1
            if event["type"] == "incident":
                inc = event["incident"]
                await engine.add_incident(engine_incident(inc))
                engine.world["incidents"][inc["id"]].update(severity=inc["severity"], label=inc.get("label"))
            elif event["type"] == "escalate":
                iid = event["incident"]
                await engine.update_incident(iid, {"priority": PRIORITY_FOR_SEVERITY[event["severity"]]})
                engine.world["incidents"][iid].update(severity=event["severity"], label=event.get("label"))
                self.adapter.request(engine.run_id, iid)
            elif event["type"] == "inject":
                await engine.inject(event["vehicle"], event["text"])

    def _route_injections(self) -> None:
        """Turn each new injection into a renegotiation of that vehicle's current call."""
        for vid in sorted(self._injected):
            target = self.engine.world["vehicles"][vid]["target_incident_id"]
            if target:
                self.adapter.request(self.engine.run_id, target)
        self._injected.clear()

    async def step(self) -> bool:
        """Run one tick; return False while the engine is paused for a human."""
        engine = self.engine
        if engine.run_id != self.run_id:  # a mode change starts the scenario over
            self.run_id, self.cursor, self.last_attempt = engine.run_id, 0, None
        if engine.escalation is not None:
            return False
        await self.apply_events()
        self._route_injections()
        todo = tuple(self.adapter.incidents_to_decide(engine.world, engine.run_id))
        now = engine.world["tick"]
        due = todo and (self.last_attempt is None or self.last_attempt[1] != todo
                        or now - self.last_attempt[0] >= self.retry_every)
        if due:
            self.last_attempt = (now, todo)
            await engine.run_round()
            self.rounds.append({"tick": now, "round": engine.round_number,
                                "status": engine.last_round_status, "decided": list(todo)})
            if engine.last_round_status == "completed":
                self.adapter.clear_decided()
        if engine.escalation is not None:
            return False
        advance(engine.world, self.dt_s)
        return True

    async def run(self, until_tick: int, tick_wall_s: float = 0.0, on_pause=None) -> None:
        """Step until the world reaches until_tick; sleep tick_wall_s per tick so pollers can watch."""
        while self.engine.world["tick"] < until_tick:
            if not await self.step():
                if on_pause is not None:
                    await on_pause(self.engine)
                await asyncio.sleep(0.2)
                continue
            if tick_wall_s:
                await asyncio.sleep(tick_wall_s)
