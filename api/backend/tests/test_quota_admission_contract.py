"""COR-18 public policy uses actual configured limits and verified rights."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.auth import dependencies
from app.auth.models import ApiKeyInfo
from app.billing.router import billing_plans
from app.config import settings

pytestmark = pytest.mark.unit


async def test_plans_report_effective_shared_ip_ceiling(monkeypatch):
    monkeypatch.setattr(settings, "rate_limit_per_ip", 30)
    plans = json.loads((await billing_plans()).body)
    assert plans["enterprise"]["rate_limit"] == 30
    assert plans["enterprise"]["plan_rate_limit"] == 500
    assert plans["free"]["rate_limit"] == 10
    assert plans["enterprise"]["shared_ip_rate_limit"] == 30
    assert plans["enterprise"]["quota_month"] == "UTC"
    monkeypatch.setattr(settings, "rate_limit_per_ip", 600)
    assert json.loads((await billing_plans()).body)["enterprise"]["rate_limit"] == 500


async def test_stale_key_quota_cannot_raise_free_account_rate(monkeypatch):
    monkeypatch.setattr(
        dependencies, "get_active_subscription", AsyncMock(return_value=None)
    )
    reserve = AsyncMock(return_value=1)
    monkeypatch.setattr(dependencies, "reserve_quota", reserve)
    rate = MagicMock(return_value=(True, 9, 60))
    monkeypatch.setattr(dependencies, "_check_rate", rate)
    key = ApiKeyInfo(
        id=12,
        name="test",
        quota_monthly=100000,
        user_id=1,
        revoked=False,
        created_at=datetime.now(UTC),
    )
    await dependencies._enforce_quota(MagicMock(), key, AsyncMock())
    rate.assert_called_once_with("plan:12", 10)
    assert reserve.call_args.kwargs["limit"] == 500
