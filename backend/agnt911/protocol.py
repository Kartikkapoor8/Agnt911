"""Negotiation protocol: three LLM rounds (propose, counter, vote) and one code round (resolve).

One call to run_round decides every open incident (or the ones listed):
  1. PROPOSE  every vehicle states its status and bids on incidents it is equipped for.
  2. COUNTER  every vehicle reads all bids and claims, objects to wrong ones, and revises its bids.
  3. VOTE     every vehicle votes on each incident's options (the best bidders, at most 4).
  4. RESOLVE  plain code: weight votes by trust; more than 60% of honest weight wins.
              No winner: severity 1-3 takes the heaviest option, severity 4-5 asks a human.
Claims from rounds 1 and 2 go through the trust module's verification hook.
Baseline mode (world.baseline) is the central dispatcher: round 1 only, no checks,
every report believed, best bid wins.
All trust decisions live in agnt911.trust; this module only calls it.
"""

import asyncio
import importlib
import logging

from pydantic import Field

from .agents import VIEW_RADIUS_M, LLMFn, VehicleAgent, haversine_m
from .models import AgentOutput, Bid, Claim, Incident, Plan, Vehicle, Vote, World

try:
    trust = importlib.import_module(f"{__package__}.trust")
except ModuleNotFoundError as exc:
    if exc.name != f"{__package__}.trust":
        raise  # trust.py exists but is broken: fail loudly, don't silently use the stub
    trust = importlib.import_module(f"{__package__}.trust_stub")

log = logging.getLogger(__name__)

MAX_ROUNDS = 4
MAX_OPTIONS = 4
CONSENSUS_SHARE = 0.6
ESCALATE_SEVERITY = 4
PROPOSE, COUNTER, VOTE, RESOLVE = 1, 2, 3, 4
PHASES = {PROPOSE: "PROPOSE", COUNTER: "COUNTER", VOTE: "VOTE", RESOLVE: "RESOLVE"}
BELIEVED_CLAIMS = ("resolved", "sector_clear", "on_scene")

PROPOSE_TASK = (
    "Round 1, PROPOSE. List open incidents you can see or that appear in the message log. "
    "For each one you are equipped for, submit one Bid. "
    "Emit claims about your own status (available, en_route, or on_scene) and any observation. "
    "Leave vote null."
)
COUNTER_TASK = (
    "Round 2, COUNTER. Here are every vehicle's bids and claims (in the shared message log). "
    "Say which bid is wrong and why: farther vehicle claiming closer, wrong equipment, "
    "leaves a district uncovered, or a claim that contradicts what you can see. "
    "Revise your own bid if needed, and return your current bids again even if unchanged. "
    "Emit an observation claim for anything you see that contradicts another vehicle's claim. "
    "Leave vote null."
)
VOTE_TASK = (
    "Round 3, VOTE. For incident {id} (severity {severity}), options are {options}. "
    "Vote for one option index. You may vote for yourself. One sentence. "
    "Leave bids empty."
)


class ProposeOutput(AgentOutput):
    """Round 1 reply: at least one claim about your status, bids optional, no vote."""

    claims: list[Claim] = Field(min_length=1)


class VoteOutput(AgentOutput):
    """Round 3 reply: must carry a vote."""

    vote: Vote


def bid_score(bid: Bid) -> float:
    """Score a bid so higher capability and shorter ETA rank first."""
    return bid.capability_match / (1 + bid.eta_seconds / 60)


async def run_round(
    world: World,
    incident_ids: list[str] | None = None,
    llm_fn: LLMFn | None = None,
) -> list[Plan]:
    """Negotiate every open incident (or only incident_ids) and return one Plan per incident.

    Mutates world: appends every claim, bid and vote to world.recent_messages in order,
    and stores trust updates on world.vehicles. Does not assign vehicles; the engine
    writes the returned plans back into the world.
    """
    incidents = _incidents_to_decide(world, incident_ids)
    if not incidents:
        return []
    agents = {vid: VehicleAgent(vid, llm_fn=llm_fn) for vid in world.vehicles}

    # Round 1: PROPOSE
    jobs = [(agents[vid], PROPOSE_TASK + "\n\n" + _call_board(world, incidents, vid), vid)
            for vid in agents]
    outputs = await _ask_all(world, jobs, ProposeOutput)
    claims, bids = _record_round(world, outputs, PROPOSE)

    # Baseline: the central dispatcher stops here, with no checks and no debate
    if world.baseline:
        return [_baseline_plan(inc, bids, claims) for inc in incidents]
    checks = _verify(world, claims, PROPOSE)

    # Round 2: COUNTER
    jobs = [(agents[vid], COUNTER_TASK, vid) for vid in agents]
    outputs = await _ask_all(world, jobs, AgentOutput)
    counter_claims, counter_bids = _record_round(world, outputs, COUNTER)
    for vid in {b.vehicle for b in counter_bids.values()}:
        bids = {key: b for key, b in bids.items() if key[0] != vid}
    bids.update(counter_bids)
    checks += _verify(world, counter_claims, COUNTER)
    claims += counter_claims

    # Round 3: VOTE, one call per incident per vehicle, all in one gather
    options = {inc.id: _options(world, bids, inc.id) for inc in incidents}
    jobs = [
        (agents[vid], _vote_task(world, inc, options[inc.id]), inc.id)
        for inc in incidents if options[inc.id]
        for vid in agents
    ]
    outputs = await _ask_all(world, jobs, VoteOutput)
    votes = _record_votes(world, outputs, options)

    # Round 4: RESOLVE (no LLM)
    return [
        _resolve(world, inc, options[inc.id], votes.get(inc.id, []), claims, checks)
        for inc in incidents
    ]


