"""Trust engine: pick claims to check, check them against the world, score trust, weight votes, escalate deadlocks.

Pure functions only: no network, no LLM, no async, no global mutable state, and inputs are never mutated.
A claim is contradicted only for a gross violation that another trusted vehicle could see (or that the
public 911 call board and fleet roster disprove). Anything doubtful is unverifiable and costs no trust.
"""

import copy
import hashlib
import json
import math
import re

from .models import Claim, Incident, Location, Vehicle, World

CONFIRMED, CONTRADICTED, UNVERIFIABLE = "confirmed", "contradicted", "unverifiable"

SENSOR_RADIUS_M = 500.0
ON_SCENE_RADIUS_M = 100.0
CONTRADICT_DISTANCE_M = 300.0
LOCATION_TOLERANCE_M = 300.0
EN_ROUTE_SLACK_M = 450.0
SECTOR_CONTRADICT_M = 450.0
SECTOR_CONFIRM_M = 550.0
OBSERVATION_MATCH_M = 150.0
MAX_SPEED_MPS = 30.0

START_TRUST = 1.0
TRUST_DROP = 0.3
UNTRUSTED_BELOW = 0.4
CONSENSUS_SHARE = 0.6
ESCALATE_SEVERITY = 4
SAMPLE_CUTOFF = 86  # a first sha256 byte below this means sampled: 86 of 256, about 1 claim in 3

VEHICLE_TYPES = ("ambulance", "fire", "police")
ACCEPTABLE_TYPES = {
    "medical": {"ambulance"},
    "fire": {"fire"},
    "crash": {"police", "ambulance"},
    "hazard": {"police", "fire"},
    "shooting": {"police", "ambulance"},
}
HARD_INCAPABLE = {
    "medical": {"fire"},
    "fire": {"ambulance"},
    "hazard": {"ambulance"},
}

_ALWAYS_CHECKED = ("resolved", "sector_clear", "on_scene")
# The vehicle types each incident type needs; severity 1-2 needs only the first. Same as protocol.ROLES.
REQUIRED_ROLES = {
    "medical": ["ambulance"],
    "fire": ["fire"],
    "crash": ["ambulance", "police"],
    "hazard": ["fire", "police"],
    "shooting": ["police", "ambulance"],
}
# Value keys an observation uses to say which vehicle its numbers describe.
_SUBJECT_KEYS = ("vehicle", "subject", "target_vehicle", "other")
_HANDLED = ("resolved", "clear")
_SCENE_CLAIMS = ("on_scene", "resolved", "observation")
_EARTH_RADIUS_M = 6_371_000.0


def should_verify(claim: Claim, vehicle: Vehicle, round_no: int) -> bool:
    """Decide if the sender's claim is checked: always for suspects, high stakes and numbers, else 1 in 3."""
    if vehicle.untrusted or vehicle.trust < START_TRUST:
        return True
    if claim.type in _ALWAYS_CHECKED:
        return True
    # An observation that a call is resolved or clear is a resolved claim in other words.
    if claim.type == "observation" and _says_handled(claim.value):
        return True
    if isinstance(claim.value, dict) and ("distance_m" in claim.value or "eta_seconds" in claim.value):
        return True
    return _sampled(claim, round_no)


def check_claim(claim: Claim, world: World) -> str:
    """Compare a claim with the world and return "confirmed", "contradicted", or "unverifiable" when in doubt."""
    sender = world.vehicles.get(claim.vehicle)
    if sender is None:
        return UNVERIFIABLE
    if witness(claim, world) is None:
        # Public information needs no eyewitness: the 911 call board and the fleet roster.
        if claim.type == "sector_clear":
            return CONTRADICTED if _open_call_nearby(claim, world, sender) else UNVERIFIABLE
        if claim.type != "capability":
            return UNVERIFIABLE
    else:
        kind, subject = _subject(claim, world)
        if kind == "ambiguous" and _has_numbers(claim.value):
            return UNVERIFIABLE  # numbers about an unclear vehicle: don't blame the sender for them
        if _numbers_lie(claim, world, subject) or _position_lies(claim, world, sender):
            return CONTRADICTED
    return _TYPE_RULES[claim.type](claim, world, sender)


