"""Unit tests: proxy_error_boundary maps proxy failures to the ADR-0004 contract."""

import pytest
from fastapi import HTTPException

from app.common.exceptions import ErreurApiError, UpstreamUnavailableError
from app.common.proxy import proxy_error_boundary

pytestmark = pytest.mark.unit


async def test_upstream_down_becomes_503_with_default_retry_after():
    with pytest.raises(ErreurApiError) as ei:
        async with proxy_error_boundary("Sirene"):
            raise UpstreamUnavailableError("Sirene", "timeout")
    assert ei.value.code == 503
    assert ei.value.headers == {"Retry-After": "60"}


async def test_retry_after_override_is_honored():
    with pytest.raises(ErreurApiError) as ei:
        async with proxy_error_boundary("BDNB", retry_after=120):
            raise UpstreamUnavailableError("BDNB")
    assert ei.value.headers["Retry-After"] == "120"


async def test_business_http_exception_passes_through_untouched():
    with pytest.raises(HTTPException) as ei:
        async with proxy_error_boundary("BDNB"):
            raise HTTPException(status_code=422, detail="Fournir adresse OU parcelle")
    assert ei.value.status_code == 422
    assert ei.value.detail == "Fournir adresse OU parcelle"


async def test_unexpected_error_becomes_502():
    with pytest.raises(ErreurApiError) as ei:
        async with proxy_error_boundary("RNB"):
            raise ValueError("malformed upstream payload")
    assert ei.value.code == 502
    assert ei.value.headers is None


async def test_clean_block_yields_normally():
    ran = False
    async with proxy_error_boundary("Georisques"):
        ran = True
    assert ran
