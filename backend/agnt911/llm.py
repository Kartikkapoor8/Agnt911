"""Single entry point for calling Claude and getting back a validated Pydantic model."""

import json
import logging
import os
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

MODEL = "claude-sonnet-5-5"
# Model per protocol round, read from the "Task: Round N" line of the user message. Round 1
# (proposing) needs the best reasoning; counter and vote are cheaper. Anything else uses MODEL.
ROUND_MODELS = {1: "claude-sonnet-5-5", 2: "claude-haiku-5-5", 3: "claude-haiku-5-5"}
# Model for vehicles with an injection in world.injections, in every round. The engine may flip it.
INJECTED_MODEL = "claude-haiku-5-5"
# Cap per agent turn. Thinking counts toward it; a turn's JSON is well under 1000 tokens.
MAX_TOKENS = 4000
# Hard limit on API requests per process (retries included); override with AGNT911_MAX_CALLS.
DEFAULT_MAX_CALLS = 500
LOG_EVERY = 50
# Sonnet 5.5 rejects temperature; effort is the only knob. Low keeps rounds fast for a short JSON reply.
# Determinism comes from our code: scoring, voting and trust are plain Python.
EFFORT = "low"

T = TypeVar("T", bound=BaseModel)

log = logging.getLogger(__name__)

_client = None
_calls = 0
_calls_lock = threading.Lock()
_ROUND_RE = re.compile(r"Task: Round (\d)")
_model_override: ContextVar[str | None] = ContextVar("model_override", default=None)


@contextmanager
def using_model(model: str) -> Iterator[None]:
    """Make call_claude use this model for calls made inside the block (on this thread or task)."""
    token = _model_override.set(model)
    try:
        yield
    finally:
        _model_override.reset(token)


class CallLimitExceeded(RuntimeError):
    """Raised instead of making an API request once the process has used its call budget."""


def call_limit() -> int:
    """Return the per-process API request limit: AGNT911_MAX_CALLS, or 500."""
    return int(os.environ.get("AGNT911_MAX_CALLS", DEFAULT_MAX_CALLS))


def call_count() -> int:
    """Return how many API requests this process has made so far."""
    return _calls


def reset_call_count() -> None:
    """Set the request counter back to zero (for tests and fresh runs)."""
    global _calls
    with _calls_lock:
        _calls = 0


def _count_call() -> None:
    """Reserve one request against the budget, or raise CallLimitExceeded; log every LOG_EVERY requests."""
    global _calls
    limit = call_limit()
    with _calls_lock:
        if _calls >= limit:
            raise CallLimitExceeded(
                f"LLM call limit reached: {_calls} of {limit} API requests used. "
                "Raise AGNT911_MAX_CALLS or call llm.reset_call_count() to continue.")
        _calls += 1
        count = _calls
    if count % LOG_EVERY == 0:
        log.warning("LLM budget: %d of %d API requests used", count, limit)


def model_for(user: str) -> str:
    """Pick the model for one turn: an active using_model() block, else the round's model, else MODEL."""
    override = _model_override.get()
    if override:
        return override
    match = _ROUND_RE.search(user)
    return ROUND_MODELS.get(int(match.group(1)), MODEL) if match else MODEL


def get_client():
    """Create the Anthropic client on first use so importing this module needs no API key."""
    global _client
    if _client is None:
        import anthropic

        load_dotenv()
        _client = anthropic.Anthropic()
    return _client


def extract_json(text: str) -> str:
    """Pull the JSON object out of a model reply, ignoring code fences and surrounding prose."""
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


def call_claude(system: str, user: str, response_model: type[T]) -> T:
    """Ask Claude for JSON matching response_model, validate it, and retry once on failure.

    The model comes from model_for(). Every request, retries included, counts against call_limit().
    """
    schema = json.dumps(response_model.model_json_schema())
    system_full = (
        f"{system}\n\nRespond with a single JSON object that matches this JSON schema, "
        f"with no other text:\n{schema}"
    )
    messages = [{"role": "user", "content": user}]
    model = model_for(user)
    client = get_client()
    error: ValidationError | None = None
    for _attempt in range(2):
        _count_call()
        reply = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            output_config={"effort": EFFORT},
            system=system_full,
            messages=messages,
        )
        if getattr(reply, "stop_reason", None) == "refusal":
            raise RuntimeError(f"Claude declined this turn: {getattr(reply, 'stop_details', None)}")
        text = "".join(b.text for b in reply.content if getattr(b, "type", "") == "text")
        try:
            return response_model.model_validate_json(extract_json(text))
        except ValidationError as exc:
            error = exc
            messages = messages + [
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": f"That JSON failed validation:\n{exc}\nReturn corrected JSON only.",
                },
            ]
    raise error
