"""Contract tests: the handshake between modules. Placeholder until shapes are locked."""

from agnt911.models import World


def test_world_model_exists():
    """The shared World model can be imported."""
    assert World is not None
