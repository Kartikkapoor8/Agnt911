"""Negotiation protocol: three LLM rounds (propose, counter, vote) and one code round (resolve).

One call to run_round decides every open incident (or the ones listed):
  1. PROPOSE  every vehicle states its status and bids on incidents it is equipped for.
  2. COUNTER  every vehicle reads all bids and claims, objects to wrong ones, and revises its bids.
  3. VOTE     every vehicle votes once per required role of each incident (crash needs an
              ambulance and police, and so on). Options are that role's best bidders with
              capability_match >= 0.5, at most 4.
  4. RESOLVE  plain code, highest severity first: weight votes by trust; more than 60% of
              honest weight wins. A vehicle that wins anywhere leaves every later option list
              and those votes are re-tallied. No winner: the heaviest option goes. A role with
              no free option keeps the vehicle already on its way; failing that the fleet pulls
              the trusted vehicle of that role it can best spare (free first, then the one on
              the lowest-severity call below this one) and that call is negotiated again. A
              severity 1-2 incident with nobody left goes to the next role in its list. Only a
              severity 5 call whose every trusted vehicle of the role holds another severity 5
              call it would leave uncovered asks a human, and never one a human already settled.
              Untrusted vehicles are never pulled and never offered.
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
from .events import emit
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
DEADLOCK_SEVERITY = 5  # only a call this severe can stop for a human, and only against calls as severe
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
    for bid in bids.values():
        _emit_bid(world, bid, revised=False)

    # Baseline: the central dispatcher stops here, with no checks and no debate
    if world.baseline:
        options = _all_options(world, bids, incidents)
        return _resolve_all(world, incidents, bids, options, {}, claims, [])
    checks = _verify(world, claims, PROPOSE)
    disproven = _verify_bids(world, list(bids.values()), PROPOSE)

    # Round 2: COUNTER
    jobs = [(agents[vid], COUNTER_TASK, vid) for vid in agents]
    outputs = await _ask_all(world, jobs, AgentOutput)
    counter_claims, counter_bids = _record_round(world, outputs, COUNTER)
    for key, bid in counter_bids.items():
        old = bids.get(key)
        if old is None or (old.eta_seconds, old.capability_match) != (bid.eta_seconds, bid.capability_match):
            _emit_bid(world, bid, revised=old is not None)
    for vid in {b.vehicle for b in counter_bids.values()}:
        for key in [k for k in bids if k[0] == vid and k not in counter_bids]:
            emit(world, "bid_withdrawn", vehicle=vid, incident=key[1], round=COUNTER)
        bids = {key: b for key, b in bids.items() if key[0] != vid}
    bids.update(counter_bids)
    checks += _verify(world, counter_claims, COUNTER)
    # A bid restated in round 2 is judged by its round-2 check; untouched round-1 bids keep theirs.
    disproven = (disproven - set(counter_bids)) | _verify_bids(world, list(counter_bids.values()), COUNTER)
    claims += counter_claims

    # Round 3: VOTE, one call per required role per incident per vehicle, all in one gather.
    # A bid the trust module contradicted is not put to the vote.
    options = _all_options(world, {k: b for k, b in bids.items() if k not in disproven}, incidents)
    jobs = [
        (agents[vid], _vote_task(world, world.incidents[slot[0]], slot[1], opts), slot)
        for slot, opts in options.items() if opts
        for vid in agents
    ]
    outputs = await _ask_all(world, jobs, VoteOutput)
    votes = _record_votes(world, outputs, options)

    # Round 4: RESOLVE (no LLM)
    return _resolve_all(world, incidents, bids, options, votes, claims, checks, disproven)


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
        check = {
            "claim_id": claim.id,
            "verifier": _verifier(world, claim),
            "result": result,
            "vehicle": claim.vehicle,
            "incident": claim.incident,
            "round": round_no,
        }
        checks.append(check)
        _emit_check(world, claim, check, before=sender, after=world.vehicles[sender.id])
    return checks


def _bid_as_claim(world: World, bid: Bid) -> Claim:
    """Present a bid to the trust module as what it asserts: an observation of that incident, with its ETA."""
    return Claim(id=f"{bid.vehicle}-r{bid.round}-bid-{bid.incident}", tick=world.tick, vehicle=bid.vehicle,
                 type="observation", incident=bid.incident, location=world.vehicles[bid.vehicle].location,
                 # value.vehicle names the bidder, so trust checks the ETA against the bidder's own position
                 value={"eta_seconds": bid.eta_seconds, "vehicle": bid.vehicle}, confidence=bid.confidence,
                 reasoning=bid.reasoning,
                 round=bid.round, compromised=bid.compromised)


def _verify_bids(world: World, bids: list[Bid], round_no: int) -> set[tuple[str, str]]:
    """Check every bid's ETA with the trust module (an ETA faster than any vehicle can drive is a lie).

    Returns the (vehicle, incident) keys of contradicted bids. Results go to trust and the event stream
    only: plan.checks must match signed claims in the log, and a bid is not a claim message.
    """
    disproven = set()
    for bid in bids:
        sender = world.vehicles.get(bid.vehicle)
        claim = _bid_as_claim(world, bid)
        if sender is None or not trust.should_verify(claim, sender, round_no):
            continue
        result = trust.check_claim(claim, world)
        updated = trust.update_trust(sender, result)
        if isinstance(updated, Vehicle):
            world.vehicles[sender.id] = updated
        check = {"claim_id": claim.id, "verifier": _verifier(world, claim), "result": result}
        _emit_check(world, claim, check, before=sender, after=world.vehicles[sender.id],
                    subject="bid", eta_seconds=bid.eta_seconds)
        if result == "contradicted":
            disproven.add((bid.vehicle, bid.incident))
    return disproven


def _emit_bid(world: World, bid: Bid, revised: bool) -> None:
    """Emit bid_placed for a new bid (round 1) or a changed one (round 2)."""
    emit(world, "bid_placed", vehicle=bid.vehicle, incident=bid.incident, round=bid.round, revised=revised,
         eta_seconds=bid.eta_seconds, capability_match=bid.capability_match,
         cost_to_district=bid.cost_to_district)


def _emit_check(world: World, claim: Claim, check: dict, before: Vehicle, after: Vehicle,
                subject: str = "claim", **extra) -> None:
    """Emit claim_verified, then trust_changed and any untrusted flip that the check caused."""
    emit(world, "claim_verified", vehicle=claim.vehicle, incident=claim.incident, claim_id=check["claim_id"],
         verifier=check["verifier"], result=check["result"], claim_type=claim.type, subject=subject, **extra)
    if after.trust != before.trust:
        emit(world, "trust_changed", vehicle=claim.vehicle, incident=claim.incident, old=before.trust,
             new=after.trust, reason=f"{check['result']} {subject} {check['claim_id']}")
    if after.untrusted and not before.untrusted:
        emit(world, "vehicle_untrusted", vehicle=claim.vehicle, trust=after.trust)
    elif before.untrusted and not after.untrusted:
        emit(world, "vehicle_trusted_again", vehicle=claim.vehicle, trust=after.trust)


def _verifier(world: World, claim: Claim) -> str | None:
    """Name the vehicle that checks a claim: trust.witness if the trust module has one, else nearest police within 500 m, else nearest vehicle."""
    if hasattr(trust, "witness"):  # the real trust module names the vehicle whose view it judged by
        return trust.witness(claim, world)
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
        emit(world, "vote_cast", vehicle=vid, incident=slot[0], role=slot[1],
             option_index=vote.option_index, choice=options[slot][vote.option_index].vehicle)
    return votes


def _resolve_all(
    world: World,
    incidents: list[Incident],
    bids: dict[tuple[str, str], Bid],
    options: dict[Slot, list[Bid]],
    votes: dict[Slot, list[Vote]],
    claims: list[Claim],
    checks: list[dict],
    disproven: set[tuple[str, str]] = frozenset(),
) -> list[Plan]:
    """Resolve incidents highest severity first so no vehicle wins twice; return plans in the original order."""
    taken: dict[str, str] = {}  # vehicle id -> the incident that won it earlier in this round
    plans: dict[str, Plan] = {}
    settled = settled_by_human(world)
    for incident in sorted(incidents, key=lambda i: -i.severity):  # stable: ties keep incident order
        plan = _resolve(world, incident, bids, options, votes, claims, checks, taken, disproven, settled)
        taken.update(dict.fromkeys(plan.assigned, incident.id))
        _emit_plan(world, plan)
        plans[incident.id] = plan
    return [plans[i.id] for i in incidents]


def _emit_plan(world: World, plan: Plan) -> None:
    """Emit one event per plan: escalated_to_human, plan_committed (consensus or dispatcher), or plan_no_consensus."""
    if plan.escalated:
        emit(world, "escalated_to_human", incident=plan.incident, options=plan.evidence.get("options", []),
             evidence=plan.evidence.get("evidence", {}), role=plan.evidence.get("role"),
             contested=plan.evidence.get("contested", {}))
        return
    kind = "plan_committed" if plan.consensus or world.baseline else "plan_no_consensus"
    evidence = plan.evidence or {}
    emit(world, kind, incident=plan.incident, assigned=plan.assigned, tally=plan.tally,
         round_reached=plan.round_reached, baseline=world.baseline,
         fallback=evidence.get("fallback"), believed=bool(evidence.get("believed")),
         kept=evidence.get("kept"), reason=evidence.get("reason"), pulled=evidence.get("pulled"))


def _resolve(
    world: World,
    incident: Incident,
    bids: dict[tuple[str, str], Bid],
    options: dict[Slot, list[Bid]],
    votes: dict[Slot, list[Vote]],
    claims: list[Claim],
    checks: list[dict],
    taken: dict[str, str],
    disproven: set[tuple[str, str]] = frozenset(),
    settled: set[str] = frozenset(),
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

    assigned, role_consensus, escalations, kept, pulled = [], [], [], {}, []
    tally_all: dict[str, float] = {}
    voted_on: list[str] = []  # every role's options as voted on, so a vote's option_index stays readable
    for slot in [s for s in options if s[0] == incident.id]:  # the slots actually built, fallbacks included
        role = slot[1]
        free = [b for b in options[slot] if b.vehicle not in taken]
        voted_on += [b.vehicle for b in options[slot]]
        if world.baseline:
            winner, consensus = (free[0].vehicle if free else None), False
        else:
            winner, consensus, tally = _resolve_role(world, options[slot], free, votes.get(slot, []))
            tally_all.update(tally)
        if winner:
            assigned.append(winner)
        elif keep := _incumbent(world, incident, role, taken):
            # Nobody new to send: the vehicle already on its way keeps the role (nothing to disagree about).
            assigned.append(keep)
            kept[role], consensus = keep, not world.baseline
        elif world.baseline:
            pass  # the central dispatcher only sends bidders
        elif pull := _pull(world, incident, role, taken, assigned, bids, disproven):
            # The fleet's own rule, not a vote: nothing to disagree about.
            assigned.append(pull["vehicle"])
            pulled.append(pull)
            consensus = True
        elif escalation := _deadlock(world, incident, role, taken, settled, options[slot], votes.get(slot, []),
                                     claims, checks):
            escalations.append(escalation)
        role_consensus.append(consensus)

    evidence = None
    if escalations:
        evidence = {**escalations[0], "also": escalations[1:]} if len(escalations) > 1 else escalations[0]
    elif not assigned and incident.severity <= SINGLE_ROLE_MAX_SEVERITY:
        fallback = _resolve_time_fallback(world, incident, bids, options, taken)
        if fallback:
            assigned, role_consensus, evidence = [fallback["vehicle"]], [False], {"fallback": fallback}
    if kept:
        evidence = {**(evidence or {}), "kept": kept}
    if pulled:
        evidence = {**(evidence or {}), "pulled": pulled, "reason": _pull_reason(pulled)}
    return plan.model_copy(update={
        "assigned": assigned,
        "consensus": bool(role_consensus) and all(role_consensus),
        "escalated": bool(escalations),
        "options": voted_on,
        "tally": tally_all,
        "evidence": evidence,
    })


def _incumbent(world: World, incident: Incident, role: str, taken: dict[str, str]) -> str | None:
    """Return the trusted vehicle of this role already en route to or on scene at the incident, if it is still free to keep."""
    return next((v.id for v in sorted(world.vehicles.values(), key=lambda v: v.id)
                 if v.type == role and v.target_incident == incident.id and v.status in ("en_route", "on_scene")
                 and not v.untrusted and v.id not in taken), None)


def _resolve_time_fallback(
    world: World,
    incident: Incident,
    bids: dict[tuple[str, str], Bid],
    options: dict[Slot, list[Bid]],
    taken: dict[str, str],
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
    voted_on: list[Bid],
    free: list[Bid],
    votes: list[Vote],
) -> tuple[str | None, bool, dict[str, float]]:
    """Tally one role's votes among the options still free and return (winner, consensus, tally).

    More than 60% of honest weight is consensus; otherwise the heaviest free option still goes.
    """
    tally = {b.vehicle: 0.0 for b in free}
    for vote in votes:
        choice = voted_on[vote.option_index].vehicle
        if choice in tally:  # a vote for a vehicle already taken elsewhere no longer counts
            tally[choice] += trust.vote_weight(world.vehicles[vote.vehicle])
    honest_total = sum(trust.vote_weight(v) for v in world.vehicles.values() if not v.untrusted)
    # max() keeps the first of equal weights, and options are already best-bid first
    leader = max(tally, key=lambda vid: tally[vid]) if tally else None
    consensus = leader is not None and honest_total > 0 and tally[leader] > CONSENSUS_SHARE * honest_total
    return leader, consensus, tally


def _live_call(world: World, vehicle: Vehicle) -> str | None:
    """The unresolved call a vehicle is assigned to, or None if it is free."""
    iid = vehicle.target_incident
    return iid if iid in world.incidents and world.incidents[iid].status != "resolved" else None


def _spare(world: World, vehicle: Vehicle, call: str, taken: dict[str, str]) -> bool:
    """True if the call keeps another trusted vehicle of the same type without this one."""
    return any(v.id != vehicle.id and v.type == vehicle.type and not v.untrusted and v.id not in taken
               and v.target_incident == call for v in world.vehicles.values())


def _pull(
    world: World,
    incident: Incident,
    role: str,
    taken: dict[str, str],
    assigned: list[str],
    bids: dict[tuple[str, str], Bid],
    disproven: set[tuple[str, str]],
) -> dict | None:
    """Pick the trusted vehicle of this role the fleet can best spare for this incident, or None.

    Free vehicles first, then the lowest-severity call: only calls less severe than this one, unless the
    call keeps another unit of the same type. Nearest breaks ties. Never an untrusted vehicle, one already
    won this round, one whose bid here was caught lying, or one that said it can't do this call.
    Returns {vehicle, role, from, from_severity}; from is None for a free vehicle.
    """
    candidates = []
    for v in world.vehicles.values():
        bid = bids.get((v.id, incident.id))
        if (v.type != role or v.untrusted or v.id in taken or v.id in assigned or (v.id, incident.id) in disproven
                or (bid is not None and bid.capability_match < MIN_CAPABILITY)):
            continue
        call = _live_call(world, v)
        if call == incident.id:
            continue
        severity = world.incidents[call].severity if call else 0
        if call and severity >= incident.severity and not _spare(world, v, call, taken):
            continue
        candidates.append((severity, haversine_m(v.location, incident.location), v.id, call))
    if not candidates:
        return None
    severity, _dist, vid, call = min(candidates)
    return {"vehicle": vid, "role": role, "from": call, "from_severity": severity if call else None}


def _pull_reason(pulled: list[dict]) -> str:
    """'pulled from I6' for one pull; each vehicle named when there are several."""
    def one(p: dict, named: bool) -> str:
        what = f"pulled from {p['from']}" if p["from"] else "sent while free (no bid)"
        return f"{p['vehicle']} {what}" if named else what
    return one(pulled[0], False) if len(pulled) == 1 else "; ".join(one(p, True) for p in pulled)


def settled_by_human(world: World) -> set[str]:
    """Calls a human already decided: each answered escalation's call, the calls its options held, and every call in the answer."""
    settled: set[str] = set()
    asked = []
    for e in world.events:
        if e.type == "escalated_to_human":
            asked.append(e)
        elif e.type == "human_resolved":
            for q in asked:
                settled.add(q.incident)
                settled.update((q.data.get("contested") or {}).values())
            settled.update(iid for iid in (e.data.get("assignments") or {}).values() if iid)
            asked = []
    return settled


