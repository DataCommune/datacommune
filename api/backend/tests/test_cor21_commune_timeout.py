"""OPS-12: real HTTP route must distinguish timeout from empty data and SQL bugs."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import DBAPIError


@pytest.mark.parametrize(
    "sqlstate,expected", [("57014", 503), ("42P01", 503), ("42601", 500)]
)
async def test_commune_sql_error_http_contract(client, monkeypatch, sqlstate, expected):
    from app.database import get_db
    from app.main import app
    from app.middleware import RequestLoggingMiddleware

    class DatabaseTestError(Exception):
        pass

    original = DatabaseTestError("synthetic SQL must not leak")
    original.sqlstate = sqlstate
    session = AsyncMock()
    session.execute.side_effect = [
        SimpleNamespace(scalar_one=lambda: True),
        DBAPIError("SELECT synthetic_private_payload", {}, original),
    ]

    async def db():
        yield session

    app.dependency_overrides[get_db] = db
    monkeypatch.setattr(RequestLoggingMiddleware, "_log_to_db", AsyncMock())
    try:
        response = await client.get("/v1/commune", params={"code_insee": "44999"})
        assert response.status_code == expected
        assert "synthetic_private_payload" not in response.text
        assert "donnees" not in response.json()
        if expected == 503:
            assert "temporairement indisponible" in response.text
    finally:
        app.dependency_overrides.pop(get_db, None)
