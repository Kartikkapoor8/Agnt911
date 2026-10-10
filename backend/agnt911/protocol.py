"""Negotiation protocol: three LLM rounds (propose, counter, vote) and one code round (resolve).

One call to run_round decides every open incident (or the ones listed):
  1. PROPOSE  every vehicle states its status and bids on incidents it is equipped for.
  2. COUNTER  every vehicle reads all bids and claims, objects to wrong ones, and revises its bids.
  3. VOTE     every vehicle votes once per required role of each incident (crash needs an
              ambulance and police, and so on). Options are that role's best bidders with
              capability_match >= 0.5, at most 4.
  4. RESOLVE  plain code, highest severity first: weight votes by trust; more than 60% of
              honest weight wins. A vehicle that wins anywhere leaves every later option list
              and those votes are re-tallied. No winner: severity 1-3 takes the heaviest
              option, severity 4-5 asks a human. A severity 1-2 incident whose role has no
              eligible bids, or whose options were all taken, goes to the next role in its list.
Claims from rounds 1 and 2 go through the trust module's verification hook.
Baseline mode (world.baseline) is the central dispatcher: round 1 only, no checks,
every report believed, best eligible bid per role wins.
All trust decisions live in agnt911.trust; this module only calls it.
"""

import asyncio
import importlib
import logging

from pydantic import Field

from . import llm
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
MIN_CAPABILITY = 0.5
CONSENSUS_SHARE = 0.6
ESCALATE_SEVERITY = 4
SINGLE_ROLE_MAX_SEVERITY = 2
PROPOSE, COUNTER, VOTE, RESOLVE = 1, 2, 3, 4
PHASES = {PROPOSE: "PROPOSE", COUNTER: "COUNTER", VOTE: "VOTE", RESOLVE: "RESOLVE"}
BELIEVED_CLAIMS = ("resolved", "sector_clear", "on_scene")
ROLES = {
    "medical": ["ambulance"],
    "fire": ["fire"],
    "crash": ["ambulance", "police"],
    "hazard": ["fire", "police"],
    "shooting": ["police", "ambulance"],
}

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
    "Round 3, VOTE. For incident {id} (severity {severity}), role {role}, options are {options}. "
    "Vote for one option index. You may vote for yourself. One sentence. "
    "Leave bids empty."
)

Slot = tuple[str, str]  # (incident id, role)


class ProposeOutput(AgentOutput):
    """Round 1 reply: at least one claim about your status, bids optional, no vote."""

    claims: list[Claim] = Field(min_length=1)


class VoteOutput(AgentOutput):
    """Round 3 reply: must carry a vote."""

    vote: Vote


def bid_score(bid: Bid) -> float:
    """Score a bid so higher capability and shorter ETA rank first."""
    return bid.capability_match / (1 + bid.eta_seconds / 60)


def required_roles(incident: Incident) -> list[str]:
    """Return the vehicle types an incident needs: both roles from severity 3 up, else the first."""
    roles = ROLES[incident.type]
    return roles[:1] if incident.severity <= SINGLE_ROLE_MAX_SEVERITY else roles


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
    jobs = [(agents[vid], PROPOSE_TASK, vid) for vid in agents]
    outputs = await _ask_all(world, jobs, ProposeOutput, calls=incidents)  # calls are part of each view
    claims, bids = _record_round(world, outputs, PROPOSE)

    # Baseline: the central dispatcher stops here, with no checks and no debate
    if world.baseline:
        options = _all_options(world, bids, incidents)
        return _resolve_all(world, incidents, bids, options, {}, claims, [])
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

    # Round 3: VOTE, one call per required role per incident per vehicle, all in one gather
    options = _all_options(world, bids, incidents)
    jobs = [
        (agents[vid], _vote_task(world, world.incidents[slot[0]], slot[1], opts), slot)
        for slot, opts in options.items() if opts
        for vid in agents
    ]
    outputs = await _ask_all(world, jobs, VoteOutput)
    votes = _record_votes(world, outputs, options)

    # Round 4: RESOLVE (no LLM)
    return _resolve_all(world, incidents, bids, options, votes, claims, checks)


def _incidents_to_decide(world: World, incident_ids: list[str] | None) -> list[Incident]:
    """Return the incidents to negotiate: the ones named, or every open one."""
    if incident_ids is None:
        return [i for i in world.incidents.values() if i.status == "open"]
    missing = [i for i in incident_ids if i not in world.incidents]
    if missing:
        raise ValueError(f"unknown incident ids: {missing}")
    return [world.incidents[i] for i in incident_ids]


