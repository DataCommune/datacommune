"""Credential generation policy, independent of password hashing implementation."""

import pytest

from app.auth import service

pytestmark = pytest.mark.unit


def test_reset_nonces_are_distinct_and_legacy_links_refused():
    first = service.create_reset_token("user@example.com", "credential-one")
    second = service.create_reset_token("user@example.com", "credential-one")
    assert first != second
    assert "credential-one" not in first
    legacy = service._reset_serializer.dumps("user@example.com", salt="password-reset")
    assert service.verify_reset_token(legacy) is None


def test_old_and_unversioned_sessions_require_login():
    old = {"credential_version": service.credential_version("credential-one")}
    assert service.session_matches_credential(old, {"password_hash": "credential-one"})
    assert not service.session_matches_credential(
        old, {"password_hash": "credential-two"}
    )
    assert not service.session_matches_credential(
        {}, {"password_hash": "credential-one"}
    )
