"""Single entry point for calling Claude and getting back a validated Pydantic model."""

import json
import re
from typing import TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

MODEL = "claude-sonnet-5-5"
# Thinking is always on for Sonnet 5.5 and counts toward max_tokens, so leave room beyond the JSON.
MAX_TOKENS = 16000
# Sonnet 5.5 rejects temperature; effort is the only knob. Low keeps rounds fast for a short JSON reply.
# Determinism comes from our code: scoring, voting and trust are plain Python.
EFFORT = "low"

T = TypeVar("T", bound=BaseModel)

_client = None


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
    """Ask Claude for JSON matching response_model, validate it, and retry once on failure."""
    schema = json.dumps(response_model.model_json_schema())
    system_full = (
        f"{system}\n\nRespond with a single JSON object that matches this JSON schema, "
        f"with no other text:\n{schema}"
    )
    messages = [{"role": "user", "content": user}]
    client = get_client()
    error: ValidationError | None = None
    for _attempt in range(2):
        reply = client.messages.create(
            model=MODEL,
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
