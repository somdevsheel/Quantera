import sys
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from api.main import create_app  # noqa: E402
from api.settings import Settings  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(create_app(Settings())) as c:
        yield c


@pytest.fixture(scope="session")
def redistribution_client():
    with TestClient(create_app(Settings(mode="redistribution"))) as c:
        yield c


@pytest.fixture(scope="session")
def lake(client):
    return client.app.state.lake


@pytest.fixture(scope="session")
def duck():
    return duckdb.connect()
