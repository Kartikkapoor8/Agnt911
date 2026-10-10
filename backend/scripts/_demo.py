"""Shared helpers for the demo scripts (run as `python scripts/<name>.py` from backend/)."""

from agnt911.engine.runtime import Engine
from agnt911.geo import haversine_m


async def auto_resolve(engine: Engine) -> dict | None:
    """Stand in for the human at an escalation so an unattended run can continue.

    Per escalated plan: keep the vehicles it already has, and for each stuck role take the first offered
    option that no other call holds; if every option is contested, send the nearest free vehicle of that
    role (whatever its trust). Returns the assignments made, or None if some role has nobody to send.
    """
    assignments = {}
    for plan in engine.escalation["plans"]:
        evidence = plan["evidence"] or {}
        incident = engine.world.incidents[plan["incident"]]
        picks = list(plan["assigned"])
        for stuck in [evidence, *evidence.get("also", [])]:
            contested = stuck.get("contested", {})
            pick = next((v for v in stuck.get("options", []) if v not in contested), None)
            if pick is None:
                free = [v for v in engine.world.vehicles.values()
                        if v.type == stuck.get("role") and v.status == "free" and v.id not in assignments]
                pick = min(free, key=lambda v: haversine_m(v.location, incident.location)).id if free else None
            if pick is None:
                return None
            picks.append(pick)
        assignments.update(dict.fromkeys(picks, plan["incident"]))
    await engine.resolve(assignments)
    return assignments
