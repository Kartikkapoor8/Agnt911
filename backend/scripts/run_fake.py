"""Run one negotiation on the fake world and print the transcript.

Uses real Claude when ANTHROPIC_API_KEY is set (env or backend/.env), the offline fake LLM otherwise.
Usage, from backend/:  .venv/bin/python scripts/run_fake.py [--mock] [--baseline] [--incident I1 ...]
"""

import argparse
import asyncio
import logging
import os
import textwrap
import threading
import time

from dotenv import load_dotenv

from agnt911 import llm, protocol
from agnt911.fake_llm import fake_llm
from agnt911.fake_world import build_fake_world
from agnt911.models import Plan, World

WIDTH = 110


class Counter:
    """Thread-safe call counter used to wrap the LLM function and the raw API client."""

    def __init__(self):
        """Start at zero."""
        self.n = 0
        self._lock = threading.Lock()

    def wrap(self, fn):
        """Return fn wrapped so every call bumps the count."""
        def counted(*args, **kwargs):
            with self._lock:
                self.n += 1
            return fn(*args, **kwargs)
        return counted


def say(who: str, text: str) -> None:
    """Print one transcript line, wrapped and indented under the speaker."""
    print(textwrap.fill(text, WIDTH, initial_indent=f"  {who:<4}", subsequent_indent=" " * 6))


def print_transcript(world: World, plans: list[Plan]) -> None:
    """Print the log grouped by round, then the checks, tallies and plans."""
    options = {p.incident: p.options for p in plans}
    for round_no in sorted({m.round for m in world.recent_messages}):
        print(f"\n=== Round {round_no} · {protocol.PHASES[round_no]} ===")
        for m in (m for m in world.recent_messages if m.round == round_no):
            if m.kind == "claim":
                about = f" {m.incident}" if m.incident else ""
                say(m.vehicle, f"[{m.type}{about}] conf {m.confidence:.2f}: {m.reasoning}")
            elif m.kind == "bid":
                say(m.vehicle, f"BID {m.incident}: ETA {m.eta_seconds}s, capability {m.capability_match:.2f}, "
                               f"district cost {m.cost_to_district:.2f}: {m.reasoning}")
            else:
                pick = [v for v in options.get(m.incident, []) if world.vehicles[v].type == m.role]
                choice = pick[m.option_index] if m.option_index < len(pick) else "?"
                say(m.vehicle, f"VOTE {m.incident}/{m.role} -> #{m.option_index} ({choice}): {m.reasoning}")
        checks = {c["claim_id"]: c for p in plans for c in p.checks if c["round"] == round_no}
        for c in checks.values():
            say("chk", f"{c['verifier'] or 'nobody'} checked {c['vehicle']} {c['claim_id']}: {c['result']}")

    print(f"\n=== Round 4 · {protocol.PHASES[4]} ===")
    for p in plans:
        sev = world.incidents[p.incident].severity
        if world.baseline:
            tally = f"dispatcher ranks {', '.join(p.options) or 'no bids'}"
            how = "dispatcher"
        else:
            tally = "tally " + (", ".join(f"{v} {w:.1f}" for v, w in p.tally.items()) or "no options")
            how = "consensus" if p.consensus else "no consensus"
        roles = "+".join(protocol.required_roles(world.incidents[p.incident]))
        outcome = f"-> {', '.join(p.assigned)} ({how})" if p.assigned else "-> nobody"
        if p.escalated:
            outcome += f"; ESCALATED {p.evidence['role']} to human, options {p.evidence.get('options')}"
        if p.evidence and "fallback" in p.evidence:
            f = p.evidence["fallback"]
            outcome += f" (fallback to {f['role']}: {f['reason']})"
        if p.evidence and "believed" in p.evidence:
            b = p.evidence["believed"]
            outcome += f" (believed {b['vehicle']}'s '{b['type']}' report)"
        say(p.incident, f"{world.incidents[p.incident].type} sev {sev} needs {roles} | {tally} | {outcome}")


def main() -> None:
    """Parse flags, run one negotiation, and print the transcript and call counts."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="use the fake LLM even if a key is set")
    parser.add_argument("--baseline", action="store_true", help="central-dispatcher mode")
    parser.add_argument("--incident", action="append", help="only decide these incident ids")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=logging.WARNING, format="  !!  %(message)s")
    real = bool(os.environ.get("ANTHROPIC_API_KEY")) and not args.mock
    world = build_fake_world()
    world.baseline = args.baseline

    turns, requests = Counter(), Counter()
    if real:
        client = llm.get_client()
        client.messages.create = requests.wrap(client.messages.create)
        llm_fn = turns.wrap(llm.call_claude)
    else:
        llm_fn = turns.wrap(fake_llm)

    mode = f"{'real Claude ' + llm.MODEL if real else 'fake LLM'}, {'baseline' if args.baseline else 'negotiation'}"
    print(f"Agnt911 fake world · {len(world.vehicles)} vehicles · {len(world.incidents)} incidents · {mode}")
    start = time.perf_counter()
    plans = asyncio.run(protocol.run_round(world, args.incident, llm_fn=llm_fn))
    wall = time.perf_counter() - start

    print_transcript(world, plans)
    api = f", {requests.n} API requests (incl. retries)" if real else ""
    print(f"\nLLM calls: {turns.n} vehicle turns{api} · wall time {wall:.1f} s")


if __name__ == "__main__":
    main()
