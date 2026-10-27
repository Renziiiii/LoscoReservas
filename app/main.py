"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401 — registers models on Base before create_all
from app.config import Settings
from app.db import Base, make_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        yield

    app = FastAPI(
        title="LoscoReservas API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.settings = settings

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        """Liveness probe used by Docker and monitoring."""
        return {"status": "ok"}

    return app


app = create_app()