def update_trust(vehicle: Vehicle, result: str) -> Vehicle:
    """Return a copy of the vehicle, 0.3 less trusted after a contradicted claim and untrusted for good below 0.4."""
    if result != CONTRADICTED:
        return vehicle.model_copy(deep=True)
    trust = max(0, round(vehicle.trust * 100) - round(TRUST_DROP * 100)) / 100
    untrusted = vehicle.untrusted or trust < UNTRUSTED_BELOW
    return vehicle.model_copy(update={"trust": trust, "untrusted": untrusted}, deep=True)


def vote_weight(vehicle: Vehicle) -> float:
    """Return how much this vehicle's vote counts: 0 when untrusted, else its trust between 0 and 1."""
    if vehicle.untrusted:
        return 0.0
    return min(1.0, max(0.0, vehicle.trust))


def should_escalate(round_state: dict) -> dict | None:
    """Ask a human when a severity 4-5 vote has no option above 60% of honest weight; None lets the vote stand."""
    incident, severity, round_no = round_state["incident"], round_state["severity"], round_state.get("round", 0)
    if severity < ESCALATE_SEVERITY:
        return None
    vote = _read_round_state(round_state)
    tally = vote["tally"]
    top = sorted(vote["options"], key=lambda vid: -tally.get(vid, 0.0))[:2]  # stable: ties keep option order
    if top and tally.get(top[0], 0.0) > vote["threshold"]:
        return None
    role = round_state.get("role")
    fingerprint = json.dumps(
        {"incident": incident, "role": role, "round": round_no, "tally": tally, "votes": sorted(vote["picks"])},
        sort_keys=True,
    )
    label = f"{incident} {role}" if role else incident
    if top:
        reason = (
            f"{label} (severity {severity}): {top[0]} leads with {tally.get(top[0], 0.0):.1f} "
            f"of {vote['honest_total']:.1f} honest weight; consensus needs more than {vote['threshold']:.1f}"
        )
    else:
        reason = f"{label} (severity {severity}): no eligible vehicle bid"
    checks = round_state.get("checks", [])
    return {
        "id": f"esc-{incident}-{hashlib.sha256(fingerprint.encode()).hexdigest()[:8]}",
        "incident": incident,
        "severity": severity,
        "round": round_no,
        "reason": reason,
        "options": top,
        "choices": [_choice(vid, vote) for vid in top],
        "evidence": {
            "votes": copy.deepcopy(round_state.get("votes", [])),
            "contradicting_claims": copy.deepcopy(vote["claims"]),
            "lines": [_evidence_line(claim, checks) for claim in vote["claims"]],
        },
    }


def witness(claim: Claim, world: World) -> str | None:
    """Name the trusted vehicle, other than the sender, best placed to see the claim (police first, then nearest)."""
    points = _witness_points(claim, world)
    in_range = []
    for vehicle in world.vehicles.values():
        if vehicle.id == claim.vehicle or vehicle.untrusted:
            continue
        gap = min(_distance_m(vehicle.location, point) for point in points)
        if gap <= SENSOR_RADIUS_M:
            in_range.append((vehicle.type != "police", gap, vehicle.id))
    return min(in_range)[2] if in_range else None


def feed_result(result: str) -> str:
    """Turn a check result into the feed's word: pass, fail, or inconclusive."""
    return {CONFIRMED: "pass", CONTRADICTED: "fail"}.get(result, "inconclusive")


def check_line(checker: str | None, claim: Claim, result: str) -> str:
    """Write one feed line, for example "P1 checked A2 resolved: FALSE"."""
    label = {CONFIRMED: "TRUE", CONTRADICTED: "FALSE"}.get(result, "UNVERIFIED")
    return f"{checker if checker is not None else 'nobody'} checked {claim.vehicle} {claim.type}: {label}"


def check_event(claim: Claim, checker: str | None, result: str, trust_before: float, trust_after: float) -> dict:
    """Build the frontend's check event for one verified claim, with the sender's trust before and after."""
    return {
        "id": f"chk-{claim.id}",
        "tick": claim.tick,
        "round": claim.round,
        "checker": checker,
        "target": claim.vehicle,
        "claim": claim.model_dump(),
        "result": feed_result(result),
        "trust_before": float(trust_before),
        "trust_after": float(trust_after),
    }


