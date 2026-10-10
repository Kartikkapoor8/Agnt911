"""Deterministic stand-in for Claude so the protocol runs offline: honest vehicles, simple rules, no network."""

import json
import re

from .models import AgentOutput, Bid, Claim, Location, Vote

SPEED_M_PER_S = 12.0
TURNOUT_S = 30
CAPABILITY = {
    "ambulance": {"medical": 1.0, "crash": 0.8},
    "fire": {"fire": 1.0, "hazard": 0.8, "crash": 0.5},
    "police": {"crash": 0.9, "hazard": 0.9, "fire": 0.4, "medical": 0.3},
}
_BOARD_LINE = re.compile(r"- (\w+): (\w+), severity (\d), at [-\d.,]+, (\d+) m from you")


def split_user_message(user: str) -> tuple[dict, str]:
    """Split a VehicleAgent user message into its JSON payload and its task text."""
    payload, _, task = user.partition("\n\nTask: ")
    return json.loads(payload), task


def fake_llm(system: str, user: str, response_model: type[AgentOutput]) -> AgentOutput:
    """Answer one protocol round the way an honest, rule-following vehicle would."""
    payload, task = split_user_message(user)
    me = payload["you"]
    here = Location(**me["location"])
    if task.startswith("Round 1"):
        bids = [
            Bid(
                vehicle=me["id"], incident=iid,
                eta_seconds=TURNOUT_S + round(int(dist) / SPEED_M_PER_S),
                capability_match=CAPABILITY[me["type"]][itype],
                cost_to_district=0.2, confidence=0.9,
                reasoning=f"{me['type']} {dist} m away, equipped for {itype}.",
            )
            for iid, itype, _sev, dist in _BOARD_LINE.findall(task)
            if itype in CAPABILITY[me["type"]]
        ]
        status = Claim(
            id="x", tick=0, vehicle=me["id"], type="available", location=here,
            confidence=1.0, reasoning=f"Free in {me['home_district']}.",
        )
        return response_model(claims=[status], bids=bids)
    if task.startswith("Round 2"):
        mine = [m for m in payload["shared_message_log"]
                if m.get("kind") == "bid" and m["vehicle"] == me["id"]]
        return response_model(bids=[Bid(**m) for m in mine])
    incident = re.search(r"For incident (\w+)", task).group(1)
    return response_model(vote=Vote(
        vehicle=me["id"], incident=incident, option_index=0,
        reasoning="Option 0 has the best capability and ETA.",
    ))
