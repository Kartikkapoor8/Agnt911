"""Contract tests: the handshake between modules. If one fails, a shared shape changed; tell the gc."""

import inspect

from agnt911 import protocol
from agnt911.models import AgentOutput, Plan, World

TRUST_FUNCTIONS = {
    "should_verify": ["claim", "vehicle", "round_no"],
    "check_claim": ["claim", "world"],
    "update_trust": ["vehicle", "result"],
    "vote_weight": ["vehicle"],
    "should_escalate": ["round_state"],
}


def test_world_model_exists():
    """The shared World model can be imported."""
    assert World is not None


def test_trust_module_has_the_functions_protocol_calls():
    """Whichever trust module protocol loaded (real or stub) has the five functions in schema.md."""
    for name, params in TRUST_FUNCTIONS.items():
        fn = getattr(protocol.trust, name)
        assert list(inspect.signature(fn).parameters) == params, name


def test_plan_and_agent_output_fields():
    """Plan and AgentOutput carry the fields the engine and frontend read."""
    assert {"incident", "assigned", "round_reached", "consensus", "escalated",
            "options", "tally", "checks", "evidence"} <= set(Plan.model_fields)
    assert set(AgentOutput.model_fields) == {"claims", "bids", "vote"}