def _incidents_to_decide(world: World, incident_ids: list[str] | None) -> list[Incident]:
    """Return the incidents to negotiate: the ones named, or every open one."""
    if incident_ids is None:
        return [i for i in world.incidents.values() if i.status == "open"]
    missing = [i for i in incident_ids if i not in world.incidents]
    if missing:
        raise ValueError(f"unknown incident ids: {missing}")
    return [world.incidents[i] for i in incident_ids]


def _call_board(world: World, incidents: list[Incident], vehicle_id: str) -> str:
    """List the 911 calls being decided, with distance from this vehicle, for the round-1 task."""
    me = world.vehicles[vehicle_id]
    lines = [
        f"- {i.id}: {i.type}, severity {i.severity}, at {i.location.lat:.4f},{i.location.lng:.4f}, "
        f"{round(haversine_m(me.location, i.location))} m from you"
        for i in incidents
    ]
    return "Open 911 calls (broadcast to every vehicle):\n" + "\n".join(lines)


async def _ask_all(
    world: World,
    jobs: list[tuple[VehicleAgent, str, str]],
    response_model: type[AgentOutput],
) -> list[tuple[str, str, AgentOutput | None]]:
    """Run every (agent, task, tag) job in one parallel gather; a failed call yields None."""

    async def one(agent: VehicleAgent, task: str, tag: str):
        try:
            out = await asyncio.to_thread(agent.act, world, task, response_model)
        except Exception as exc:  # one broken vehicle must not stop the fleet
            log.warning("%s gave no usable reply (%s): %s", agent.vehicle_id, tag, exc)
            out = None
        return agent.vehicle_id, tag, out

    return list(await asyncio.gather(*(one(*job) for job in jobs)))


def _record_round(
    world: World,
    outputs: list[tuple[str, str, AgentOutput | None]],
    round_no: int,
) -> tuple[list[Claim], dict[tuple[str, str], Bid]]:
    """Stamp each vehicle's claims and bids with sender and round, append them to the log, and return them."""
    claims: list[Claim] = []
    bids: dict[tuple[str, str], Bid] = {}
    for vid, _tag, out in outputs:
        if out is None:
            continue
        for n, claim in enumerate(out.claims, 1):
            claim = claim.model_copy(update={
                "id": f"{vid}-r{round_no}-c{n}", "tick": world.tick, "vehicle": vid,
                "round": round_no, "sig": "",
            })
            world.recent_messages.append(claim)
            claims.append(claim)
        for bid in out.bids:
            if bid.incident not in world.incidents:
                log.warning("%s bid on unknown incident %s; dropped", vid, bid.incident)
                continue
            bid = bid.model_copy(update={"vehicle": vid, "round": round_no, "sig": ""})
            world.recent_messages.append(bid)
            bids[(vid, bid.incident)] = bid
    return claims, bids


def _verify(world: World, claims: list[Claim], round_no: int) -> list[dict]:
    """Run the trust module's check on every claim it wants verified and store the trust change."""
    checks = []
    for claim in claims:
        sender = world.vehicles.get(claim.vehicle)
        if sender is None or not trust.should_verify(claim, sender, round_no):
            continue
        result = trust.check_claim(claim, world)
        updated = trust.update_trust(sender, result)
        if isinstance(updated, Vehicle):
            world.vehicles[sender.id] = updated
        checks.append({
            "claim_id": claim.id,
            "verifier": _verifier(world, claim),
            "result": result,
            "vehicle": claim.vehicle,
            "incident": claim.incident,
            "round": round_no,
        })
    return checks


def _verifier(world: World, claim: Claim) -> str | None:
    """Name the vehicle placed to check a claim: nearest police within 500 m, else nearest vehicle, else None."""
    incident = world.incidents.get(claim.incident) if claim.incident else None
    spot = incident.location if incident else claim.location
    near = [
        (v.type != "police", haversine_m(v.location, spot), v.id)
        for v in world.vehicles.values()
        if v.id != claim.vehicle and haversine_m(v.location, spot) <= VIEW_RADIUS_M
    ]
    return min(near)[2] if near else None


