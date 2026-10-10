"""Run the scripted demo scenario through the engine, stepping 1 s per tick, and print the typed event stream.

Real Claude when ANTHROPIC_API_KEY is set (env or backend/.env), the fake LLM otherwise or with --mock.
A human escalation is auto-resolved by scripts/_demo.py so the run can continue (and says so).
Usage, from backend/:  .venv/bin/python scripts/run_scenario.py --until 160 --show 55-90 [--mock]
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from agnt911 import llm
from agnt911.engine.runtime import Engine
from agnt911.fake_llm import fake_llm
from _demo import auto_resolve


def describe(event: dict) -> str:
    """One readable line per event."""
    who = " ".join(x for x in (event["vehicle"], event["incident"]) if x)
    data = ", ".join(f"{k}={json.dumps(v)}" for k, v in event["data"].items()
                     if v is not None and v is not False and v != {} and k not in ("evidence", "tally"))
    if "tally" in event["data"]:
        data += f", tally={json.dumps(event['data']['tally'])}"
    if event["data"].get("evidence"):
        data += f", evidence={len(json.dumps(event['data']['evidence']))} chars"
    return f"t{event['tick']:>4}  {event['type']:<22} {who:<8} {data}"


async def run(until: int, real: bool) -> Engine:
    """Step the demo to tick `until`, negotiating whenever something is pending."""
    engine = Engine(log_dir=Path("logs"), llm_fn=None if real else fake_llm)
    try:
        while engine.world.tick < until:
            await engine.run_round()
            if engine.escalation is not None:
                answer = await auto_resolve(engine)
                if answer is None:
                    print(f"  tick {engine.world.tick}: escalation with nobody to send; stopping here")
                    break
                print(f"  tick {engine.world.tick}: auto-resolved escalation (stand-in for the human): {answer}")
            await engine.step(1.0)
            if engine.world.tick % 10 == 0:
                print(f"  tick {engine.world.tick}: {len(engine.world.events)} events, "
                      f"{llm.call_count()} API requests, last round {engine.last_round_status}", flush=True)
    finally:
        await engine.stop()
    return engine


def main() -> None:
    """Run the scenario, then print the events in the --show tick range and save them all."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--until", type=int, default=160)
    parser.add_argument("--show", default="0-9999", help="tick range to print, e.g. 55-90")
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    real = bool(os.environ.get("ANTHROPIC_API_KEY")) and not args.mock
    lo, hi = (int(x) for x in args.show.split("-"))
    print(f"Demo scenario to tick {args.until} with {'real Claude' if real else 'the fake LLM'}")
    start = time.perf_counter()
    engine = asyncio.run(run(args.until, real))
    events = [e.model_dump() for e in engine.world.events]
    out = engine.log.path.with_suffix(".events.json")
    out.write_text(json.dumps(events, indent=1))
    print(f"\nEvents from tick {lo} to {hi}:")
    for e in events:
        if lo <= e["tick"] <= hi:
            print(describe(e))
    counts = {}
    for e in events:
        counts[e["type"]] = counts.get(e["type"], 0) + 1
    print(f"\n{len(events)} events, by type: {counts}")
    print(f"{llm.call_count()} API requests, wall {time.perf_counter() - start:.0f} s. "
          f"Full JSONL: {engine.log.path}  Events: {out}")


if __name__ == "__main__":
    main()
