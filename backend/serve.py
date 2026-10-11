"""Hosted entry point: the engine's app plus gzip, so each /state poll ships ~55 KB instead of ~466 KB."""
from fastapi.middleware.gzip import GZipMiddleware

from agnt911.engine.api import app

app.add_middleware(GZipMiddleware, minimum_size=1000)
