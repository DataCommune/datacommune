"""A login redirect, production response or stale schema cannot qualify a release."""

import importlib.util
import io
import json
import urllib.error
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "verify_preproduction",
    Path(__file__).resolve().parents[3] / "scripts/verify_preproduction.py",
)
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)
SHA = "a" * 40


def payload(**changes):
    return {
        "environment": "preproduction",
        "deployment_sha": SHA,
        "schema_actual": "094",
        "schema_expected": "094",
        **changes,
    }


def test_same_candidate_and_schema_is_qualified():
    assert module.validate_versions(payload(), payload(), SHA) == "094"


@pytest.mark.parametrize(
    "changes",
    [
        {"environment": "production"},
        {"deployment_sha": "b" * 40},
        {"schema_actual": "093"},
        {"schema_actual": ""},
        {"schema_actual": ["094"]},
        {"schema_expected": None},
    ],
)
def test_api_mismatch_blocks_qualification(changes):
    with pytest.raises(ValueError):
        module.validate_versions(payload(), payload(**changes), SHA)


def test_old_frontend_blocks_qualification():
    with pytest.raises(ValueError):
        module.validate_versions(payload(deployment_sha="b" * 40), payload(), SHA)


def test_redirect_handler_never_follows_access_login():
    assert (
        module.NoRedirect().redirect_request(
            None, None, 302, "Found", {}, "https://login"
        )
        is None
    )


def test_missing_access_credentials_fail_before_network():
    class NoNetwork:
        def open(self, *_args, **_kwargs):
            pytest.fail("Must fail before any request")

    with pytest.raises(ValueError):
        module.read_json(
            "https://dev.datacommune.fr/__version", {"token": ""}, NoNetwork()
        )


class FakeResponse:
    def __init__(self, status=200, content_type="application/json", body=b"{}"):
        self.status = status
        self.headers = type(
            "Headers", (), {"get_content_type": lambda self: content_type}
        )()
        self._body = io.BytesIO(body)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount):
        return self._body.read(amount)


class FakeOpener:
    def __init__(self, result):
        self.result = result

    def open(self, *_args, **_kwargs):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("status", [302, 401, 403])
def test_anonymous_access_refusal_is_positive_evidence(status):
    error = urllib.error.HTTPError("redacted", status, "refused", {}, None)
    assert (
        module.assert_anonymous_refused("https://dev.invalid", FakeOpener(error))
        == status
    )


def test_anonymous_http_200_is_never_accepted_as_closed_access():
    with pytest.raises(ValueError):
        module.assert_anonymous_refused(
            "https://dev.invalid", FakeOpener(FakeResponse(status=200))
        )


@pytest.mark.parametrize(
    ("response", "expected_error"),
    [
        (FakeResponse(status=302), "Expected an authenticated HTTP 200"),
        (FakeResponse(content_type="text/html"), "Expected application JSON"),
        (FakeResponse(body=b"x" * 65_537), "Unexpectedly large"),
        (FakeResponse(body=b"not-json"), None),
        (
            FakeResponse(body=json.dumps(["not", "object"]).encode()),
            "application object",
        ),
    ],
)
def test_authenticated_proof_rejects_redirect_html_large_or_invalid_body(
    response, expected_error
):
    credentials = {"id": "present", "secret": "present"}
    with pytest.raises((ValueError, json.JSONDecodeError)) as caught:
        module.read_json("https://dev.invalid", credentials, FakeOpener(response))
    if expected_error:
        assert expected_error in str(caught.value)


def test_candidate_must_be_full_sha():
    with pytest.raises(ValueError):
        module.validate_versions(payload(), payload(), "abc1234")


@pytest.mark.parametrize("authenticated", [False, True])
def test_cloudflare_probe_uses_explicit_user_agent(authenticated):
    class UserAgentGate:
        def open(self, request, **_kwargs):
            assert request.get_header("User-agent") == (
                "DataCommune-Preproduction-Check/1.0"
            )
            if authenticated:
                assert request.get_header("Cf-access-client-id") == "test-id"
                return FakeResponse(body=b'{"environment":"preproduction"}')
            assert request.get_header("Cf-access-client-id") is None
            raise urllib.error.HTTPError("redacted", 403, "refused", {}, None)

    if authenticated:
        assert module.read_json(
            "https://dev.invalid",
            {"CF-Access-Client-Id": "test-id"},
            UserAgentGate(),
        ) == {"environment": "preproduction"}
    else:
        assert (
            module.assert_anonymous_refused("https://dev.invalid", UserAgentGate())
            == 403
        )
