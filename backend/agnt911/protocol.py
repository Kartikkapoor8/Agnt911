"""Negotiation protocol entry point (stub; the loop is not built yet)."""

from .models import Plan, World

MAX_ROUNDS = 4


def run_round(world: World) -> list[Plan]:
    """Run one negotiation over the world and return a Plan per open incident (not implemented yet).

    Intended flow, at most MAX_ROUNDS (4) rounds per incident:
      1. Propose: each vehicle that can help returns a Bid for the incident.
      2. Counter: vehicles read the bids and may revise, object, or stand down.
      3. Weighted vote: each vehicle votes for a plan option; votes count in
         proportion to the voter's trust score, and untrusted vehicles are ignored.
      4. Commit: if one option wins, lock it as the Plan (consensus=True).
         If no consensus after the last round, mark the Plan escalated=True so a human decides.
    """
    raise NotImplementedError("negotiation loop is not built yet")
