"""Platform tests run against real PostgreSQL in a throwaway schema, created through the
Alembic migrations (so every run also tests the migrations) and dropped afterwards."""

from __future__ import annotations

import sys
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from quantera.api.app import create_app  # noqa: E402
from quantera.core.config import Settings, get_settings  # noqa: E402
from quantera.core.db import Database  # noqa: E402
from quantera.lake.client import LakeClient  # noqa: E402

PASSWORD = "correct horse battery staple"


@pytest.fixture(scope="session")
def schema() -> Iterator[str]:
    name = f"test_{uuid.uuid4().hex[:12]}"
    base = get_settings()
    engine = create_engine(base.database_url)
    with engine.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{name}"'))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["schema"] = name
    command.upgrade(cfg, "head")
    try:
        yield name
    finally:
        with engine.begin() as c:
            c.execute(text(f'DROP SCHEMA "{name}" CASCADE'))
        engine.dispose()


@pytest.fixture
def settings(schema: str) -> Settings:
    return Settings(environment="test", db_schema=schema,  # type: ignore[call-arg]
                    auth_rate_limit_per_minute=1000)


@pytest.fixture(scope="session")
def lake() -> Iterator[LakeClient]:
    """The real lake API (latest frozen release), served in-process: no mocks."""
    from api.main import create_app as create_lake_app
    from api.settings import Settings as LakeSettings

    with TestClient(create_lake_app(LakeSettings())) as lake_http:
        yield LakeClient("", http=lake_http)


@pytest.fixture
def client(settings: Settings, lake: LakeClient) -> Iterator[TestClient]:
    db = Database(settings.database_url, settings.db_schema)
    with TestClient(create_app(settings, db, lake)) as c:
        yield c
    db.engine.dispose()


def register(client: TestClient, email: str | None = None) -> dict[str, Any]:
    email = email or f"user-{uuid.uuid4().hex[:8]}@example.com"
    r = client.post("/api/v1/auth/register",
                    json={"email": email, "password": PASSWORD, "full_name": "Test User"})
    assert r.status_code == 201, r.text
    body: dict[str, Any] = r.json()
    body["email"] = email
    body["headers"] = {"Authorization": f"Bearer {body['access_token']}"}
    return body


@pytest.fixture
def user(client: TestClient) -> dict[str, Any]:
    return register(client)