async def _ask_all(
    world: World,
    jobs: list[tuple[VehicleAgent, str, object]],
    response_model: type[AgentOutput],
    calls: list[Incident] | None = None,
) -> list[tuple[str, object, AgentOutput | None]]:
    """Run every (agent, task, tag) job in one parallel gather; a failed call yields None.

    calls are the broadcast 911 calls added to each vehicle's view. Hitting the LLM call limit
    stops the whole round instead of being treated as one vehicle's failure.
    """

    async def one(agent: VehicleAgent, task: str, tag: object):
        try:
            out = await asyncio.to_thread(agent.act, world, task, response_model, calls)
        except llm.CallLimitExceeded:
            raise
        except Exception as exc:  # one broken vehicle must not stop the fleet
            log.warning("%s gave no usable reply (%s): %s", agent.vehicle_id, tag, exc)
            out = None
        return agent.vehicle_id, tag, out

    return list(await asyncio.gather(*(one(*job) for job in jobs)))


def _record_round(
    world: World,
    outputs: list[tuple[str, object, AgentOutput | None]],
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
                "round": round_no, "sig": "", "compromised": vid in world.injections,
            })
            world.recent_messages.append(claim)
            claims.append(claim)
        for bid in out.bids:
            if bid.incident not in world.incidents:
                log.warning("%s bid on unknown incident %s; dropped", vid, bid.incident)
                continue
            bid = bid.model_copy(update={"vehicle": vid, "round": round_no, "sig": "",
                                         "compromised": vid in world.injections})
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


def _all_options(
    world: World, bids: dict[tuple[str, str], Bid], incidents: list[Incident],
) -> dict[Slot, list[Bid]]:
    """Return the option list for every (incident, role) slot, in incident then role order.

    A severity 1-2 incident needs one role. If its first role has no eligible bids, the next
    role in its list that does takes the slot instead (a minor hazard with no fire engine
    goes to police). If none do, the first role keeps its empty slot and takes the no-bids path.
    """
    slots: dict[Slot, list[Bid]] = {}
    for inc in incidents:
        roles = required_roles(inc)
        if len(roles) == 1:
            candidates = [(role, _options(world, bids, inc.id, role)) for role in ROLES[inc.type]]
            role, opts = next(((r, o) for r, o in candidates if o), candidates[0])
            slots[(inc.id, role)] = opts
        else:
            slots.update({(inc.id, role): _options(world, bids, inc.id, role) for role in roles})
    return slots


def _options(world: World, bids: dict[tuple[str, str], Bid], incident_id: str, role: str) -> list[Bid]:
    """Return the best eligible bids for one role of an incident: right type, capability >= 0.5, at most 4.

    Untrusted vehicles are left out, except in baseline where the dispatcher believes everyone.
    A vehicle already committed to a different incident by an earlier round is left out too.
    """
    candidates = [
        b for (vid, iid), b in bids.items()
        if iid == incident_id
        and world.vehicles[vid].type == role
        and b.capability_match >= MIN_CAPABILITY
        and (world.baseline or not world.vehicles[vid].untrusted)
        and world.vehicles[vid].target_incident in (None, incident_id)
    ]
    candidates.sort(key=lambda b: (-bid_score(b), b.eta_seconds, b.vehicle))
    return candidates[:MAX_OPTIONS]


def _vote_task(world: World, incident: Incident, role: str, options: list[Bid]) -> str:
    """Write the round-3 task for one role of one incident, listing each option with its ETA and capability."""
    listed = "; ".join(
        f"{n}: {b.vehicle} ({world.vehicles[b.vehicle].type}, ETA {b.eta_seconds} s, "
        f"capability {b.capability_match:.2f}, district cost {b.cost_to_district:.2f})"
        for n, b in enumerate(options)
    )
    return VOTE_TASK.format(id=incident.id, severity=incident.severity, role=role, options=f"[{listed}]")


def _record_votes(
    world: World,
    outputs: list[tuple[str, Slot, AgentOutput | None]],
    options: dict[Slot, list[Bid]],
) -> dict[Slot, list[Vote]]:
    """Stamp each valid vote with sender, incident, role and round, append it to the log, and group by slot."""
    votes: dict[Slot, list[Vote]] = {}
    for vid, slot, out in outputs:
        if out is None or out.vote is None:
            continue
        if out.vote.option_index >= len(options[slot]):
            log.warning("%s voted for missing option %d on %s; dropped", vid, out.vote.option_index, slot)
            continue
        vote = out.vote.model_copy(update={
            "vehicle": vid, "incident": slot[0], "role": slot[1], "round": VOTE, "sig": "",
            "compromised": vid in world.injections,
        })
        world.recent_messages.append(vote)
        votes.setdefault(slot, []).append(vote)
    return votes


def _resolve_all(
    world: World,
    incidents: list[Incident],
    bids: dict[tuple[str, str], Bid],
    options: dict[Slot, list[Bid]],
    votes: dict[Slot, list[Vote]],
    claims: list[Claim],
    checks: list[dict],
) -> list[Plan]:
    """Resolve incidents highest severity first so no vehicle wins twice; return plans in the original order."""
    taken: set[str] = set()
    plans: dict[str, Plan] = {}
    for incident in sorted(incidents, key=lambda i: -i.severity):  # stable: ties keep incident order
        plan = _resolve(world, incident, bids, options, votes, claims, checks, taken)
        taken.update(plan.assigned)
        plans[incident.id] = plan
    return [plans[i.id] for i in incidents]


