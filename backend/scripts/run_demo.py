"""Serve the engine on HTTP while stepping it through scenarios/demo.json with the protocol plugged in.

Real Claude when ANTHROPIC_API_KEY is set (env or --env file), the offline fake LLM with --mock.
Usage, from backend/:  .venv/bin/python scripts/run_demo.py [--mock] [--until 95] [--port 8000]
"""

import argparse
import asyncio
import logging
import os
from contextlib import asynccontextmanager

import uvicorn
from dotenv import load_dotenv

from agnt911.adapter import Adapter
from agnt911.engine import Engine
from agnt911.engine.api import create_app
from agnt911.fake_llm import fake_llm
from agnt911.scenario import ScenarioDriver, load_scenario, world_factory


def main() -> None:
    """Parse flags, build engine + adapter + driver, and serve /state and /inject while the scenario runs."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mock", action="store_true", help="use the fake LLM even if a key is set")
    parser.add_argument("--env", default=".env", help="dotenv file with ANTHROPIC_API_KEY")
    parser.add_argument("--until", type=int, default=95, help="stop stepping at this tick")
    parser.add_argument("--tick-wall", type=float, default=0.3, help="wall seconds per tick, for pollers")
    parser.add_argument("--dt", type=float, default=1.0, help="simulated seconds per tick")
    parser.add_argument("--start-delay", type=float, default=3.0, help="wall seconds before tick 0 runs")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    load_dotenv(args.env)
    logging.basicConfig(level=logging.WARNING, format="  !!  %(message)s")
    real = bool(os.environ.get("ANTHROPIC_API_KEY")) and not args.mock
    scenario = load_scenario()
    adapter = Adapter([v["id"] for v in scenario["vehicles"]], llm_fn=None if real else fake_llm)
    engine = Engine(log_dir=args.log_dir, agents=adapter.agents, protocol=adapter.run_protocol,
                    world_factory=world_factory(scenario), agent_timeout_s=300)
    adapter.bind(engine)
    driver = ScenarioDriver(engine, adapter, scenario, dt_s=args.dt)
    print(f"Agnt911 demo · {'real Claude' if real else 'fake LLM'} · log {engine.log.path}", flush=True)

    async def report_pause(eng) -> None:
        """Print once per escalation that the world is waiting for POST /resolve."""
        if getattr(report_pause, "seen", None) is not eng.escalation:
            report_pause.seen = eng.escalation
            print(f"paused at tick {eng.world['tick']} for a human: {eng.escalation.get('reason')}", flush=True)

    async def drive() -> None:
        """Step the scenario, printing each round as it finishes."""
        done = 0
        async def watch():
            nonlocal done
            while True:
                for r in driver.rounds[done:]:
                    print(f"round {r['round']} at tick {r['tick']}: {r['status']} {r['decided']}", flush=True)
                done = len(driver.rounds)
                await asyncio.sleep(0.5)
        watcher = asyncio.create_task(watch())
        await asyncio.sleep(args.start_delay)
        await driver.run(args.until, tick_wall_s=args.tick_wall, on_pause=report_pause)
        await asyncio.sleep(1)
        watcher.cancel()
        print(f"reached tick {engine.world['tick']}", flush=True)

    @asynccontextmanager
    async def lifespan(app):
        """Run the scenario driver instead of the engine's free-running wall-clock loops."""
        app.state.engine = engine
        task = asyncio.create_task(drive())
        try:
            yield
        finally:
            task.cancel()
            await engine.stop()

    app = create_app(engine)
    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
