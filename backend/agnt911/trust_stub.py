"""Stand-in for the trust engine until agnt911/trust.py exists. Same names and shapes; Chris replaces it.

protocol.py imports agnt911.trust and falls back to this module only if trust.py is missing.
The signatures here are the contract written in schema.md under "Requests".
"""

from .models import Claim, Vehicle, World

CheckResult = str  # "confirmed" | "contradicted" | "unverifiable"


def should_verify(claim: Claim, vehicle: Vehicle, round_no: int) -> bool:
    """Decide whether a claim gets checked this round (stub: never)."""
    return False


def check_claim(claim: Claim, world: World) -> CheckResult:
    """Compare a claim to the world and say if it holds (stub: always unverifiable)."""
    return "unverifiable"


def update_trust(vehicle: Vehicle, result: CheckResult) -> Vehicle:
    """Return the vehicle with trust adjusted for one check result (stub: unchanged)."""
    return vehicle


def vote_weight(vehicle: Vehicle) -> float:
    """Return how much this vehicle's vote counts (stub: 1.0, or 0.0 when untrusted)."""
    return 0.0 if vehicle.untrusted else 1.0


def should_escalate(round_state: dict) -> dict | None:
    """Decide whether a stuck vote goes to a human (stub: always, with the two top options by weight)."""
    tally = round_state["tally"]
    top = sorted(tally, key=lambda vehicle_id: -tally[vehicle_id])[:2]
    return {
        "options": top,
        "evidence": {
            "votes": round_state["votes"],
            "contradicting_claims": round_state["contradicting_claims"],
        },
    }
