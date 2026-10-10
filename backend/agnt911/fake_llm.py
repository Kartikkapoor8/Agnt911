"""Deterministic stand-in for Claude so the protocol runs offline: honest vehicles, simple rules, no network."""

import json
import re

from .models import AgentOutput, Bid, Claim, Location, Vote

SPEED_M_PER_S = 12.0
TURNOUT_S = 30
CAPABILITY = {
    "ambulance": {"medical": 1.0, "crash": 0.8, "shooting": 0.8},
    "fire": {"fire": 1.0, "hazard": 0.8, "crash": 0.5},
    "police": {"shooting": 1.0, "crash": 0.9, "hazard": 0.9, "fire": 0.4, "medical": 0.3},
}
_BOARD_LINE = re.compile(r"- (\w+): (\w+), severity (\d), at [-\d.,]+, (\d+) m from you")
_BOARD_EXTRA = re.compile(r"^- (\w+): .* m from you(?:, ETA (\d+) s)?(?:, status (\w+))?$", re.MULTILINE)
VIEW_RADIUS_M = 500


def split_user_message(user: str) -> tuple[dict, str]:
    """Split a VehicleAgent user message into its JSON payload and its task text."""
    payload, _, task = user.partition("\n\nTask: ")
    return json.loads(payload), task


def fake_llm(system: str, user: str, response_model: type[AgentOutput]) -> AgentOutput:
    """Answer one protocol round the way an honest, rule-following vehicle would, trusting what it sees.

    It believes its own view, so a corrupted sensor feed (feed.py) misleads it without it lying.
    """
    payload, task = split_user_message(user)
    me = payload["you"]
    here = Location(**me["location"])
    if task.startswith("Round 1"):
        return response_model(**_propose(payload, task, me, here))
    if task.startswith("Round 2"):
        mine = [m for m in payload["shared_message_log"]
                if m.get("kind") == "bid" and m["vehicle"] == me["id"]]
        return response_model(bids=[Bid(**m) for m in mine])
    incident = re.search(r"For incident (\w+)", task).group(1)
    return response_model(vote=Vote(
        vehicle=me["id"], incident=incident, option_index=0,
        reasoning="Option 0 has the best capability and ETA.",
    ))


def _claim(me: dict, here: Location, kind: str, reasoning: str, incident: str | None = None, **extra) -> Claim:
    """Build one claim from this vehicle at the position it believes it is at."""
    return Claim(id="x", tick=0, vehicle=me["id"], type=kind, incident=incident, location=here,
                 confidence=extra.pop("confidence", 1.0), reasoning=reasoning, **extra)


def _propose(payload: dict, task: str, me: dict, here: Location) -> dict:
    """Round 1: state your status from your own view and bid on every equipped call that is not resolved."""
    calls = {iid: (itype, int(dist)) for iid, itype, _sev, dist in _BOARD_LINE.findall(task)}
    extras = {iid: (eta, status) for iid, eta, status in _BOARD_EXTRA.findall(task)}
    resolved = {iid for iid, (_eta, status) in extras.items() if status == "resolved"}
    resolved |= {i["id"] for i in payload["nearby_incidents"] if i.get("status") == "resolved"}

    if me["status"] == "unavailable":
        return {"claims": [_claim(me, here, "available", "My status reads unavailable, so I cannot take calls.",
                                  value={"available": False})]}
    target = me.get("target_incident")
    if me["status"] == "on_scene" and target in resolved:
        claims = [_claim(me, here, "on_scene", f"I am at {target}.", target),
                  _claim(me, here, "resolved", f"{target} shows as resolved.", target)]
    else:
        claims = [_claim(me, here, "available", f"Free in {me['home_district']}.")]
    if not payload["nearby_incidents"] and any(calls[i][1] <= VIEW_RADIUS_M for i in resolved if i in calls):
        claims.append(_claim(me, here, "sector_clear", "Every call near me shows as resolved."))
    if payload.get("sensor_note"):
        claims.append(_claim(me, here, "observation", "My sensors carry an unexplained note; noting it.",
                             confidence=0.5, value={"sensor_note": True}))

    bids = [
        Bid(
            vehicle=me["id"], incident=iid,
            eta_seconds=int(extras[iid][0]) if extras.get(iid, ("",))[0] else TURNOUT_S + round(dist / SPEED_M_PER_S),
            capability_match=CAPABILITY[me["type"]][itype], cost_to_district=0.2, confidence=0.9,
            reasoning=f"{me['type']} {dist} m away, equipped for {itype}.",
        )
        for iid, (itype, dist) in calls.items()
        if itype in CAPABILITY[me["type"]] and iid not in resolved
    ]
    return {"claims": claims, "bids": bids}
