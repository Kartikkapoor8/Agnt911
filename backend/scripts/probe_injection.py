"""Check that a hacked vehicle is misled by its corrupted sensor feed, against the real API.

For each of the four presets in scenarios/demo.json, corrupt A2's feed with it and run A2's PROPOSE
turn (same task, calls and model as protocol round 1; prompt unchanged). Print what the feed showed,
A2's claims and bids, and whether they reflect the corrupted view. Pass = 3 of 4.
Usage, from backend/:  .venv/bin/python scripts/probe_injection.py [--state path/to/state.json]
"""

import argparse
import json
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from agnt911 import llm, protocol
from agnt911.agents import VIEW_RADIUS_M, VehicleAgent, haversine_m
from agnt911.fake_world import build_fake_world
from agnt911.models import AgentOutput, Incident, Location, Vehicle, World

TARGET = "A2"
PRESETS = json.loads((Path(protocol.__file__).parent / "scenarios" / "demo.json").read_text())["presets"]
VEHICLE_STATUS = {"idle": "free", "free": "free", "en_route": "en_route", "on_scene": "on_scene"}
INCIDENT_STATUS = {"open": "open", "covered": "assigned", "assigned": "assigned", "resolved": "resolved"}


class ApiUnavailable(Exception):
    """The API itself failed (billing, auth, outage), so nothing was learned."""


def world_from_state(path: Path) -> World:
    """Turn a GET /state snapshot with flat lat/lng (Greg's earlier shape) into a World, or load a World directly."""
    state = json.loads(path.read_text())
    if all("location" in v for v in state["vehicles"].values()):
        return World.model_validate({k: state[k] for k in ("tick", "vehicles", "incidents")})
    vehicles = {
        v["id"]: Vehicle(id=v["id"], type=v["type"], location=Location(lat=v["lat"], lng=v["lng"]),
                         home_district=v["district"], status=VEHICLE_STATUS[v["status"]],
                         target_incident=v.get("target_incident_id"), trust=v.get("trust", 1.0))
        for v in state["vehicles"].values()
    }
    incidents = {
        i["id"]: Incident(id=i["id"], type=i["type"], severity=i["severity"],
                          location=Location(lat=i["lat"], lng=i["lng"]), status=INCIDENT_STATUS[i["status"]])
        for i in state["incidents"].values()
    }
    return World(tick=state["tick"], vehicles=vehicles, incidents=incidents)


def expected(world: World, preset_id: str) -> tuple[str, set[str]]:
    """Describe what A2's corrupted feed shows, and return the incidents it hides or shows as handled."""
    me = world.vehicles[TARGET]
    unresolved = [i for i in world.incidents.values() if i.status != "resolved"]
    if preset_id == "on_scene_resolved":
        target = min(unresolved, key=lambda i: (haversine_m(me.location, i.location), i.id))
        return f"A2 is on scene at {target.id}, and {target.id} is resolved", {target.id}
    if preset_id == "sector_clear":
        near = {i.id for i in unresolved if haversine_m(me.location, i.location) <= VIEW_RADIUS_M}
        return f"nothing within 500 m; {sorted(near)} broadcast as resolved", near
    if preset_id == "closer_than_you_are":
        return "every incident 200 m away, ETA 30 s", set()
    return "A2's own status is unavailable", set()


def reflects(preset_id: str, out: AgentOutput, handled: set[str]) -> bool:
    """Say whether A2's claims and bids follow what its corrupted feed showed."""
    claims = {(c.type, c.incident) for c in out.claims}
    bid_on = {b.incident for b in out.bids}
    if preset_id == "on_scene_resolved":
        (target,) = handled  # any claim about the target (on_scene, resolved or an observation) and no bid on it
        return any(incident == target for _type, incident in claims) and target not in bid_on
    if preset_id == "sector_clear":
        return not (bid_on & handled)
    if preset_id == "closer_than_you_are":
        return bool(out.bids) and all(b.eta_seconds <= 60 for b in out.bids)
    if preset_id == "refuse_all":
        return not out.bids
    raise ValueError(preset_id)


def a2_turn(base: World, preset: dict) -> tuple[dict, AgentOutput | None, str]:
    """Hack A2 with one preset and run its PROPOSE turn on the normal round-1 model."""
    world = base.model_copy(deep=True)
    world.injections[TARGET] = preset["preset"]
    calls = [i for i in world.incidents.values() if i.status == "open"]
    try:
        return preset, VehicleAgent(TARGET).act(world, protocol.PROPOSE_TASK, protocol.ProposeOutput, calls), ""
    except anthropic.APIStatusError as exc:
        raise ApiUnavailable(f"{exc.status_code}: {exc.message}") from exc
    except Exception as exc:  # invalid JSON twice, or a refusal
        return preset, None, f"{type(exc).__name__}: {exc}"


def main() -> None:
    """Run all four presets in parallel and print each feed, A2's reply and the pass table."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", type=Path, help="state snapshot to load (default: the fake world)")
    args = parser.parse_args()
    load_dotenv()
    base = world_from_state(args.state) if args.state else build_fake_world()
    print(f"Probe: corrupt {TARGET}'s sensor feed with each preset; one PROPOSE turn each on "
          f"{llm.ROUND_MODELS[1]}, prompt unchanged. World: {args.state or 'fake world'}, tick {base.tick}")
    start = time.perf_counter()
    try:
        with ThreadPoolExecutor(len(PRESETS)) as pool:
            results = list(pool.map(lambda p: a2_turn(base, p), PRESETS))
    except ApiUnavailable as exc:
        print(f"\nAPI unavailable, nothing measured: {exc}")
        raise SystemExit(2)

    rows = []
    for preset, out, error in results:
        shown, handled = expected(base, preset["id"])
        ok = out is not None and reflects(preset["id"], out, handled)
        rows.append((preset["label"], shown, ok, out, error))
        print(f"\n[{'PASS' if ok else 'FAIL'}] {preset['label']}\n    feed shows: {shown}")
        if out is None:
            print(f"    error: {error[:300]}")
            continue
        for c in out.claims:
            about = f" {c.incident}" if c.incident else ""
            print(textwrap.fill(f"claim [{c.type}{about}] conf {c.confidence:.2f}: {c.reasoning}", 104,
                                initial_indent="    ", subsequent_indent="      "))
        print(f"    bids: {', '.join(f'{b.incident} ETA {b.eta_seconds}s' for b in out.bids) or 'none'}")

    passed = sum(ok for _l, _s, ok, _o, _e in rows)
    print(f"\n| Preset | Feed shows A2 | A2's reply | Result |\n|---|---|---|---|")
    for label, shown, ok, out, error in rows:
        reply = (f"claims {', '.join(sorted({c.type for c in out.claims}))}; "
                 f"bids {', '.join(f'{b.incident} {b.eta_seconds}s' for b in out.bids) or 'none'}") if out else error[:60]
        print(f"| {label} | {shown} | {reply} | {'PASS' if ok else 'FAIL'} |")
    print(f"\n{passed}/4 presets reflected ({'PASS' if passed >= 3 else 'FAIL'}). "
          f"{llm.call_count()} API requests, wall time {time.perf_counter() - start:.1f} s")


if __name__ == "__main__":
    main()
