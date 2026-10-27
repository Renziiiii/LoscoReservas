"""Shared fixtures: an isolated app instance with a temporary SQLite database."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TEST_USERS = "valen:testpass123"


def _make_settings(tmp_path) -> Settings:
    return Settings(
        database_path=tmp_path / "reservas.db",
        users=TEST_USERS,
        jwt_secret="test-secret-0123456789abcdef0123456789abcdef",
        _env_file=None,
    )


@pytest.fixture()
def anon_client(tmp_path):
    """Booted app + TestClient WITHOUT authentication (for 401 checks)."""
    app = create_app(_make_settings(tmp_path))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def client(anon_client):
    """Same client, authenticated as the seeded test user."""
    response = anon_client.post(
        "/api/auth/login",
        data={"username": "valen", "password": "testpass123"},
    )
    assert response.status_code == 200, "login failed during fixture setup"
    anon_client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    return anon_client


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "reservas.db"