def _deadlock(
    world: World,
    incident: Incident,
    role: str,
    taken: dict[str, str],
    settled: set[str],
    voted_on: list[Bid],
    votes: list[Vote],
    claims: list[Claim],
    checks: list[dict],
) -> dict | None:
    """Ask a human only on a true deadlock: a severity 5 call whose every trusted vehicle of this role holds
    another severity 5 call it would leave uncovered. Never for a call a human already settled.

    The options are those tied-up vehicles, each marked contested with the call holding it.
    """
    if incident.severity < DEADLOCK_SEVERITY or incident.id in settled:
        return None
    trusted = [v for v in world.vehicles.values() if v.type == role and not v.untrusted]
    tied = {}
    for v in sorted(trusted, key=lambda v: (haversine_m(v.location, incident.location), v.id)):
        call = taken.get(v.id) or _live_call(world, v)
        if call is None or call == incident.id or world.incidents[call].severity < DEADLOCK_SEVERITY:
            return None
        tied[v.id] = call
    if not tied:
        return None  # no trusted vehicle of this role at all: nobody a human could send either
    decision = trust.should_escalate({
        "incident": incident.id,
        "severity": incident.severity,
        "role": role,
        "round": RESOLVE,
        "options": [],
        "tally": {},
        "honest_total": sum(trust.vote_weight(v) for v in world.vehicles.values() if not v.untrusted),
        "threshold": 0.0,
        "votes": [v.model_dump() for v in votes],
        "contradicting_claims": _contradicting_claims(incident.id, claims, checks),
        "checks": checks,  # lets should_escalate name who checked each contradicting claim
    }) or {}
    held = ", ".join(f"{vid} is on {call}" for vid, call in tied.items())
    return {
        **decision,
        "role": role,
        "reason": f"{incident.id} (severity 5) needs {'an' if role[0] in 'aeiou' else 'a'} {role}, and every "
                  f"trusted {role} holds another severity 5 call: {held}",
        "options": list(tied),
        "choices": [{"vehicle": vid, "label": f"Send {vid} (leaves {call})", "backers": [], "weight": 0.0}
                    for vid, call in tied.items()],
        "contested": tied,
    }


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
