"""Shared helpers for the demo scripts (run as `python scripts/<name>.py` from backend/)."""

from agnt911.engine.runtime import Engine


async def auto_resolve(engine: Engine) -> dict | None:
    """Stand in for the human at an escalation so an unattended run can continue.

    Per escalated plan: keep the vehicles it already has, and for each stuck role take the first offered
    option that no other call holds, else the first option (moving it off its call, which the human then owns).
    Offers are trusted vehicles only. Returns the assignments made, or None if some role has nobody to send.
    """
    assignments = {}
    for plan in engine.escalation["plans"]:
        evidence = plan["evidence"] or {}
        picks = list(plan["assigned"])
        for stuck in [evidence, *evidence.get("also", [])]:
            contested = stuck.get("contested", {})
            options = [v for v in stuck.get("options", []) if v not in assignments]
            pick = next((v for v in options if v not in contested), options[0] if options else None)
            if pick is None:
                return None
            picks.append(pick)
        assignments.update(dict.fromkeys(picks, plan["incident"]))
    await engine.resolve(assignments)
    return assignments
