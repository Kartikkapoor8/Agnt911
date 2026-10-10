"""Greg's runtime calls Kartik's protocol and applies its plans to the shared World."""

import asyncio
from collections import deque
from copy import deepcopy
import json
import math
from pathlib import Path
import random
import time
import uuid

from .. import protocol
from ..agents import LLMFn
from ..fake_llm import fake_llm, split_user_message
from ..models import Incident, Plan, World
from .bus import MessageBus, SignedHistory
from .context import decision_rng
from .logging import RunLog
from .world import assign, covered, load_scenario, scenario_world, tick, validate_injection


class Engine:
    """Own one city, background loops, signed transcript, and audit log."""

    def __init__(self, log_dir: str | Path = "logs", *, world: World | None = None,
                 scenario: dict | None = None, llm_fn: LLMFn | None = fake_llm,
                 protocol_runner=None, tick_interval_s: float = 1.0,
                 round_interval_s: float = 2.0, decision_timeout_s: float = 90.0,
                 seed: int | None = None):
        """Use existing demo events by default; fake LLM is the safe offline default."""
        for value in (tick_interval_s, round_interval_s, decision_timeout_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Intervals and timeout must be positive and finite")
        if world is not None and scenario is not None:
            raise ValueError("Provide world or scenario, not both")
        self.scenario = deepcopy(scenario) if scenario is not None else (load_scenario() if world is None else None)
        self.seed = seed if seed is not None else (self.scenario["seed"] if self.scenario else 911)
        if type(self.seed) is not int:
            raise ValueError("Seed must be an integer")
        self._initial = world.model_copy(deep=True) if world else scenario_world(self.scenario)
        self.log_dir, self.llm_fn = log_dir, llm_fn
        self.protocol_runner = protocol_runner or protocol.run_round
        self.tick_interval_s, self.round_interval_s = tick_interval_s, round_interval_s
        self.decision_timeout_s = decision_timeout_s
        self._lock, self._round_lock = asyncio.Lock(), asyncio.Lock()
        self._tasks = []
        self._revision = 0
        self._closed = False
        self._loop_error = None
        self.comparison = []
        self._new_run(False)

    def _new_run(self, baseline: bool) -> None:
        self._revision += 1
        self.run_id = uuid.uuid4().hex
        self.world = self._initial.model_copy(deep=True)
        self.world.baseline = baseline
        self.world.injections = {}
        self.world.recent_messages = []
        self.time_s, self.decision = 0.0, 0
        self.rng = random.Random(self.seed)
        self.bus = MessageBus(self.world.vehicles, self.run_id)
        self.log = RunLog(self.log_dir, self.run_id)
        self.events, self.checks = deque(maxlen=500), deque(maxlen=500)
        self.plans, self.metrics, self.timing, self._coverage_roles = {}, {}, {}, {}
        self.reported_handled = {}
        self.escalation = None
        self.last_round_status = "waiting"
        self._pending = {iid for iid, i in self.world.incidents.items() if i.status == "open"}
        self._scheduled = sorted(deepcopy(self.scenario["events"]), key=lambda e: e["tick"]) if self.scenario else []
        self._event_cursor = 0
        self._log("run_started", seed=self.seed, baseline=baseline)
        for incident in self.world.incidents.values():
            self._record_created(incident)
        self._apply_events()

    def _log(self, event: str, **data) -> None:
        record = {"run_id": self.run_id, "event": event, "time_s": self.time_s,
                  "tick": self.world.tick, "decision": self.decision, **deepcopy(data)}
        self.log.write(event, self.time_s, **{k: v for k, v in record.items()
                                            if k not in ("run_id", "event", "time_s")})
        self.events.append(record)

    def _record_created(self, incident: Incident, label: str | None = None) -> None:
        self.timing[incident.id] = {"created_at_s": self.time_s, "covered_at_s": None, "resolved_at_s": None}
        self._log("incident_created", incident=incident.model_dump(), label=label)

    def _inject(self, vid: str, text: str) -> None:
        validate_injection(text)
        if vid not in self.world.vehicles:
            raise KeyError(vid)
        self.world.injections[vid] = text
        self._revision += 1
        self._pending.update(i.id for i in self.world.incidents.values() if i.status != "resolved")
        self._log("vehicle_hacked", vehicle_id=vid)

    def _apply_events(self) -> None:
        while self._event_cursor < len(self._scheduled):
            event = self._scheduled[self._event_cursor]
            if event["tick"] > self.world.tick:
                break
            self._event_cursor += 1
            if event["type"] == "incident":
                incident = Incident.model_validate(event["incident"])
                self.world.incidents[incident.id] = incident
                self._pending.add(incident.id)
                self._revision += 1
                self._record_created(incident, event["incident"].get("label"))
            elif event["type"] == "inject":
                self._inject(event["vehicle"], event["text"])
            else:
                iid = event["incident"]
                if self.world.incidents[iid].status != "resolved":
                    self.world.incidents[iid].severity = event["severity"]
                    self._pending.add(iid)
                    self._revision += 1
                    self._log("incident_updated", incident=iid, severity=event["severity"], label=event.get("label"))

    def _state(self) -> dict:
        result = self.world.model_dump(exclude={"injections"})
        result.update(run_id=self.run_id, seed=self.seed, time_s=self.time_s, decision=self.decision,
                      mode="baseline" if self.world.baseline else "protected",
                      compromised=list(self.world.injections), checks=list(self.checks),
                      events=list(self.events), plans={iid: p.model_dump() for iid, p in self.plans.items()},
                      metrics=self.metrics, comparison=self.comparison, incident_timing=self.timing,
                      coverage={iid: covered(self.world, inc, self._coverage_roles.get(iid))
                                for iid, inc in self.world.incidents.items()},
                      reported_handled=self.reported_handled, escalation=self.escalation,
                      paused=self.escalation is not None,
                      integration={"protocol": "agnt911.protocol.run_round",
                                   "llm": "real" if self.llm_fn is None else "fake_or_custom",
                                   "trust_stub": getattr(protocol.trust, "__name__", "").endswith("trust_stub"),
                                   "metrics_available": bool(self.metrics),
                                   "last_round_status": self.last_round_status, "loop_error": self._loop_error},
                      map_center=self.scenario["map_center"] if self.scenario else None,
                      presets=self.scenario.get("presets", []) if self.scenario else [])
        return deepcopy(result)

    async def state(self) -> dict:
        """Return detached shared-model fields plus engine/UI metadata; omit injection text."""
        async with self._lock:
            return self._state()

    async def inject(self, vehicle_id: str, text: str) -> None:
        """Deliver an operator override only to that vehicle's existing system prompt."""
        async with self._lock:
            self._inject(vehicle_id, text)

    async def set_mode(self, mode: str) -> None:
        """Reset the same scenario and seed, retaining summaries for comparison."""
        if mode not in ("protected", "baseline"):
            raise ValueError("Unknown mode")
        async with self._lock:
            self._log("run_finished", metrics=self.metrics, incident_timing=self.timing)
            self.log.close()
            self.comparison = (self.comparison + [{"run_id": self.run_id,
                "baseline": self.world.baseline, "metrics": deepcopy(self.metrics),
                "incident_timing": deepcopy(self.timing)}])[-20:]
            self._new_run(mode == "baseline")

    def _commit(self, assignments: dict, source: str) -> None:
        before = {vid: self.world.vehicles[vid].target_incident for vid in assignments}
        assign(self.world, assignments)
        for vid, iid in assignments.items():
            if before[vid] != iid:
                self._log("dispatch", vehicle_id=vid, previous_target=before[vid],
                          target_incident=iid, source=source)

    def _plan_assignments(self, plans: list[Plan]) -> dict:
        assignments = {}
        for plan in plans:
            if plan.escalated:
                continue
            assigned = [] if plan.evidence and "believed" in plan.evidence else plan.assigned
            for vid in assigned:
                if assignments.get(vid) is not None:
                    raise ValueError("Protocol assigned one vehicle to multiple incidents")
                assignments[vid] = plan.incident
            for vid, vehicle in self.world.vehicles.items():
                if vehicle.target_incident == plan.incident and vid not in assigned:
                    assignments.setdefault(vid, None)
        # A retained assignment cannot be silently stolen from a different call.
        decided = {p.incident for p in plans if not p.escalated}
        for vid, iid in assignments.items():
            vehicle = self.world.vehicles.get(vid)
            if vehicle is None:
                raise ValueError("Unknown assigned vehicle")
            if iid and vehicle.target_incident and vehicle.target_incident not in decided:
                raise ValueError("Vehicle is already committed to another incident")
        assign(self.world.model_copy(deep=True), assignments)
        return assignments

    def _available_llm(self, incident_ids: list[str]):
        base = self.llm_fn
        if base is None:
            from ..llm import call_claude
            base = call_claude

        def call(system, user, response_model):
            output = base(system, user, response_model)
            me = split_user_message(user)[0]["you"]
            if me["status"] != "free":
                target = me["target_incident"]
                output.bids = [b for b in output.bids if target in incident_ids and b.incident == target]
            return output
        return call

    async def run_round(self) -> None:
        """Run the team's complete negotiation on a snapshot, then commit valid non-stale results."""
        async with self._round_lock:
            async with self._lock:
                ids = [iid for iid in self.world.incidents if iid in self._pending
                       and self.world.incidents[iid].status != "resolved"]
                if self.escalation is not None or not ids:
                    return
                self.decision += 1
                decision, revision, run_id = self.decision, self._revision, self.run_id
                snapshot = self.world.model_copy(deep=True)
                active_bus = self.bus

                def on_message(message):
                    if revision == self._revision and run_id == self.run_id:
                        self.world.recent_messages = active_bus.history()
                        self._log(message.kind, round=message.round, message=message.model_dump())

                snapshot.recent_messages = SignedHistory(active_bus.history(), active_bus, decision, on_message)
                self.last_round_status = "running"
            token = decision_rng.set(self.rng)
            try:
                plans = await asyncio.wait_for(
                    self.protocol_runner(snapshot, incident_ids=ids, llm_fn=self._available_llm(ids)),
                    timeout=self.decision_timeout_s)
                if not isinstance(plans, list) or any(not isinstance(p, Plan) for p in plans):
                    raise ValueError("Protocol must return shared Plan models")
                if len(plans) != len(ids) or {p.incident for p in plans} != set(ids):
                    raise ValueError("Protocol did not return one plan per requested incident")
                for vid, vehicle in snapshot.vehicles.items():
                    if vid not in self.world.vehicles or not math.isfinite(vehicle.trust) or not 0 <= vehicle.trust <= 1:
                        raise ValueError("Invalid trust update")
                json.dumps([p.model_dump() for p in plans], allow_nan=False)
                claims = {m.id: m for m in snapshot.recent_messages if m.kind == "claim"}
                for plan in plans:
                    for check in plan.checks:
                        claim = claims[check["claim_id"]]
                        if (check["vehicle"] != claim.vehicle or check["incident"] != claim.incident
                            or check["round"] != claim.round
                            or check["result"] not in ("confirmed", "contradicted", "unverifiable")
                            or (check["verifier"] is not None and check["verifier"] not in snapshot.vehicles)
                            or not active_bus.verify(claim)):
                            raise ValueError("Invalid verification record")
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                if revision == self._revision and run_id == self.run_id:
                    self.last_round_status = "failed"
                    self._log("round_failed", error="CancelledError")
                return
            except Exception as exc:
                if revision == self._revision:
                    self.last_round_status = "failed"
                    self._log("round_failed", error=type(exc).__name__)
                return
            finally:
                decision_rng.reset(token)
            async with self._lock:
                if revision != self._revision or run_id != self.run_id:
                    self.last_round_status = "discarded"
                    self._log("round_discarded", source_run=run_id, source_decision=decision)
                    return
                try:
                    assignments = self._plan_assignments(plans)
                except ValueError as exc:
                    self.last_round_status = "failed"
                    self._log("round_failed", error=str(exc))
                    return
                self._commit(assignments, "protocol")
                for vid, updated in snapshot.vehicles.items():
                    live = self.world.vehicles[vid]
                    if (live.trust, live.untrusted) != (updated.trust, updated.untrusted):
                        self._log("trust_changed", vehicle_id=vid, previous=live.trust,
                                  trust=updated.trust, untrusted=updated.untrusted)
                        live.trust, live.untrusted = updated.trust, updated.untrusted
                checks = {c["claim_id"]: c for p in plans for c in p.checks}
                for check in checks.values():
                    self.checks.append(deepcopy(check))
                    self._log("verification", round=check["round"], check=check)
                for plan in plans:
                    self.plans[plan.incident] = plan.model_copy(deep=True)
                    if not plan.escalated:
                        incident = self.world.incidents[plan.incident]
                        if plan.evidence and "believed" in plan.evidence:
                            self.reported_handled[plan.incident] = plan.evidence["believed"]
                            incident.status = "assigned"  # Belief does not establish physical resolution.
                        else:
                            self.reported_handled.pop(plan.incident, None)
                        if incident.severity <= 2 and plan.assigned:
                            self._coverage_roles[incident.id] = [self.world.vehicles[plan.assigned[0]].type]
                    self._log("plan_committed" if not plan.escalated else "escalation", plan=plan.model_dump())
                escalated = [p.model_dump() for p in plans if p.escalated]
                self.escalation = {"plans": escalated} if escalated else None
                self._pending.difference_update(ids)
                self.last_round_status = "completed"
                self._refresh_coverage()
                self._log("round_completed", plans=[p.model_dump() for p in plans])

    def _refresh_coverage(self) -> None:
        for iid, incident in self.world.incidents.items():
            if covered(self.world, incident, self._coverage_roles.get(iid)) and self.timing[iid]["covered_at_s"] is None:
                self.timing[iid]["covered_at_s"] = self.time_s
                self._log("incident_covered", incident=iid)

    async def step(self, dt_s: float | None = None) -> None:
        """Advance physical state and apply scheduled events unless a human decision is pending."""
        async with self._lock:
            if self.escalation is not None:
                return
            dt_s = self.tick_interval_s if dt_s is None else dt_s
            arrivals = tick(self.world, dt_s)
            self.time_s += dt_s
            for vid in arrivals:
                self._log("vehicle_arrived", vehicle_id=vid, incident=self.world.vehicles[vid].target_incident)
            self._refresh_coverage()
            self._apply_events()

    async def add_incident(self, data: dict) -> None:
        """Introduce a new shared-model call and schedule negotiation."""
        incident = Incident.model_validate(data)
        if incident.status != "open" or incident.assigned:
            raise ValueError("New incidents must be open and unassigned")
        async with self._lock:
            if incident.id in self.world.incidents:
                raise ValueError("Incident already exists")
            self.world.incidents[incident.id] = incident
            self._pending.add(incident.id)
            self._revision += 1
            self._record_created(incident)

    async def update_incident(self, incident_id: str, changes: dict) -> None:
        """Re-negotiate a change in incident type or severity without changing shared models."""
        if not changes or set(changes) - {"type", "severity"}:
            raise ValueError("Only type and severity can change")
        async with self._lock:
            if incident_id not in self.world.incidents:
                raise KeyError(incident_id)
            incident = Incident.model_validate({**self.world.incidents[incident_id].model_dump(), **changes})
            if incident.status == "resolved":
                raise ValueError("Incident is already resolved")
            self.world.incidents[incident_id] = incident
            self._coverage_roles.pop(incident_id, None)
            self._pending.add(incident_id)
            self._revision += 1
            self._log("incident_updated", incident=incident_id, changes=changes)

    async def resolve_incident(self, incident_id: str) -> None:
        """Complete service only after physical coverage, release units, and reconsider open calls."""
        async with self._lock:
            incident = self.world.incidents.get(incident_id)
            if incident is None:
                raise KeyError(incident_id)
            if incident.status == "resolved":
                return
            if not covered(self.world, incident, self._coverage_roles.get(incident_id)):
                raise ValueError("Incident is not physically covered")
            self._commit({v.id: None for v in self.world.vehicles.values()
                          if v.target_incident == incident_id}, "incident_resolved")
            incident.status = "resolved"
            self.timing[incident_id]["resolved_at_s"] = self.time_s
            self._pending.update(i.id for i in self.world.incidents.values() if i.status == "open")
            self._revision += 1
            self._log("incident_resolved", incident=incident_id)

    async def resolve(self, assignments: dict[str, str | None]) -> None:
        """Apply a compatible human plan for all pending escalations and resume the loops."""
        async with self._lock:
            if self.escalation is None:
                raise ValueError("No pending escalation")
            pending = {p["incident"] for p in self.escalation["plans"]}
            if {iid for iid in assignments.values() if iid} - pending:
                raise ValueError("Human choices must address pending escalations")
            plans = []
            for iid in pending:
                selected = [vid for vid, target in assignments.items() if target == iid]
                if not selected:
                    raise ValueError("Choose responders for each pending escalation")
                if any(vid not in self.world.vehicles for vid in selected):
                    raise ValueError("Unknown vehicle in human choice")
                roles = {self.world.vehicles[vid].type for vid in selected}
                if not set(protocol.required_roles(self.world.incidents[iid])) <= roles:
                    raise ValueError("Human choice must cover every required role")
                plans.append(Plan(incident=iid, assigned=selected, round_reached=4,
                                  consensus=False, escalated=False, evidence={"human": True}))
            changes = self._plan_assignments(plans)
            assign(self.world.model_copy(deep=True), assignments)
            self._commit({**changes, **assignments}, "human")
            for plan in plans:
                self.plans[plan.incident] = plan
            self.escalation = None
            self._revision += 1
            self._log("human_resolution", assignments=assignments)

    async def _tick_loop(self):
        previous = time.monotonic()
        while True:
            await asyncio.sleep(self.tick_interval_s)
            now = time.monotonic()
            await self.step(now - previous)
            previous = now

    async def _round_loop(self):
        while True:
            await self.run_round()
            await asyncio.sleep(self.round_interval_s)

    async def _supervise(self, loop):
        try:
            await loop()
        except Exception as exc:
            self._loop_error = type(exc).__name__
            self._log("loop_failed", error=self._loop_error)
            raise

    async def start(self) -> None:
        """Start one movement task and one negotiation task, once."""
        if self._closed:
            raise RuntimeError("Engine is closed")
        if not self._tasks:
            self._tasks = [asyncio.create_task(self._supervise(loop))
                           for loop in (self._tick_loop, self._round_loop)]

    async def stop(self) -> None:
        """Cancel the engine tasks and flush a final state without exposing injection text."""
        if self._closed:
            return
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._log("run_stopped", world=self.world.model_dump(exclude={"injections"}), incident_timing=self.timing)
        self.log.close()
        self._closed = True