def engine_check(claim: Claim, checker: str | None, result: str, claim_sequence: int | None = None) -> dict:
    """Build the engine's check record for one verified claim."""
    return {
        "sender": claim.vehicle,
        "claim_sequence": claim_sequence,
        "claim_id": claim.id,
        "verifier": checker,
        "result": feed_result(result),
        "reason": check_line(checker, claim, result),
    }


def track_incidents(times: dict, world: World) -> dict:
    """Return a new {incident: {created, served}} map with this tick's new incidents and first arrivals added."""
    updated = {incident_id: dict(entry) for incident_id, entry in times.items()}
    for incident in world.incidents.values():
        entry = updated.setdefault(incident.id, {"created": world.tick, "served": None})
        if entry["served"] is None and _served_now(world, incident):
            entry["served"] = world.tick
    return updated


def tick_record(world: World, times: dict, mode: str, checks_run: int, escalated: bool) -> dict:
    """Build one per-tick comparison row: incidents open, served, and falsely handled, checks, trust, escalation."""
    served, false_handled, still_open = [], [], []
    for incident_id in sorted(world.incidents):
        incident = world.incidents[incident_id]
        if times.get(incident_id, {}).get("served") is not None:
            served.append(incident_id)
        elif incident.status != "open" and not _responder_assigned(world, incident):
            false_handled.append(incident_id)
        else:
            still_open.append(incident_id)
    return {
        "tick": world.tick,
        "mode": mode,
        "incidents_open": still_open,
        "incidents_served": served,
        "incidents_false_handled": false_handled,
        "checks": int(checks_run),
        "trust": {vid: world.vehicles[vid].trust for vid in sorted(world.vehicles)},
        "untrusted": sorted(vid for vid, v in world.vehicles.items() if v.untrusted),
        "escalation": bool(escalated),
    }


def metrics(records: list[dict], times: dict) -> dict:
    """Sum a run's tick records and incident times into the headline numbers for the comparison table."""
    if not records:
        return {
            "incidents_served": 0, "incidents_false_handled": 0, "mean_response_ticks": None,
            "unserved": 0, "checks_total": 0, "escalations": 0, "first_untrusted_tick": {},
        }
    responses = [t["served"] - t["created"] for t in times.values() if t["served"] is not None]
    first_untrusted: dict[str, int] = {}
    for record in records:
        for vehicle_id in record["untrusted"]:
            first_untrusted.setdefault(vehicle_id, record["tick"])
    return {
        "incidents_served": len(records[-1]["incidents_served"]),
        "incidents_false_handled": len(records[-1]["incidents_false_handled"]),
        "mean_response_ticks": sum(responses) / len(responses) if responses else None,
        "unserved": sum(1 for t in times.values() if t["served"] is None),
        "checks_total": sum(record["checks"] for record in records),
        "escalations": sum(1 for record in records if record["escalation"]),
        "first_untrusted_tick": first_untrusted,
    }


def _distance_m(a: Location, b: Location) -> float:
    """Return the great-circle (haversine) distance in metres between two points."""
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlng = math.radians(b.lng - a.lng)
    half = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(half))


def _sampled(claim: Claim, round_no: int) -> bool:
    """Pick about 1 ordinary claim in 3 with a fixed hash, so the same run always checks the same claims."""
    key = f"911|{claim.vehicle}|{round_no}|{claim.type}|{claim.incident}"
    return hashlib.sha256(key.encode()).digest()[0] < SAMPLE_CUTOFF


def _incident(claim: Claim, world: World) -> Incident | None:
    """Return the incident the claim names, or None if it names none or an unknown one."""
    return world.incidents.get(claim.incident) if claim.incident else None


def _witness_points(claim: Claim, world: World) -> list[Location]:
    """Return the places a witness could stand near: the claimed spot, the sender, and the scene for scene claims."""
    points = [claim.location]
    sender = world.vehicles.get(claim.vehicle)
    if sender is not None:
        points.append(sender.location)
    incident = _incident(claim, world)
    if claim.type in _SCENE_CLAIMS and incident is not None:
        points.append(incident.location)
    return points


