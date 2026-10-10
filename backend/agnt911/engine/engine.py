"""Async orchestration only. Agent reasoning and trust policy live in adapters."""

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
import math
from pathlib import Path
import random
import time
import uuid

from .bus import MessageBus, canonical
from .logging import RunLog
from .world import add_incident, assign, create_world, observe, refresh_coverage, tick

Agent = Callable[[dict], Awaitable[dict]]


@dataclass
class RoundResult:
    # Only a committed protocol result should contain assignments.
    assignments: dict[str, str | None] = field(default_factory=dict)
    trust: dict[str, float] = field(default_factory=dict)
    metrics: dict = field(default_factory=dict)
    escalation: dict | None = None
    checks: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class RoundContext:
    rng: random.Random
    mode: str
    tick: int


ProtocolRunner = Callable[[dict, list[dict], RoundContext], Awaitable[RoundResult]]


class Engine:
    def __init__(self, log_dir: str | Path = "logs", agents: dict[str, Agent] | None = None,
                 protocol: ProtocolRunner | None = None, tick_interval_s: float = 0.1,
                 round_interval_s: float = 2.0, agent_timeout_s: float = 30.0,
                 seed: int = 911, world_factory: Callable[[], dict] = create_world):
        for value in (tick_interval_s, round_interval_s, agent_timeout_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Intervals and timeouts must be positive and finite")
        self.log_dir = log_dir
        if type(seed) is not int:
            raise ValueError("Seed must be an integer")
        self.seed = seed
        self.world_factory = world_factory
        self.agents = dict(agents or {})
        self.protocol = protocol
        if bool(self.agents) != bool(self.protocol):
            raise ValueError("Provide both vehicle agents and a protocol runner")
        if self.agents and set(self.agents) != set(world_factory()["vehicles"]):
            raise ValueError("Adapter must supply all seven vehicle agents")
        self.tick_interval_s = tick_interval_s
        self.round_interval_s = round_interval_s
        self.agent_timeout_s = agent_timeout_s
        self._lock = asyncio.Lock()
        self._round_lock = asyncio.Lock()
        self._tasks: list[asyncio.Task] = []
        self._revision = 0
        self._loop_error: str | None = None
        self._closed = False
        self._new_run("protected")

    def _new_run(self, mode: str) -> None:
        self.run_id = uuid.uuid4().hex
        self.mode = mode
        self.world = self.world_factory()
        self.rng = random.Random(self.seed)
        self.round_number = 0
        self.metrics = {}
        self.escalation = None
        self.injections = {}
        self.bus = MessageBus(self.world["vehicles"], self.run_id)
        self.log = RunLog(self.log_dir, self.run_id)
        self._log("run_started", 0.0, mode=mode, seed=self.seed, world=self.world)
        for incident in self.world["incidents"].values():
            self._log("incident_created", 0.0, incident=incident)
        self.last_round_status = "waiting" if self.protocol else "unconfigured"
        self.completed_runs = getattr(self, "completed_runs", [])

    def _log(self, event: str, time_s: float, **data) -> None:
        data.setdefault("round", self.round_number)
        self.log.write(event, time_s, tick=self.world["tick"], **data)

    def _commit(self, assignments: dict, source: str) -> None:
        before = {vid: self.world["vehicles"][vid]["target_incident_id"] for vid in assignments}
        assign(self.world, assignments)
        for vid, target in assignments.items():
            if before[vid] != target:
                self._log("dispatch", self.world["time_s"], vehicle_id=vid,
                          previous_target=before[vid], target_incident_id=target, source=source)

    def _snapshot(self) -> dict:
        return deepcopy({"run_id": self.run_id, "mode": self.mode, "seed": self.seed,
                         "round": self.round_number, **self.world,
                         "transcript": self.bus.transcript(), "metrics": self.metrics,
                         "comparison": self.completed_runs, "escalation": self.escalation,
                         "paused": self.escalation is not None,
                         "integration": {"agents_connected": bool(self.agents),
                                         "protocol_connected": self.protocol is not None,
                                         "last_round_status": self.last_round_status,
                                         "loop_error": self._loop_error}})

    async def state(self) -> dict:
        async with self._lock:
            return self._snapshot()

    async def inject(self, vehicle_id: str, instruction: str) -> None:
        if not instruction.strip() or len(instruction) > 2000:
            raise ValueError("Instruction must contain 1–2000 characters")
        async with self._lock:
            if vehicle_id not in self.world["vehicles"]:
                raise KeyError(vehicle_id)
            self.injections[vehicle_id] = instruction
            self.world["vehicles"][vehicle_id]["hacked"] = True
            self._revision += 1
            self._log("vehicle_hacked", self.world["time_s"], vehicle_id=vehicle_id)

    async def set_mode(self, mode: str) -> None:
        if mode not in {"protected", "baseline"}:
            raise ValueError("Unknown mode")
        async with self._lock:
            summary = {"run_id": self.run_id, "mode": self.mode,
                       "time_s": self.world["time_s"], "metrics": deepcopy(self.metrics)}
            self._log("run_finished", self.world["time_s"], metrics=self.metrics, world=self.world)
            self.log.close()
            self.completed_runs = (self.completed_runs + [summary])[-20:]
            self._revision += 1
            self._new_run(mode)

    async def resolve(self, assignments: dict[str, str | None]) -> None:
        async with self._lock:
            if self.escalation is None:
                raise ValueError("There is no pending human escalation")
            # Validate before the logging helper reads vehicle IDs.
            assign(deepcopy(self.world), assignments)
            self._commit(assignments, "human")
            self.escalation = None
            self._revision += 1
            self._log("human_resolution", self.world["time_s"], assignments=assignments)

    async def add_incident(self, incident: dict) -> None:
        async with self._lock:
            add_incident(self.world, incident)
            self._revision += 1
            self._log("incident_created", self.world["time_s"],
                      incident=self.world["incidents"][incident["id"]])

    async def resolve_incident(self, incident_id: str) -> None:
        """Explicit scenario/service completion, separate from physical coverage."""
        async with self._lock:
            incident = self.world["incidents"].get(incident_id)
            if incident is None:
                raise KeyError(incident_id)
            if incident["status"] == "resolved":
                return
            if incident["status"] != "covered":
                raise ValueError("Only a physically covered incident can be resolved")
            incident["status"] = "resolved"
            incident["resolved_at_s"] = self.world["time_s"]
            self._commit({vid: None for vid, v in self.world["vehicles"].items()
                          if v["target_incident_id"] == incident_id}, "incident_resolved")
            self._revision += 1
            self._log("incident_resolved", self.world["time_s"], incident=incident)

    async def update_incident(self, incident_id: str, changes: dict) -> None:
        """Scenario hook, e.g. a crash becoming a pileup; invalidates in-flight plans."""
        if not changes or set(changes) - {"type", "required_types", "priority"}:
            raise ValueError("Only type, required_types, and priority can change")
        if "type" in changes and (not isinstance(changes["type"], str) or not changes["type"]):
            raise ValueError("Incident type must be a nonempty string")
        if "priority" in changes and (type(changes["priority"]) is not int or changes["priority"] not in (1, 2, 3)):
            raise ValueError("Priority must be 1, 2, or 3")
        if "required_types" in changes:
            types = changes["required_types"]
            if not isinstance(types, list) or not types or any(t not in {"ambulance", "fire", "police"} for t in types):
                raise ValueError("Invalid required vehicle types")
        async with self._lock:
            if incident_id not in self.world["incidents"]:
                raise KeyError(incident_id)
            self.world["incidents"][incident_id].update(deepcopy(changes))
            refresh_coverage(self.world)
            self._revision += 1
            self._log("incident_updated", self.world["time_s"], incident_id=incident_id, changes=changes)

    async def _call_agent(self, agent: Agent, observation: dict) -> dict:
        return await asyncio.wait_for(agent(observation), timeout=self.agent_timeout_s)

    async def run_round(self) -> None:
        if self.protocol is None:
            return
        async with self._round_lock:
            async with self._lock:
                if self.escalation is not None:
                    return
                self.round_number += 1
                round_number = self.round_number
                revision = self._revision
                snapshot = self._snapshot()
                context = RoundContext(self.rng, self.mode, self.world["tick"])
                observations = {}
                for vid in self.agents:
                    observations[vid] = {**observe(self.world, vid), "run_id": self.run_id,
                                         "round": round_number, "tick": self.world["tick"], "mode": self.mode,
                                         "transcript": self.bus.transcript(),
                                         "injection": self.injections.get(vid)}
                self.last_round_status = "running"
            # Do not hold the world lock while waiting on seven API calls.
            outputs = await asyncio.gather(*(self._call_agent(self.agents[vid], observations[vid])
                                             for vid in self.agents), return_exceptions=True)
            async with self._lock:
                if revision != self._revision:
                    self._log("round_discarded", self.world["time_s"], reason="scenario_changed")
                    self.last_round_status = "discarded"
                    return
                messages, errors = [], {}
                for vid, output in zip(self.agents, outputs):
                    if isinstance(output, BaseException):
                        errors[vid] = type(output).__name__
                        continue
                    try:
                        message = self.bus.publish(vid, output["kind"], output["payload"],
                                                   round_number, snapshot["time_s"], snapshot["tick"])
                        messages.append(message)
                        self._log("claim" if message["kind"] == "claim" else "message",
                                  self.world["time_s"], message=message)
                    except Exception as exc:
                        errors[vid] = type(exc).__name__
                self._log("round_messages", self.world["time_s"], round=round_number,
                               messages=messages, agent_errors=errors)
            try:
                result = await asyncio.wait_for(self.protocol(deepcopy(snapshot), deepcopy(messages), context),
                                                timeout=self.agent_timeout_s)
                result = deepcopy(result)
                if not isinstance(result, RoundResult):
                    raise ValueError("Protocol must return RoundResult")
                canonical({"metrics": result.metrics, "escalation": result.escalation, "checks": result.checks})
                if not isinstance(result.metrics, dict) or (result.escalation is not None and not isinstance(result.escalation, dict)):
                    raise ValueError("Metrics and escalation must be JSON objects")
                for vid, trust in result.trust.items():
                    if vid not in snapshot["vehicles"] or type(trust) not in (int, float) or not math.isfinite(trust) or not 0 <= trust <= 1:
                        raise ValueError("Trust must be finite and between 0 and 1")
                if not isinstance(result.checks, list):
                    raise ValueError("Checks must be a list")
                known_claims = {(m["sender"], m["sequence"]) for m in self.bus.transcript() + messages
                                if m["kind"] == "claim"}
                for check in result.checks:
                    if (not isinstance(check, dict)
                        or (check["sender"], check["claim_sequence"]) not in known_claims
                        or check["verifier"] not in snapshot["vehicles"]
                        or check["result"] not in ("pass", "fail", "inconclusive")
                        or not isinstance(check["reason"], str)):
                        raise ValueError("Invalid verification record")
                # Validate the entire plan on a copy before touching live state.
                assign(deepcopy(snapshot), result.assignments)
            except asyncio.CancelledError:
                # A self-cancelled adapter must not silently kill the round loop;
                # cancellation of this engine task during shutdown still propagates.
                if asyncio.current_task().cancelling():
                    raise
                async with self._lock:
                    if revision == self._revision:
                        self.last_round_status = "failed"
                        self._log("round_failed", self.world["time_s"], round=round_number,
                                       error="CancelledError")
                return
            except Exception as exc:
                async with self._lock:
                    if revision == self._revision:
                        self.last_round_status = "failed"
                        self._log("round_failed", self.world["time_s"], round=round_number,
                                       error=type(exc).__name__)
                return
            async with self._lock:
                if revision != self._revision:
                    self.last_round_status = "discarded"
                    self._log("round_discarded", self.world["time_s"], reason="scenario_changed")
                    return
                # An escalation pauses dispatch; it cannot also commit a plan.
                if result.escalation is None:
                    self._commit(result.assignments, "protocol")
                for check in result.checks:
                    self._log("verification", self.world["time_s"], check=check)
                for vid, trust in result.trust.items():
                    previous = self.world["vehicles"][vid]["trust"]
                    self.world["vehicles"][vid]["trust"] = trust
                    if previous != trust:
                        self._log("trust_changed", self.world["time_s"], vehicle_id=vid,
                                  previous=previous, trust=trust)
                self.metrics = deepcopy(result.metrics)
                self.escalation = deepcopy(result.escalation)
                self.last_round_status = "completed"
                self._log("round_completed", self.world["time_s"], round=round_number,
                               assignments=result.assignments if result.escalation is None else {},
                               trust=result.trust, metrics=self.metrics, escalation=self.escalation,
                               world=self.world)

    async def _tick_loop(self) -> None:
        previous = time.monotonic()
        while True:
            await asyncio.sleep(self.tick_interval_s)
            now = time.monotonic()
            async with self._lock:
                if self.escalation is None:
                    tick(self.world, now - previous)
            previous = now

    async def _round_loop(self) -> None:
        while True:
            await self.run_round()
            await asyncio.sleep(self.round_interval_s)

    async def _supervise(self, loop) -> None:
        try:
            await loop()
        except Exception as exc:
            self._loop_error = type(exc).__name__
            self._log("loop_failed", self.world["time_s"], error=self._loop_error)
            raise

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("Engine is closed")
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._supervise(loop))
                           for loop in (self._tick_loop, self._round_loop)]

    async def stop(self) -> None:
        if self._closed:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._log("run_stopped", self.world["time_s"], metrics=self.metrics, world=self.world)
        self.log.close()
        self._closed = True
