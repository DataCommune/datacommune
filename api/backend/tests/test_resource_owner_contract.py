"""Exact disposable project ownership; Docker and every DB connection are mocked."""

import json

import psycopg
import pytest

from tests.integration import test_publication_restore as restore

pytestmark = pytest.mark.unit
OWNED = "dc-verify-00112233445566778899aabbccddeeff-01"
DSN = "postgresql://contract_test:synthetic@127.0.0.1:49991/contract_test"


@pytest.fixture(autouse=True)
def forbid_database(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No database access in ownership unit tests")

    monkeypatch.setattr(psycopg, "connect", forbidden)
    monkeypatch.setattr(psycopg.AsyncConnection, "connect", forbidden)


def container(project, port=49991, identifier="owned"):
    return {
        "Id": identifier,
        "Config": {"Labels": {"com.docker.compose.project": project}},
        "NetworkSettings": {
            "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(port)}]}
        },
    }


def simulate(monkeypatch, rows):
    commands = []

    def command(args, audit):
        commands.append(args)
        if args[1] == "ps":
            return b"owned foreign"
        if args[1] == "inspect":
            return json.dumps(rows).encode()
        raise AssertionError("Unexpected command")

    monkeypatch.setattr(restore, "_command", command)
    return commands


def test_exact_owner_selected_even_when_foreign_project_exposes_same_port(monkeypatch):
    monkeypatch.setenv("DC_TEST_PROJECT", OWNED)
    commands = simulate(
        monkeypatch,
        [container(OWNED), container(OWNED + "-foreign", identifier="foreign")],
    )
    assert restore._owned_container(DSN, []) == "owned"
    assert f"label=com.docker.compose.project={OWNED}" in commands[0]


@pytest.mark.parametrize(
    "rows",
    [
        [container("dc-contract-test-other")],
        [container(OWNED + "-other")],
        [container(OWNED, port=49992)],
        [container(OWNED), container(OWNED, identifier="duplicate")],
    ],
)
def test_non_owned_wrong_port_or_ambiguous_container_refused(monkeypatch, rows):
    monkeypatch.setenv("DC_TEST_PROJECT", OWNED)
    simulate(monkeypatch, rows)
    with pytest.raises(AssertionError, match="one newly wrapper-owned"):
        restore._owned_container(DSN, [])


def test_missing_owner_refused_before_any_docker_command(monkeypatch):
    monkeypatch.delenv("DC_TEST_PROJECT", raising=False)
    commands = simulate(monkeypatch, [])
    with pytest.raises(AssertionError, match="Exact wrapper-owned"):
        restore._owned_container(DSN, [])
    assert commands == []


def test_remote_dsn_refused_before_any_docker_command(monkeypatch):
    monkeypatch.setenv("DC_TEST_PROJECT", OWNED)
    commands = simulate(monkeypatch, [])
    with pytest.raises(AssertionError):
        restore._owned_container(DSN.replace("127.0.0.1", "foreign.invalid"), [])
    assert commands == []
