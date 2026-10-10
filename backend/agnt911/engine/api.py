"""Single-process FastAPI server. Poll state; no websockets or database."""

from contextlib import asynccontextmanager
import importlib
import os
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import AliasChoices, BaseModel, Field, field_validator

from .engine import Engine


class Injection(BaseModel):
    vehicle_id: str
    instruction: str = Field(min_length=1, max_length=2000,
                             validation_alias=AliasChoices("instruction", "text"))

    @field_validator("instruction")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Instruction cannot be blank")
        return value


class ModeChange(BaseModel):
    mode: Literal["protected", "baseline"]


class HumanResolution(BaseModel):
    assignments: dict[str, str | None] = Field(min_length=1, max_length=7)


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        active = engine
        if active is None:
            adapter_name = os.getenv("AGNT911_ADAPTER")
            adapter = importlib.import_module(adapter_name) if adapter_name else None
            active = Engine(log_dir=os.getenv("AGNT911_LOG_DIR", "logs"),
                            agents=adapter.agents if adapter else None,
                            protocol=adapter.run_protocol if adapter else None,
                            seed=int(os.getenv("AGNT911_SEED", "911")))
        app.state.engine = active
        await active.start()
        try:
            yield
        finally:
            await active.stop()

    app = FastAPI(title="Agnt911 core", lifespan=lifespan)
    origins = os.getenv("AGNT911_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    app.add_middleware(CORSMiddleware, allow_origins=[v.strip() for v in origins.split(",") if v.strip()],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/state")
    async def state(request: Request):
        return await request.app.state.engine.state()

    @app.post("/inject")
    async def inject(body: Injection, request: Request):
        try:
            await request.app.state.engine.inject(body.vehicle_id, body.instruction)
        except KeyError:
            raise HTTPException(404, "Unknown vehicle") from None
        return {"accepted": True, "vehicle_id": body.vehicle_id}

    @app.post("/mode")
    async def mode(body: ModeChange, request: Request):
        await request.app.state.engine.set_mode(body.mode)
        return await request.app.state.engine.state()

    @app.post("/resolve")
    async def resolve(body: HumanResolution, request: Request):
        try:
            await request.app.state.engine.resolve(body.assignments)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return await request.app.state.engine.state()

    return app


app = create_app()
