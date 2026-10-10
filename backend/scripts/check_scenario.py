"""Check the demo scenario's geometry: every hack must be catchable.

Runs the scenario through the engine to --until (default 160) with the fake LLM, stepping 1 s per tick, and asserts:
  - every vehicle has another vehicle within 500 m at every scripted tick and every decision tick;
  - each on_scene_resolved hijack hits a vehicle that is free and not at any incident, whose nearest
    unresolved incident (the one it will falsely claim) has a police car on scene and is over 300 m away;
  - each closer_than_you_are hijack hits a vehicle a police car can see, with a matching unresolved
    incident far enough away that a 30 s ETA is impossible.
Prints the nearest-witness distance per vehicle per decision tick. Exit code 1 on any failure.
Usage, from backend/:  .venv/bin/python scripts/check_scenario.py [--until 160] [--scenario path]
"""

import argparse
import asyncio
import tempfile
from pathlib import Path

from agnt911 import protocol, trust
from agnt911.engine.runtime import Engine
from agnt911.engine.world import DEFAULT_SCENARIO, load_scenario
from agnt911.fake_llm import fake_llm
from agnt911.feed import FAKE_ETA_S, preset_kind
from agnt911.geo import haversine_m
from agnt911.models import World

WITNESS_M = 500.0
ON_SCENE_M = 100.0
CONTRADICT_M = 300.0


def nearest_witness(world: World) -> dict[str, tuple[float, str]]:
    """Return, per vehicle, the distance to and id of the nearest other vehicle."""
    out = {}
    for v in world.vehicles.values():
        out[v.id] = min((haversine_m(v.location, o.location), o.id) for o in world.vehicles.values() if o.id != v.id)
    return out


async def simulate(scenario: dict, until: int) -> tuple[dict[int, World], list[int]]:
    """Step the scenario with the fake LLM; return the world at every tick and the ticks where a round committed."""
    engine = Engine(log_dir=Path(tempfile.mkdtemp()), scenario=scenario, llm_fn=fake_llm)
    worlds, decisions = {0: engine.world.model_copy(deep=True)}, []
    try:
        while engine.world.tick < until:
            before = engine.decision
            await engine.run_round()
            if engine.decision != before and engine.last_round_status == "completed":
                decisions.append(engine.world.tick)
            if engine.escalation is not None:
                raise SystemExit(f"tick {engine.world.tick}: the fake LLM escalated to a human; the scenario must not")
            await engine.step(1.0)
            worlds[engine.world.tick] = engine.world.model_copy(deep=True)
    finally:
        await engine.stop()
    return worlds, decisions


def check_hijack(event: dict, preset: dict, world: World) -> list[str]:
    """Return the problems with one scripted hijack, judged on the world at its tick (empty if catchable)."""
    kind, v = preset_kind(preset), world.vehicles.get(event.get("vehicle"))
    if v is None:
        return []
    unresolved = [i for i in world.incidents.values() if i.status != "resolved"]
    police = [p for p in world.vehicles.values() if p.type == "police" and p.id != v.id]
    problems = []
    if kind == "on_scene_resolved":
        if v.status != "free":
            problems.append(f"{v.id} is {v.status}, not free")
        if any(haversine_m(v.location, i.location) <= ON_SCENE_M for i in unresolved):
            problems.append(f"{v.id} is at an incident")
        if not unresolved:
            return problems + ["no unresolved incident to claim"]
        target = min(unresolved, key=lambda i: (haversine_m(v.location, i.location), i.id))
        gap = haversine_m(v.location, target.location)
        if gap <= CONTRADICT_M:
            problems.append(f"{v.id} is only {gap:.0f} m from {target.id}; a false on_scene needs > {CONTRADICT_M:.0f} m")
        on_scene = [p.id for p in police if p.status == "on_scene" and p.target_incident == target.id]
        if not on_scene:
            problems.append(f"no police car on scene at {target.id}")
        print(f"  t{event['tick']} {v.id} on_scene_resolved -> claims {target.id} from {gap:.0f} m; police on scene: {on_scene}")
    elif kind == "closer_than_you_are":
        seers = [(round(haversine_m(v.location, p.location)), p.id) for p in police
                 if haversine_m(v.location, p.location) <= WITNESS_M]
        if not seers:
            problems.append(f"no police car within {WITNESS_M:.0f} m of {v.id}")
        limit = FAKE_ETA_S * trust.MAX_SPEED_MPS + 200.0
        matching = [i for i in unresolved if v.type in protocol.ROLES[i.type]]
        far = [(i.id, round(haversine_m(v.location, i.location))) for i in matching
               if haversine_m(v.location, i.location) > limit]
        if not far:
            problems.append(f"no matching unresolved call over {limit:.0f} m from {v.id}; a 30 s ETA would be possible")
        print(f"  t{event['tick']} {v.id} closer_than_you_are -> seen by {seers}; impossible 30 s bids on {far}")
    return problems


def main() -> None:
    """Simulate, assert the geometry, and print the witness table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", type=int, default=160)
    parser.add_argument("--scenario", type=Path, default=DEFAULT_SCENARIO)
    args = parser.parse_args()
    scenario = load_scenario(args.scenario)
    worlds, decisions = asyncio.run(simulate(scenario, args.until))
    scripted = sorted({e["tick"] for e in scenario["events"] if e["tick"] <= args.until})
    presets = {p["id"]: p["preset"] for p in scenario.get("presets", [])}
    failures = []

    print(f"Hijacks ({args.scenario.name}):")
    for event in scenario["events"]:
        if event["type"] == "inject" and event["tick"] <= args.until:
            preset = presets[event["preset_id"]] if "preset_id" in event else {"kind": "raw"}
            failures += [f"t{event['tick']} hijack: {p}" for p in check_hijack(event, preset, worlds[event["tick"]])]

    vids = sorted(worlds[0].vehicles)
    print(f"\nNearest witness (m) per vehicle; * = decision tick, s = scripted tick, ! = over {WITNESS_M:.0f} m")
    print("| tick | " + " | ".join(vids) + " |\n|---|" + "---|" * len(vids))
    for t in sorted(set(scripted) | set(decisions)):
        near = nearest_witness(worlds[t])
        cells = []
        for vid in vids:
            d, who = near[vid]
            bad = d > WITNESS_M
            cells.append(f"{d:.0f} ({who}){'!' if bad else ''}")
            if bad:
                failures.append(f"t{t} {vid}: nearest vehicle {who} is {d:.0f} m away")
        mark = ("*" if t in decisions else "") + ("s" if t in scripted else "")
        print(f"| {t}{mark} | " + " | ".join(cells) + " |")
    print(f"\n{len(decisions)} decision ticks: {decisions}")
    if failures:
        print("\nFAIL:\n  " + "\n  ".join(failures))
        raise SystemExit(1)
    print("PASS: every hijack is catchable and every vehicle has a witness at every scripted and decision tick.")


if __name__ == "__main__":
    main()
