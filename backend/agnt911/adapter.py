"""Integration seam: plugs protocol.run_round into Greg's Engine (the AGNT911_ADAPTER contract).

The engine's world is a dict ("lat"/"lng", "district", "idle", "priority", "signature");
the protocol works on models.World ("location", "home_district", "free", "severity", "sig").
This module converts between the two, picks which incidents a round decides, signs every
claim, bid and vote the protocol produced on the engine's bus, and turns Plans into the
engine's RoundResult. Nothing here decides who goes where; that is protocol.py.
"""

from . import protocol
from .engine import RoundResult
from .models import Bid, Claim, Incident, Location, Plan, Vehicle, Vote, World

GREG_STATUS = {"idle": "free", "en_route": "en_route", "on_scene": "on_scene"}
PRIORITY_FOR_SEVERITY = {1: 1, 2: 1, 3: 2, 4: 3, 5: 3}
SEVERITY_FOR_PRIORITY = {1: 2, 2: 3, 3: 5}
CHECK_RESULT = {"confirmed": "pass", "contradicted": "fail", "unverifiable": "inconclusive"}
UNTRUSTED_BELOW = 0.4  # CLAUDE.md: untrusted below 0.4; the engine stores trust but no flag
MAX_LOG_MESSAGES = 100  # signed claims/bids/votes handed to the agents as the shared log
MESSAGE_MODELS = {"claim": Claim, "bid": Bid, "vote": Vote}


def required_types(incident_type: str) -> list[str]:
    """Return every vehicle type the protocol may assign to this incident type (fallback roles included)."""
    return list(protocol.ROLES[incident_type])


def from_transcript(transcript: list[dict]) -> list[Claim | Bid | Vote]:
    """Rebuild the protocol's message models from the engine's signed envelopes, with sig set to the signature."""
    out = []
    for m in transcript:
        model = MESSAGE_MODELS.get(m["kind"])
        if model and m["payload"].get("kind") == m["kind"]:
            out.append(model.model_validate({**m["payload"], "sig": m["signature"]}))
    return out


def to_world(snapshot: dict, injections: dict[str, str], max_messages: int = MAX_LOG_MESSAGES) -> World:
    """Convert an engine snapshot into the protocol's World."""
    vehicles = {
        vid: Vehicle(
            id=vid, type=v["type"], location=Location(lat=v["lat"], lng=v["lng"]),
            home_district=v["district"], status=GREG_STATUS[v["status"]],
            target_incident=v["target_incident_id"], trust=v["trust"],
            untrusted=v["trust"] < UNTRUSTED_BELOW,
        )
        for vid, v in snapshot["vehicles"].items()
    }
    incidents = {}
    for iid, i in snapshot["incidents"].items():
        assigned = [vid for vid, v in vehicles.items() if v.target_incident == iid]
        status = "resolved" if i["status"] == "resolved" else ("assigned" if assigned else "open")
        incidents[iid] = Incident(
            id=iid, type=i["type"], severity=i.get("severity", SEVERITY_FOR_PRIORITY[i["priority"]]),
            location=Location(lat=i["lat"], lng=i["lng"]), status=status, assigned=assigned,
        )
    messages = from_transcript(snapshot["transcript"])[-max_messages:]
    return World(tick=snapshot["tick"], vehicles=vehicles, incidents=incidents,
                 recent_messages=messages, baseline=snapshot["mode"] == "baseline",
                 injections=dict(injections))


