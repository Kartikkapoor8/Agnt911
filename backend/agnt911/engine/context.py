"""Optional per-decision randomness for synchronous trust functions."""

from contextvars import ContextVar
import random

decision_rng: ContextVar[random.Random] = ContextVar("agnt911_decision_rng")


def current_rng() -> random.Random:
    """Return the engine's seeded RNG inside a protocol call, or fail outside one."""
    return decision_rng.get()
