"""IN-04: fenetre_mediane_commune maps transient SQL failures to 503."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError

from app.commune.queries import fenetre_mediane_commune


@pytest.mark.parametrize("sqlstate", ["57014", "42P01"])
async def test_fenetre_mediane_transient_sql_error_is_503(sqlstate):
    class DatabaseTestError(Exception):
        pass

    original = DatabaseTestError("synthetic SQL must not leak")
    original.sqlstate = sqlstate
    session = AsyncMock()
    session.execute.side_effect = DBAPIError(
        "SELECT synthetic_private_payload", {}, original
    )

    with pytest.raises(HTTPException) as excinfo:
        await fenetre_mediane_commune(session)

    assert excinfo.value.status_code == 503
    assert excinfo.value.detail == "Synthèse communale temporairement indisponible."
    assert "synthetic_private_payload" not in str(excinfo.value.detail)


async def test_fenetre_mediane_other_sql_error_is_reraised():
    class DatabaseTestError(Exception):
        pass

    original = DatabaseTestError("synthetic SQL must not leak")
    original.sqlstate = "42601"
    error = DBAPIError("SELECT synthetic_private_payload", {}, original)
    session = AsyncMock()
    session.execute.side_effect = error

    with pytest.raises(DBAPIError) as excinfo:
        await fenetre_mediane_commune(session)

    assert excinfo.value is error