def _options(world: World, bids: dict[tuple[str, str], Bid], incident_id: str) -> list[Bid]:
    """Return the best bids for an incident, one per trusted vehicle, at most MAX_OPTIONS."""
    candidates = [
        b for (vid, iid), b in bids.items()
        if iid == incident_id and not world.vehicles[vid].untrusted
    ]
    candidates.sort(key=lambda b: (-bid_score(b), b.eta_seconds, b.vehicle))
    return candidates[:MAX_OPTIONS]


def _vote_task(world: World, incident: Incident, options: list[Bid]) -> str:
    """Write the round-3 task for one incident, listing each option with its ETA and capability."""
    listed = "; ".join(
        f"{n}: {b.vehicle} ({world.vehicles[b.vehicle].type}, ETA {b.eta_seconds} s, "
        f"capability {b.capability_match:.2f}, district cost {b.cost_to_district:.2f})"
        for n, b in enumerate(options)
    )
    return VOTE_TASK.format(id=incident.id, severity=incident.severity, options=f"[{listed}]")


def _record_votes(
    world: World,
    outputs: list[tuple[str, str, AgentOutput | None]],
    options: dict[str, list[Bid]],
) -> dict[str, list[Vote]]:
    """Stamp each valid vote with sender, incident and round, append it to the log, and group by incident."""
    votes: dict[str, list[Vote]] = {}
    for vid, incident_id, out in outputs:
        if out is None or out.vote is None:
            continue
        if out.vote.option_index >= len(options[incident_id]):
            log.warning("%s voted for missing option %d on %s; dropped",
                        vid, out.vote.option_index, incident_id)
            continue
        vote = out.vote.model_copy(update={
            "vehicle": vid, "incident": incident_id, "round": VOTE, "sig": "",
        })
        world.recent_messages.append(vote)
        votes.setdefault(incident_id, []).append(vote)
    return votes


def _resolve(
    world: World,
    incident: Incident,
    options: list[Bid],
    votes: list[Vote],
    claims: list[Claim],
    checks: list[dict],
) -> Plan:
    """Tally trust-weighted votes for one incident and turn the result into a Plan."""
    tally = {b.vehicle: 0.0 for b in options}
    for vote in votes:
        tally[options[vote.option_index].vehicle] += trust.vote_weight(world.vehicles[vote.vehicle])
    honest_total = sum(trust.vote_weight(v) for v in world.vehicles.values() if not v.untrusted)
    threshold = CONSENSUS_SHARE * honest_total
    # max() keeps the first of equal weights, and options are already best-bid first
    leader = max(tally, key=lambda vid: tally[vid]) if tally else None

    plan = Plan(
        incident=incident.id,
        round_reached=VOTE,
        consensus=False,
        escalated=False,
        options=[b.vehicle for b in options],
        tally=tally,
        checks=_checks_for(incident.id, checks),
    )
    if leader is not None and honest_total > 0 and tally[leader] > threshold:
        return plan.model_copy(update={"assigned": [leader], "consensus": True})
    if incident.severity >= ESCALATE_SEVERITY:
        decision = trust.should_escalate({
            "incident": incident.id,
            "severity": incident.severity,
            "round": RESOLVE,
            "options": [b.model_dump() for b in options],
            "tally": tally,
            "honest_total": honest_total,
            "threshold": threshold,
            "votes": [v.model_dump() for v in votes],
            "contradicting_claims": _contradicting_claims(incident.id, claims, checks),
        })
        if decision:
            return plan.model_copy(update={"escalated": True, "evidence": decision})
    return plan.model_copy(update={"assigned": [leader] if leader else []})


def _checks_for(incident_id: str, checks: list[dict]) -> list[dict]:
    """Return the checks about this incident plus those not tied to any incident."""
    return [c for c in checks if c["incident"] in (incident_id, None)]


def _contradicting_claims(incident_id: str, claims: list[Claim], checks: list[dict]) -> list[dict]:
    """Return observation claims about the incident and any claim a check found contradicted."""
    contradicted = {c["claim_id"] for c in checks if c["result"] == "contradicted"}
    return [
        c.model_dump() for c in claims
        if c.id in contradicted or (c.type == "observation" and c.incident == incident_id)
    ]


def _baseline_plan(incident: Incident, bids: dict[tuple[str, str], Bid], claims: list[Claim]) -> Plan:
    """Central-dispatcher plan: believe every report, else send the single best bid."""
    reports = [c for c in claims if c.incident == incident.id and c.type in BELIEVED_CLAIMS]
    plan = Plan(incident=incident.id, round_reached=PROPOSE, consensus=False, escalated=False)
    if reports:
        report = reports[-1]
        assigned = [report.vehicle] if report.type == "on_scene" else []
        return plan.model_copy(update={"assigned": assigned, "evidence": {"believed": report.model_dump()}})
    ranked = sorted(
        (b for (_vid, iid), b in bids.items() if iid == incident.id),
        key=lambda b: (-bid_score(b), b.eta_seconds, b.vehicle),
    )
    return plan.model_copy(update={
        "assigned": [ranked[0].vehicle] if ranked else [],
        "options": [b.vehicle for b in ranked[:MAX_OPTIONS]],
    })
