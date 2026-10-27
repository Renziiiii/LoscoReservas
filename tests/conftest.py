"""Shared fixtures: an isolated app instance with a temporary SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path):
    """Booted app + TestClient over a throwaway database file."""
    settings = Settings(database_path=tmp_path / "reservas.db")
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "reservas.db"
