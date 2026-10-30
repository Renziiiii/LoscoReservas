"""FastAPI application factory."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401 — registers models on Base before create_all
from app.config import Settings
from app.db import Base, make_engine
from app.auth import router as auth_router, get_current_user
from app.dash import public_router as dash_public_router, router as dash_router
from app.reservas import router as reservas_router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    engine = make_engine(settings.database_path)
    scheduler = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal scheduler
        Base.metadata.create_all(engine)
        if settings.scheduler_enabled:
            from app.scheduler import build_scheduler

            scheduler = build_scheduler(app, settings)
            scheduler.start()
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(
        title="LoscoReservas API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.sessionmaker = sessionmaker(engine)
    app.state.settings = settings
    app.include_router(auth_router)
    app.include_router(dash_public_router)
    app.include_router(dash_router)
    app.include_router(reservas_router, dependencies=[Depends(get_current_user)])

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/api/health", tags=["meta"])
    def health() -> dict:
        """Liveness probe used by Docker and monitoring."""
        return {"status": "ok"}

    return app


app = create_app()
