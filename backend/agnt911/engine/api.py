"""Single-process FastAPI endpoints using the team's shared backend package."""

import asyncio
from contextlib import asynccontextmanager
import json
import os
from typing import Literal
import urllib.error
import urllib.parse
import urllib.request

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator, model_validator

from ..fake_llm import fake_llm
from .runtime import Engine
from .world import load_scenario

ELEVENLABS_TOKEN_URL = "https://api.elevenlabs.io/v1/convai/conversation/token"


class Injection(BaseModel):
    """Judge hack: vehicle_id plus a scenario preset_id, or free text that becomes a raw sensor note.

    vehicle_id may be left out only for the fake_call preset, which places a call instead of hacking a vehicle.
    """
    vehicle_id: str | None = None
    preset_id: str | None = None
    text: str | None = Field(default=None, min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        """Reject whitespace-only judge instructions."""
        if value is not None and not value.strip():
            raise ValueError("Text cannot be blank")
        return value

    @model_validator(mode="after")
    def exactly_one(self) -> "Injection":
        """Require exactly one of preset_id or text."""
        if (self.preset_id is None) == (self.text is None):
            raise ValueError("Give exactly one of preset_id or text")
        return self


class ModeChange(BaseModel):
    """Start a fresh run under one of the two decision policies."""
    mode: Literal["protected", "baseline"]


class NewIncident(BaseModel):
    """A call the judge drops on the map; the engine gives it the next J-id."""
    type: Literal["medical", "fire", "crash", "hazard", "shooting"]
    severity: int = Field(ge=1, le=5)
    location: dict
    label: str | None = Field(default=None, max_length=200)
    source: Literal["911", "caller"] = "911"


class IncidentChange(BaseModel):
    """Later details for a call already on the map (the voice dispatcher's follow-up update_incident)."""
    severity: int | None = Field(default=None, ge=1, le=5)
    label: str | None = Field(default=None, max_length=200)
    location: dict | None = None


class HumanResolution(BaseModel):
    """An explicit human choice of vehicle targets."""
    assignments: dict[str, str | None] = Field(min_length=1, max_length=7)


def create_app(engine: Engine | None = None) -> FastAPI:
    """Build the app and own engine startup/shutdown through its lifespan."""
    @asynccontextmanager
    async def lifespan(app):
        active = engine
        if active is None:
            llm_mode = os.getenv("AGNT911_LLM", "fake")
            if llm_mode not in ("fake", "real"):
                raise ValueError("AGNT911_LLM must be fake or real")
            if llm_mode == "real":
                from dotenv import load_dotenv
                load_dotenv()
                if not os.getenv("ANTHROPIC_API_KEY"):
                    raise RuntimeError("Real mode requires ANTHROPIC_API_KEY in the environment or backend/.env")
            path = os.getenv("AGNT911_SCENARIO")
            active = Engine(log_dir=os.getenv("AGNT911_LOG_DIR", "logs"),
                            scenario=load_scenario(path) if path else None,
                            llm_fn=fake_llm if llm_mode == "fake" else None,
                            tick_interval_s=float(os.getenv("AGNT911_TICK_SECONDS", "1")),
                            clock=os.getenv("AGNT911_CLOCK", "stepped"),
                            service_s=float(os.getenv("AGNT911_SERVICE_SECONDS", "20")),
                            seed=int(os.environ["AGNT911_SEED"]) if "AGNT911_SEED" in os.environ else None)
        app.state.engine = active
        await active.start()
        try:
            yield
        finally:
            await active.stop()

    app = FastAPI(title="Agnt911", lifespan=lifespan)
    origins = os.getenv("AGNT911_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    app.add_middleware(CORSMiddleware, allow_origins=[v.strip() for v in origins.split(",") if v.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/state")
    async def state(request: Request):
        """Serve the shared World fields and current engine metadata for polling."""
        return await request.app.state.engine.state()

    @app.post("/inject")
    async def inject(body: Injection, request: Request):
        """Accept a judge override for an existing vehicle."""
        try:
            await request.app.state.engine.inject(body.vehicle_id, body.text, preset_id=body.preset_id)
        except KeyError:
            raise HTTPException(404, "Unknown vehicle") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"accepted": True, "vehicle_id": body.vehicle_id}

    @app.post("/mode")
    async def mode(body: ModeChange, request: Request):
        """Reset the scenario with the selected baseline flag."""
        await request.app.state.engine.set_mode(body.mode)
        return await request.app.state.engine.state()

    @app.post("/incident")
    async def incident(body: NewIncident, request: Request):
        """Add a judge-dropped call (id J1, J2, ...) and schedule its negotiation."""
        eng = request.app.state.engine
        n = 1 + sum(1 for iid in eng.world.incidents if iid.startswith("J"))
        while f"J{n}" in eng.world.incidents:
            n += 1
        try:
            await eng.add_incident({"id": f"J{n}", "type": body.type, "severity": body.severity,
                                    "location": body.location, "label": body.label, "source": body.source})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"accepted": True, "incident": f"J{n}"}

    @app.post("/incident/{incident_id}")
    async def change_incident(incident_id: str, body: IncidentChange, request: Request):
        """Update severity, label or location of a call already on the map; the fleet renegotiates it."""
        try:
            await request.app.state.engine.update_incident(incident_id, body.model_dump(exclude_none=True))
        except KeyError:
            raise HTTPException(404, "Unknown incident") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"accepted": True, "incident": incident_id}

    @app.get("/voice/token")
    async def voice_token():
        """Fetch a WebRTC conversation token for the ElevenLabs dispatcher agent; the API key never leaves the backend."""
        from dotenv import load_dotenv
        load_dotenv()
        key, agent = os.getenv("ELEVENLABS_API_KEY"), os.getenv("ELEVENLABS_AGENT_ID")
        if not key or not agent:
            raise HTTPException(503, "Set ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID in backend/.env")
        req = urllib.request.Request(f"{ELEVENLABS_TOKEN_URL}?agent_id={urllib.parse.quote(agent)}", headers={"xi-api-key": key})
        try:
            body = await asyncio.to_thread(lambda: json.load(urllib.request.urlopen(req, timeout=10)))
        except (urllib.error.URLError, ValueError) as exc:
            raise HTTPException(502, f"ElevenLabs token request failed: {exc}") from None
        return {"token": body["token"]}

    @app.post("/resolve")
    async def resolve(body: HumanResolution, request: Request):
        """Apply human assignments only during a pending escalation."""
        try:
            await request.app.state.engine.resolve(body.assignments)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return await request.app.state.engine.state()

    return app


app = create_app()
