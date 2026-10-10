"""Run the full demo scenario N times on the stepped clock and report how fast each hijack is caught.

Per run: for each hacked vehicle, the detection tick (its first contradicted claim or bid) and its trust
at ticks 90 and 140; honest vehicles contradicted (false alarms); ticks the human was asked; API requests used.
Escalations are auto-resolved by scripts/_demo.py so each run reaches --until.
Real Claude when ANTHROPIC_API_KEY is set (env or backend/.env), the fake LLM otherwise or with --mock.
Usage, from backend/:  .venv/bin/python scripts/batch_runs.py [--n 10] [--until 160] [--mock]
"""

import argparse
import asyncio
import os
import time
from pathlib import Path

from dotenv import load_dotenv

from agnt911 import llm
from agnt911.engine.runtime import Engine
from agnt911.fake_llm import fake_llm
from _demo import auto_resolve

TRUST_TICKS = (90, 140)


async def one_run(until: int, real: bool) -> dict:
    """Step one scenario run to `until`; return what the table needs."""
    engine = Engine(log_dir=Path("logs"), llm_fn=None if real else fake_llm)
    trust_at, stopped, escalations = {}, None, []
    start = time.perf_counter()
    try:
        while engine.world.tick < until:
            await engine.run_round()
            if engine.escalation is not None:
                escalations.append(engine.world.tick)
                if await auto_resolve(engine) is None:
                    stopped = f"escalation at tick {engine.world.tick} with nobody to send"
                    break
            await engine.step(1.0)
            if engine.world.tick in TRUST_TICKS:
                trust_at[engine.world.tick] = {vid: v.trust for vid, v in engine.world.vehicles.items()}
    finally:
        await engine.stop()
    events = engine.world.events
    hacked = {e.vehicle: e.tick for e in events if e.type == "vehicle_compromised"}
    contradicted = [e for e in events if e.type == "claim_verified" and e.data["result"] == "contradicted"]
    detection = {vid: next((e.tick for e in contradicted if e.vehicle == vid and e.tick >= t), None)
                 for vid, t in hacked.items()}
    untrusted = {vid: next((e.tick for e in events if e.type == "vehicle_untrusted" and e.vehicle == vid), None)
                 for vid in hacked}
    honest = [f"{e.vehicle}@t{e.tick} ({e.data['subject']} {e.data['claim_type']})"
              for e in contradicted if e.vehicle not in hacked or e.tick < hacked[e.vehicle]]
    return dict(escalations=escalations, hacked=hacked, detection=detection, untrusted=untrusted, trust_at=trust_at, honest=honest,
                calls=llm.call_count(), wall=time.perf_counter() - start, stopped=stopped,
                failed=engine.last_round_status, log=engine.log.path.name)


def main() -> None:
    """Run the batch and print one row per hacked vehicle per run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", "--n", type=int, default=1, dest="runs")
    parser.add_argument("--until", type=int, default=160)
    parser.add_argument("--mock", action="store_true")
    args = parser.parse_args()
    load_dotenv()
    real = bool(os.environ.get("ANTHROPIC_API_KEY")) and not args.mock
    print(f"{args.runs} run(s) of the demo scenario to tick {args.until}, stepped clock, "
          f"{'real Claude' if real else 'fake LLM'}\n")
    print("| run | hacked (tick) | detected at | untrusted at | trust t90 | trust t140 | honest contradicted | human asked at | calls | wall |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for n in range(1, args.runs + 1):
        r = asyncio.run(one_run(args.until, real))
        for k, (vid, t) in enumerate(r["hacked"].items()):
            trust = [r["trust_at"].get(tt, {}).get(vid) for tt in TRUST_TICKS]
            honest = (", ".join(r["honest"]) or "none") if k == 0 else ""
            calls = f"{r['calls']}" if k == 0 else ""
            asked = (", ".join(f"t{t}" for t in r["escalations"]) or "never") if k == 0 else ""
            wall = f"{r['wall']:.0f} s" if k == 0 else ""
            print(f"| {n} | {vid} (t{t}) | {r['detection'][vid] or 'never'} | {r['untrusted'][vid] or 'no'} | "
                  f"{trust[0] if trust[0] is not None else '-'} | {trust[1] if trust[1] is not None else '-'} | "
                  f"{honest} | {asked} | {calls} | {wall} |")
        if r["stopped"]:
            print(f"| {n} | {r['stopped']} | | | | | | | | |")
    print(f"\nLast run's JSONL: logs/{r['log']}")


if __name__ == "__main__":
    main()