def _resolve(
    world: World,
    incident: Incident,
    bids: dict[tuple[str, str], Bid],
    options: dict[Slot, list[Bid]],
    votes: dict[Slot, list[Vote]],
    claims: list[Claim],
    checks: list[dict],
    taken: set[str],
) -> Plan:
    """Fill each required role of one incident from the options still free, and return its Plan."""
    plan = Plan(
        incident=incident.id,
        round_reached=PROPOSE if world.baseline else VOTE,
        consensus=False,
        escalated=False,
        checks=_checks_for(incident.id, checks),
    )
    if world.baseline:
        reports = [c for c in claims if c.incident == incident.id and c.type in BELIEVED_CLAIMS]
        if reports:
            report = reports[-1]
            assigned = [report.vehicle] if report.type == "on_scene" else []
            return plan.model_copy(update={"assigned": assigned, "evidence": {"believed": report.model_dump()}})

    assigned, role_consensus, escalations = [], [], []
    tally_all: dict[str, float] = {}
    voted_on: list[str] = []  # every role's options as voted on, so a vote's option_index stays readable
    for slot in [s for s in options if s[0] == incident.id]:  # the slots actually built, fallbacks included
        role = slot[1]
        free = [b for b in options[slot] if b.vehicle not in taken]
        voted_on += [b.vehicle for b in options[slot]]
        if world.baseline:
            assigned += [free[0].vehicle] if free else []
            role_consensus.append(False)
            continue
        winner, consensus, tally, escalation = _resolve_role(
            world, incident, role, options[slot], free, votes.get(slot, []), claims, checks)
        tally_all.update(tally)
        role_consensus.append(consensus)
        if escalation:
            escalations.append(escalation)
        elif winner:
            assigned.append(winner)

    evidence = None
    if escalations:
        evidence = {**escalations[0], "also": escalations[1:]} if len(escalations) > 1 else escalations[0]
    elif not assigned and incident.severity <= SINGLE_ROLE_MAX_SEVERITY:
        fallback = _resolve_time_fallback(world, incident, bids, options, taken)
        if fallback:
            assigned, role_consensus, evidence = [fallback["vehicle"]], [False], {"fallback": fallback}
    return plan.model_copy(update={
        "assigned": assigned,
        "consensus": bool(role_consensus) and all(role_consensus),
        "escalated": bool(escalations),
        "options": voted_on,
        "tally": tally_all,
        "evidence": evidence,
    })


def _resolve_time_fallback(
    world: World,
    incident: Incident,
    bids: dict[tuple[str, str], Bid],
    options: dict[Slot, list[Bid]],
    taken: set[str],
) -> dict | None:
    """For a minor incident left empty at resolve time, take the best free bid of the next role in its list.

    Nobody voted on this vehicle, so the plan carries no consensus. Returns {vehicle, role, reason} or None.
    """
    used = next(role for iid, role in options if iid == incident.id)
    roles = ROLES[incident.type]
    reason = (f"every {used} option was assigned to a higher-severity incident"
              if options[(incident.id, used)] else f"no eligible {used} bids")
    for role in roles[roles.index(used) + 1:]:
        free = [b for b in _options(world, bids, incident.id, role) if b.vehicle not in taken]
        if free:
            return {"vehicle": free[0].vehicle, "role": role, "reason": reason}
    return None


def _resolve_role(
    world: World,
    incident: Incident,
    role: str,
    voted_on: list[Bid],
    free: list[Bid],
    votes: list[Vote],
    claims: list[Claim],
    checks: list[dict],
) -> tuple[str | None, bool, dict[str, float], dict | None]:
    """Tally one role's votes among the options still free and return (winner, consensus, tally, escalation)."""
    tally = {b.vehicle: 0.0 for b in free}
    for vote in votes:
        choice = voted_on[vote.option_index].vehicle
        if choice in tally:  # a vote for a vehicle already taken elsewhere no longer counts
            tally[choice] += trust.vote_weight(world.vehicles[vote.vehicle])
    honest_total = sum(trust.vote_weight(v) for v in world.vehicles.values() if not v.untrusted)
    threshold = CONSENSUS_SHARE * honest_total
    # max() keeps the first of equal weights, and options are already best-bid first
    leader = max(tally, key=lambda vid: tally[vid]) if tally else None

    if leader is not None and honest_total > 0 and tally[leader] > threshold:
        return leader, True, tally, None
    if incident.severity >= ESCALATE_SEVERITY:
        decision = trust.should_escalate({
            "incident": incident.id,
            "severity": incident.severity,
            "role": role,
            "round": RESOLVE,
            "options": [b.model_dump() for b in free],
            "tally": tally,
            "honest_total": honest_total,
            "threshold": threshold,
            "votes": [v.model_dump() for v in votes],
            "contradicting_claims": _contradicting_claims(incident.id, claims, checks),
        })
        if decision:
            return None, False, tally, {"role": role, **decision}
    return leader, False, tally, None


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