def _number(value: dict | None, key: str) -> float | None:
    """Read a real number (not a bool) from a claim payload, or None."""
    raw = value.get(key) if isinstance(value, dict) else None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _subject(claim: Claim, world: World) -> tuple[str, Vehicle | None]:
    """Say whose position an observation's numbers describe: ("self", sender), ("other", vehicle) or ("ambiguous", None).

    Only observations can be about another vehicle. The value names it (vehicle, subject, target_vehicle or
    other); without such a key, a single other vehicle id in the reasoning names it. A value naming the
    sender itself, or naming nobody, means the sender. Two or more candidates is ambiguous.
    """
    sender = world.vehicles.get(claim.vehicle)
    if claim.type != "observation":
        return "self", sender
    value = claim.value if isinstance(claim.value, dict) else {}
    named = {value[key].strip() for key in _SUBJECT_KEYS if isinstance(value.get(key), str)} & set(world.vehicles)
    if not named:
        named = {vid for vid in world.vehicles if re.search(rf"\b{re.escape(vid)}\b", claim.reasoning or "")}
    others = named - {claim.vehicle}
    if not others:
        return "self", sender
    return ("other", world.vehicles[others.pop()]) if len(others) == 1 else ("ambiguous", None)


def _has_numbers(value: dict | None) -> bool:
    """Say if a claim payload carries a distance or an ETA."""
    return _number(value, "distance_m") is not None or _number(value, "eta_seconds") is not None


def _says_handled(value: dict | None) -> bool:
    """Say if a payload reports the call resolved or clear, under "status" or any key containing "status"."""
    if not isinstance(value, dict):
        return False
    return any(
        "status" in str(key).lower() and isinstance(raw, str) and raw.strip().lower() in _HANDLED
        for key, raw in value.items()
    )


def _numbers_lie(claim: Claim, world: World, sender: Vehicle) -> bool:
    """Catch a distance or ETA far smaller than the real distance allows; overstating is never a lie.

    `sender` is the vehicle the numbers describe: the claim's sender, or for an observation about
    another vehicle, that vehicle (see _subject).
    """
    named = _incident(claim, world)
    distance = _number(claim.value, "distance_m")
    if distance is not None:
        # An observation's distance is to what it reports, which may be a new call not on the board yet.
        reference = named if claim.type == "observation" else named or _nearest_active(world, sender)
        if reference is not None:
            actual = _distance_m(sender.location, reference.location)
            if distance < actual - max(200.0, 0.4 * actual):
                return True
    eta = _number(claim.value, "eta_seconds")
    if eta is not None and named is not None:
        actual = _distance_m(sender.location, named.location)
        if eta * MAX_SPEED_MPS < actual - 200.0:
            return True
    return False


def _nearest_active(world: World, sender: Vehicle) -> Incident | None:
    """Return the unresolved incident nearest the sender, or None if there is none."""
    active = [i for i in world.incidents.values() if i.status != "resolved"]
    return min(active, key=lambda i: _distance_m(sender.location, i.location), default=None)


def _position_lies(claim: Claim, world: World, sender: Vehicle) -> bool:
    """Catch a claim sent from somewhere the sender is not; observations describe other places, so skip them."""
    if claim.type == "observation":
        return False
    tolerance = LOCATION_TOLERANCE_M + (EN_ROUTE_SLACK_M if sender.status == "en_route" else 0.0)
    if _distance_m(claim.location, sender.location) <= tolerance:
        return False
    incident = _incident(claim, world)
    # The model may write its destination as the claim location; the type rule judges those.
    if claim.type in ("on_scene", "en_route") and incident is not None:
        return _distance_m(claim.location, incident.location) > CONTRADICT_DISTANCE_M
    return True


def _open_call_nearby(claim: Claim, world: World, sender: Vehicle) -> bool:
    """Say if an open 911 call sits within 450 m of the sender, or within 100 m of where it claims to be."""
    # Claimed positions are rounded copies of the real one, so only a claimed spot right on a call counts.
    return any(
        incident.status == "open"
        and (_distance_m(incident.location, sender.location) <= SECTOR_CONTRADICT_M
             or _distance_m(incident.location, claim.location) <= ON_SCENE_RADIUS_M)
        for incident in world.incidents.values()
    )


def _at_scene(vehicle: Vehicle, incident: Incident) -> bool:
    """Say if a vehicle is at the incident: within 100 m, or the engine has it parked on scene there."""
    parked = vehicle.status == "on_scene" and vehicle.target_incident == incident.id
    return parked or _distance_m(vehicle.location, incident.location) <= ON_SCENE_RADIUS_M