class Adapter:
    """Holds the seven agent callbacks and run_protocol in the shape Engine(agents=, protocol=) expects."""

    def __init__(self, vehicle_ids, llm_fn=None):
        """Create callbacks for these vehicle ids; llm_fn replaces real Claude (tests pass fake_llm)."""
        self.llm_fn = llm_fn
        self.engine = None
        self.injections: dict[str, dict[str, str]] = {}  # run_id -> vehicle id -> text
        self.pending: dict[str, set[str]] = {}  # run_id -> incidents to renegotiate though assigned
        self.last_decided: tuple[str, list[str]] = ("", [])
        self.plans: list[dict] = []  # every Plan returned, with run_id, round and tick
        self.agents = {vid: self._make_agent(vid) for vid in vehicle_ids}

    def bind(self, engine) -> None:
        """Give the adapter the engine whose bus signs the protocol's messages and whose log records them."""
        self.engine = engine

    def request(self, run_id: str, incident_id: str) -> None:
        """Ask for an already-assigned incident to be negotiated again (escalation, injection)."""
        self.pending.setdefault(run_id, set()).add(incident_id)

    def clear_decided(self) -> None:
        """Forget renegotiation requests the last committed round handled."""
        run_id, ids = self.last_decided
        self.pending.get(run_id, set()).difference_update(ids)

    def incidents_to_decide(self, world: dict, run_id: str) -> list[str]:
        """Return unresolved incidents no vehicle is heading to, plus any renegotiation requests."""
        targeted = {v["target_incident_id"] for v in world["vehicles"].values()}
        live = [iid for iid, i in world["incidents"].items() if i["status"] != "resolved"]
        wanted = self.pending.get(run_id, set())
        return [iid for iid in live if iid not in targeted or iid in wanted]

    def _make_agent(self, vid: str):
        """Return the engine's per-vehicle callback: records the injection, reports status, no LLM call."""

        async def agent(observation: dict) -> dict:
            """Engine callback for one vehicle; the LLM turns happen inside run_round."""
            run = self.injections.setdefault(observation["run_id"], {})
            if observation.get("injection"):
                run[vid] = observation["injection"]
            else:
                run.pop(vid, None)
            me = observation["self"]
            return {"kind": "status", "payload": {"status": GREG_STATUS[me["status"]],
                                                  "target_incident": me["target_incident_id"]}}

        return agent

    async def run_protocol(self, snapshot: dict, messages: list[dict], context) -> RoundResult:
        """Run one protocol round on the snapshot and return the engine's RoundResult."""
        run_id = snapshot["run_id"]
        ids = self.incidents_to_decide(snapshot, run_id)
        self.last_decided = (run_id, ids)
        if not ids:
            return RoundResult()
        world = to_world(snapshot, self.injections.get(run_id, {}))
        before = len(world.recent_messages)
        plans = await protocol.run_round(world, ids, llm_fn=self.llm_fn)
        sequences = self._publish(world.recent_messages[before:], snapshot)
        self._log_plans(plans, snapshot)
        return RoundResult(
            assignments=self._assignments(snapshot, plans, self.pending.get(run_id, set())),
            trust={vid: v.trust for vid, v in world.vehicles.items()
                   if v.trust != snapshot["vehicles"][vid]["trust"]},
            escalation=self._escalation(plans),
            checks=self._checks(plans, sequences),
        )

    def _publish(self, new_messages: list, snapshot: dict) -> dict[str, tuple[str, int]]:
        """Sign each new protocol message on the engine bus, log it, and return claim id -> (sender, sequence)."""
        sequences = {}
        engine = self.engine
        if engine is None:
            raise RuntimeError("Adapter.bind(engine) must be called before the first round")
        for m in new_messages:
            payload = m.model_dump(mode="json", exclude={"sig"})
            signed = engine.bus.publish(m.vehicle, m.kind, payload, snapshot["round"],
                                        snapshot["time_s"], snapshot["tick"])
            engine.log.write("claim" if m.kind == "claim" else "message", engine.world["time_s"],
                             tick=engine.world["tick"], round=snapshot["round"], message=signed)
            if m.kind == "claim":
                sequences[m.id] = (signed["sender"], signed["sequence"])
        return sequences

    def _log_plans(self, plans: list[Plan], snapshot: dict) -> None:
        """Keep every Plan and write it to the run log (the engine logs only assignments)."""
        dumped = [p.model_dump(mode="json") for p in plans]
        self.plans += [{"run_id": snapshot["run_id"], "round": snapshot["round"],
                        "tick": snapshot["tick"], "plan": p} for p in dumped]
        if self.engine is not None:
            self.engine.log.write("protocol_plans", self.engine.world["time_s"],
                                  tick=self.engine.world["tick"], round=snapshot["round"],
                                  mode=snapshot["mode"], plans=dumped)

    @staticmethod
    def _assignments(snapshot: dict, plans: list[Plan], renegotiated: set[str]) -> dict[str, str | None]:
        """Map plans to vehicle -> incident; vehicles dropped from a renegotiated incident are released."""
        assignments: dict[str, str | None] = {}
        for plan in plans:
            if plan.incident in renegotiated and not plan.escalated:
                for vid, v in snapshot["vehicles"].items():
                    if v["target_incident_id"] == plan.incident and vid not in plan.assigned:
                        assignments[vid] = None
        for plan in plans:
            if not plan.escalated:
                assignments.update({vid: plan.incident for vid in plan.assigned})
        return assignments

    @staticmethod
    def _escalation(plans: list[Plan]) -> dict | None:
        """Return the engine's escalation object for the first escalated plan, or None."""
        for plan in plans:
            if plan.escalated:
                return {"reason": f"no consensus on {plan.incident} ({plan.evidence.get('role')})",
                        "incident": plan.incident, "options": plan.evidence.get("options", []),
                        "evidence": plan.evidence}
        return None

    @staticmethod
    def _checks(plans: list[Plan], sequences: dict[str, tuple[str, int]]) -> list[dict]:
        """Translate protocol checks into the engine's verification records; checks with no verifier are left out."""
        records, seen = [], set()
        for plan in plans:
            for c in plan.checks:
                if c["claim_id"] in seen or c["verifier"] is None or c["claim_id"] not in sequences:
                    continue
                seen.add(c["claim_id"])
                sender, sequence = sequences[c["claim_id"]]
                records.append({"sender": sender, "claim_sequence": sequence, "verifier": c["verifier"],
                                "result": CHECK_RESULT[c["result"]],
                                "reason": f"{c['result']} in protocol round {c['round']}",
                                "claim_id": c["claim_id"], "incident": c["incident"]})
        return records
