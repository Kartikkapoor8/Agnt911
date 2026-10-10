"""Single-process FastAPI endpoints using the team's shared backend package."""

from contextlib import asynccontextmanager
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from ..fake_llm import fake_llm
from .runtime import Engine
from .world import load_scenario


class Injection(BaseModel):
    """The team's existing judge payload: vehicle_id and text."""
    vehicle_id: str
    text: str = Field(min_length=1, max_length=2000)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        """Reject whitespace-only judge instructions."""
        if not value.strip():
            raise ValueError("Text cannot be blank")
        return value


class ModeChange(BaseModel):
    """Start a fresh run under one of the two decision policies."""
    mode: Literal["protected", "baseline"]


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
            await request.app.state.engine.inject(body.vehicle_id, body.text)
        except KeyError:
            raise HTTPException(404, "Unknown vehicle") from None
        return {"accepted": True, "vehicle_id": body.vehicle_id}

    @app.post("/mode")
    async def mode(body: ModeChange, request: Request):
        """Reset the scenario with the selected baseline flag."""
        await request.app.state.engine.set_mode(body.mode)
        return await request.app.state.engine.state()

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