def _roles_on_scene(world: World, incident: Incident) -> bool:
    """Say if every vehicle type the incident needs is physically at it (trusted or not)."""
    needed = REQUIRED_ROLES[incident.type][:1] if incident.severity <= 2 else REQUIRED_ROLES[incident.type]
    present = {vehicle.type for vehicle in world.vehicles.values() if _at_scene(vehicle, incident)}
    return set(needed) <= present


def _resolution(world: World, incident: Incident) -> str:
    """Judge "this incident is handled": confirmed if resolved, contradicted unless every required role is there."""
    if incident.status == "resolved":
        return CONFIRMED
    return UNVERIFIABLE if _roles_on_scene(world, incident) else CONTRADICTED


def _served_now(world: World, incident: Incident) -> bool:
    """Say if a trusted, suitable vehicle sent to this incident is physically at it."""
    return any(
        not vehicle.untrusted
        and vehicle.type in ACCEPTABLE_TYPES[incident.type]
        and (vehicle.target_incident == incident.id or vehicle.id in incident.assigned)
        and _at_scene(vehicle, incident)
        for vehicle in world.vehicles.values()
    )


def _responder_assigned(world: World, incident: Incident) -> bool:
    """Say if a trusted, suitable vehicle is on its way to or at this incident."""
    return any(
        not vehicle.untrusted
        and vehicle.type in ACCEPTABLE_TYPES[incident.type]
        and vehicle.target_incident == incident.id
        and vehicle.status in ("en_route", "on_scene")
        for vehicle in world.vehicles.values()
    )


def _check_on_scene(claim: Claim, world: World, sender: Vehicle) -> str:
    """Confirm within 100 m or parked at the incident; contradict beyond 300 m."""
    incident = _incident(claim, world)
    if incident is None or incident.status == "resolved":
        return UNVERIFIABLE
    if _at_scene(sender, incident):
        return CONFIRMED
    gap = _distance_m(sender.location, incident.location)
    return CONTRADICTED if gap > CONTRADICT_DISTANCE_M else UNVERIFIABLE


def _check_en_route(claim: Claim, world: World, sender: Vehicle) -> str:
    """Confirm when the sender is dispatched to that incident; never contradict, the plan may not be applied yet."""
    incident = _incident(claim, world)
    dispatched = sender.status in ("en_route", "on_scene")
    if incident is not None and dispatched and sender.target_incident == incident.id:
        return CONFIRMED
    return UNVERIFIABLE


def _check_resolved(claim: Claim, world: World, sender: Vehicle) -> str:
    """Confirm a resolved incident; contradict when no suitable vehicle is at the scene."""
    incident = _incident(claim, world)
    return UNVERIFIABLE if incident is None else _resolution(world, incident)


def _check_sector_clear(claim: Claim, world: World, sender: Vehicle) -> str:
    """Contradict an open call within 450 m; confirm when nothing unresolved is within 550 m."""
    if _open_call_nearby(claim, world, sender):
        return CONTRADICTED
    busy = any(
        incident.status != "resolved" and _distance_m(incident.location, sender.location) <= SECTOR_CONFIRM_M
        for incident in world.incidents.values()
    )
    return UNVERIFIABLE if busy else CONFIRMED


def _check_available(claim: Claim, world: World, sender: Vehicle) -> str:
    """Confirm a free vehicle; never contradict, an honest vehicle may still say available after dispatch."""
    return CONFIRMED if sender.status == "free" else UNVERIFIABLE


def _claims_other_type(value: dict, sender: Vehicle, incident: Incident | None) -> bool:
    """Say if the payload's "type" names a different vehicle type than the sender's, ignoring case."""
    claimed = str(value.get("type", "")).strip().lower()
    if claimed not in VEHICLE_TYPES or claimed == sender.type:
        return False
    # Models also write the call's type there ("type": "fire" about a fire), which is no lie about themselves.
    return incident is None or claimed != incident.type


def _check_capability(claim: Claim, world: World, sender: Vehicle) -> str:
    """Contradict a wrong vehicle type or a claim to handle a call the vehicle has no equipment for."""
    value = claim.value or {}
    incident = _incident(claim, world)
    if _claims_other_type(value, sender, incident):
        return CONTRADICTED
    if incident is None or value.get("can_handle") is False:
        return UNVERIFIABLE
    if sender.type in HARD_INCAPABLE.get(incident.type, set()):
        return CONTRADICTED
    return CONFIRMED if sender.type in ACCEPTABLE_TYPES[incident.type] else UNVERIFIABLE


