"""Serve a saved run's JSONL through the real FastAPI /state endpoint, no LLM.

    python scripts/replay.py                      # latest log in backend/logs/
    python scripts/replay.py --log logs/<id>.jsonl --speed 10 --port 8000

The log records events, not positions, so frames are rebuilt by applying logged
dispatches to the scenario fleet and moving vehicles with the engine's own tick().
"""

import argparse
import asyncio
import json
import sys
import time
from copy import deepcopy
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agnt911.engine.api import create_app  # noqa: E402
from agnt911.engine.world import load_scenario, scenario_world, tick  # noqa: E402
from agnt911.events import STATE_EVENT_LIMIT  # noqa: E402
from agnt911.models import Event, Incident  # noqa: E402

LOG_DIR = Path(__file__).resolve().parents[1] / "logs"


def latest_log() -> Path:
    logs = sorted(LOG_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not logs:
        sys.exit(f"No .jsonl logs in {LOG_DIR}")
    return logs[-1]


def build_frames(path: Path, scenario: dict) -> list[dict]:
    """One /state-shaped dict per logged tick."""
    records = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    world = scenario_world(scenario)
    labels, compromised, log, escalation = {}, [], [], None
    run_id, baseline = records[0]["run_id"], records[0].get("baseline", False)
    frames: list[dict] = []

    def snapshot():
        state = world.model_dump(exclude={"injections"})
        state["events"] = state["events"][-STATE_EVENT_LIMIT:]
        state.update(run_id=run_id, time_s=float(world.tick), mode="baseline" if baseline else "protected",
                     compromised=list(compromised), labels=dict(labels), log=log[-500:],
                     escalation=escalation, paused=escalation is not None,
                     map_center=scenario.get("map_center"), presets=scenario.get("presets", []),
                     replay=True)
        frames.append(deepcopy(state))

    for rec in records:
        while world.tick < rec["tick"]:
            snapshot()
            tick(world, 1.0)
        kind = rec["event"]
        if kind == "incident_created":
            inc = Incident.model_validate(rec["incident"])
            world.incidents[inc.id] = inc
            if rec.get("label"):
                labels[inc.id] = rec["label"]
        elif kind == "incident_updated":
            inc = world.incidents[rec["incident"]]
            if rec.get("severity") is not None:
                inc.severity = rec["severity"]
            if rec.get("label"):
                labels[inc.id] = rec["label"]
        elif kind == "dispatch":
            v = world.vehicles[rec["vehicle_id"]]
            old, new = v.target_incident, rec["target_incident"]
            if old in world.incidents:
                world.incidents[old].assigned = [x for x in world.incidents[old].assigned if x != v.id]
            v.target_incident = new
            v.status = "free" if new is None else "en_route"
            if new is not None:
                world.incidents[new].assigned.append(v.id)
                if world.incidents[new].status == "open":
                    world.incidents[new].status = "assigned"
        elif kind == "vehicle_arrived":
            world.vehicles[rec["vehicle_id"]].status = "on_scene"
        elif kind == "incident_resolved":
            world.incidents[rec["incident"]].status = "resolved"
        elif kind == "trust_changed":
            v = world.vehicles[rec["vehicle_id"]]
            v.trust, v.untrusted = rec["trust"], rec["untrusted"]
        elif kind == "vehicle_hacked":
            compromised.append(rec["vehicle_id"])
        elif kind == "escalation":
            escalation = rec.get("plan")
        elif kind == "human_resolution":
            escalation = None
        elif kind == "typed_event":
            world.events.append(Event.model_validate({k: rec[k] for k in ("tick", "type", "vehicle", "incident", "data")}))
        if kind not in ("bid", "typed_event"):
            log.append(rec)
    snapshot()
    return frames


class ReplayEngine:
    """Duck-types the parts of Engine that /state needs; frames advance on wall time."""

    def __init__(self, frames: list[dict], speed: float, loop: bool):
        self.frames, self.speed, self.loop = frames, speed, loop
        self.t0 = None

    async def start(self):
        self.t0 = time.monotonic()

    async def stop(self):
        pass

    async def state(self) -> dict:
        i = int((time.monotonic() - self.t0) * self.speed)
        i = i % len(self.frames) if self.loop else min(i, len(self.frames) - 1)
        return self.frames[i]

    async def _readonly(self, *a, **k):
        raise ValueError("Replay is read-only")

    inject = set_mode = resolve = _readonly


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", type=Path, default=None, help="JSONL run log (default: latest in backend/logs)")
    ap.add_argument("--speed", type=float, default=10.0, help="ticks per wall second (1 tick = 1 sim second)")
    ap.add_argument("--scenario", type=Path, default=None, help="scenario JSON for the starting fleet")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--loop", action="store_true", help="restart from tick 0 at the end")
    args = ap.parse_args()

    path = args.log or latest_log()
    scenario = load_scenario(args.scenario) if args.scenario else load_scenario()
    frames = build_frames(path, scenario)
    print(f"replaying {path.name}: {len(frames)} ticks at {args.speed}x "
          f"(~{len(frames) / args.speed:.0f}s) on http://127.0.0.1:{args.port}/state", flush=True)
    uvicorn.run(create_app(ReplayEngine(frames, args.speed, args.loop)), host="127.0.0.1",
                port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