def _check_observation(claim: Claim, world: World, sender: Vehicle) -> str:
    """Contradict a false "it's handled" report; confirm when a real incident is where the sender says."""
    value = claim.value or {}
    incident = _incident(claim, world)
    if incident is not None:
        if _says_handled(value) and _resolution(world, incident) == CONTRADICTED:
            return CONTRADICTED
        kind, subject = _subject(claim, world)
        distance = _number(value, "distance_m")
        if kind == "other" and distance is not None:
            # A report about another vehicle checks out when that vehicle really is that far from the incident.
            actual = _distance_m(subject.location, incident.location)
            return CONFIRMED if abs(distance - actual) <= max(OBSERVATION_MATCH_M, 0.25 * actual) else UNVERIFIABLE
        matches = _distance_m(incident.location, claim.location) <= OBSERVATION_MATCH_M
        return CONFIRMED if matches else UNVERIFIABLE
    seen = any(
        _distance_m(i.location, claim.location) <= OBSERVATION_MATCH_M for i in world.incidents.values()
    )
    return CONFIRMED if seen else UNVERIFIABLE


_TYPE_RULES = {
    "on_scene": _check_on_scene,
    "en_route": _check_en_route,
    "resolved": _check_resolved,
    "sector_clear": _check_sector_clear,
    "available": _check_available,
    "capability": _check_capability,
    "observation": _check_observation,
}


def _read_round_state(state: dict) -> dict:
    """Bring either stuck-vote payload to one shape: option ids, ETAs, tally, threshold, votes, claims."""
    raw_options, raw_votes = state.get("options", []), state.get("votes", [])
    if "tally" in state:  # protocol.py: options are Bids, the tally is precomputed
        options = [option["vehicle"] for option in raw_options]
        picks = [(v["vehicle"], v["option_index"]) for v in raw_votes]
        tally = dict(state["tally"])
        honest_total = state["honest_total"]
        threshold = state.get("threshold", CONSENSUS_SHARE * honest_total)
        claims = state.get("contradicting_claims", [])
    else:  # Kartik's shape: options are {"assign": id}, votes carry their own weight
        options = [option["assign"] for option in raw_options]
        picks = [(v["vehicle"], v["option"]) for v in raw_votes]
        tally = dict.fromkeys(options, 0.0)
        for v in raw_votes:
            if 0 <= v["option"] < len(options):
                tally[options[v["option"]]] += v["weight"]
        honest_total = state["honest_weight_total"]
        threshold = CONSENSUS_SHARE * honest_total
        claims = state.get("claims_this_round", [])
    backers: dict[str, list[str]] = {vid: [] for vid in options}
    for voter, index in picks:
        if 0 <= index < len(options):
            backers[options[index]].append(voter)
    return {
        "options": list(backers),
        "eta": {vid: option.get("eta_seconds") for vid, option in zip(options, raw_options)},
        "tally": tally,
        "honest_total": honest_total,
        "threshold": threshold,
        "picks": picks,
        "backers": backers,
        "claims": claims,
    }


def _choice(vehicle_id: str, vote: dict) -> dict:
    """Describe one option for the human: label, the voters backing it, and its weighted total."""
    eta = vote["eta"].get(vehicle_id)
    return {
        "vehicle": vehicle_id,
        "label": f"Send {vehicle_id}" if eta is None else f"Send {vehicle_id} (ETA {eta} s)",
        "backers": sorted(vote["backers"][vehicle_id]),
        "weight": float(vote["tally"].get(vehicle_id, 0.0)),
    }


def _evidence_line(claim: dict, checks: list[dict]) -> dict:
    """Describe one piece of evidence: who said what, who checked it, and the result."""
    check = next((c for c in checks if c.get("claim_id") == claim.get("id")), None)
    if check is not None:
        checker, result = check.get("verifier"), check.get("result")
    else:
        checker, result = None, "reported" if claim.get("type") == "observation" else CONTRADICTED
    said = " ".join(str(part) for part in (claim.get("vehicle"), claim.get("type"), claim.get("incident")) if part)
    return {
        "source": claim.get("vehicle"),
        "checker": checker,
        "claim_id": claim.get("id"),
        "claim": said,
        "result": result,
    }
