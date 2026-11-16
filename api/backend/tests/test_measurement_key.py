"""scripts/measurement_key.py: ephemeral key, revoked whatever happens."""

import pytest

from scripts import measurement_key as mk

pytestmark = pytest.mark.unit


class FakeConn:
    """In-memory api_keys / quota_usage enough for the SQL of the script."""

    def __init__(self, keys=None):
        self.keys = list(keys or [])
        self.usage = {}
        self._result = None

    def execute(self, query, params=()):
        q = " ".join(query.split())
        if q.startswith("SELECT id FROM api_keys WHERE name"):
            self._result = [
                {"id": k["id"]}
                for k in self.keys
                if k["name"] == params[0]
                and k["status"] == "active"
                and not k["revoked"]
            ]
        elif q.startswith("INSERT INTO api_keys"):
            digest, name, quota, prefix = params
            row = {
                "id": len(self.keys) + 100,
                "key_hash": digest,
                "name": name,
                "quota_monthly": quota,
                "key_prefix": prefix,
                "status": "active",
                "revoked": False,
                "user_id": None,
                "environment": "live",
                "is_default": False,
            }
            self.keys.append(row)
            self._result = [{"id": row["id"]}]
        elif q.startswith("UPDATE api_keys"):
            key_id, name = params
            hit = [k for k in self.keys if k["id"] == key_id and k["name"] == name]
            for k in hit:
                k["status"], k["revoked"] = "revoked", True
            self._result = [
                {f: k[f] for f in ("id", "name", "status", "revoked", "quota_monthly")}
                for k in hit
            ]
        elif q.startswith("SELECT request_count FROM quota_usage"):
            value = self.usage.get(params)
            self._result = [{"request_count": value}] if value is not None else []
        else:
            raise AssertionError(q)
        return self

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return self._result


def test_created_key_is_named_bounded_and_never_internal():
    conn = FakeConn()
    created = mk.create_key(conn)
    row = conn.keys[0]
    assert row["name"] == "02-18 latency measurement"
    assert row["quota_monthly"] == 1000
    assert row["user_id"] is None and row["is_default"] is False
    assert created["plaintext"].startswith("dc_live_")
    assert (
        created["prefix"].endswith("...")
        and created["plaintext"] not in created["prefix"]
    )
    assert row["key_hash"] != created["plaintext"]


def test_a_second_active_measurement_key_is_refused():
    conn = FakeConn(
        [{"id": 7, "name": mk.KEY_NAME, "status": "active", "revoked": False}]
    )
    with pytest.raises(RuntimeError):
        mk.create_key(conn)


def _run(conn, measure, probe_status=403):
    env = {}
    seen = {}

    def fake_measure(argv):
        seen["argv"] = argv
        seen["key_in_env"] = env.get(mk.MEASURE_ENV)
        conn.usage[(conn.keys[-1]["id"], "2026-09")] = 42
        return measure()

    report = mk.run_with_key(
        lambda: conn,
        ["--base-url", "http://127.0.0.1:8000", "--out", "x.json"],
        measure=fake_measure,
        probe=lambda key: probe_status,
        month="2026-09",
        env=env,
    )
    return report, env, seen


def test_the_key_is_given_by_environment_and_revoked_after_the_measure():
    conn = FakeConn()
    report, env, seen = _run(conn, lambda: 0)
    assert seen["argv"][-2:] == ["--api-key-env", mk.MEASURE_ENV]
    assert seen["key_in_env"].startswith("dc_live_")
    assert mk.MEASURE_ENV not in env
    assert conn.keys[0]["revoked"] is True and conn.keys[0]["status"] == "revoked"
    assert report["revocation"]["status"] == "revoked"
    assert report["usage_avant"] == 0 and report["usage_apres"] == 42
    assert report["sonde_ancienne_cle"] == 403
    assert report["mesure_code_sortie"] == 0
    assert seen["key_in_env"] not in repr(report)


def test_the_key_is_revoked_when_the_measure_fails():
    conn = FakeConn()

    def boom():
        raise RuntimeError("network down")

    report, env, _ = _run(conn, boom)
    assert conn.keys[0]["revoked"] is True
    assert report["mesure_code_sortie"] == "exception:RuntimeError"
    assert mk.MEASURE_ENV not in env


def test_an_accepted_old_key_is_reported_as_a_failed_revocation():
    conn = FakeConn()
    report, _, _ = _run(conn, lambda: 0, probe_status=200)
    assert report["revocation_prouvee"] is False


def test_revoke_only_touches_the_measurement_key():
    conn = FakeConn(
        [
            {
                "id": 1,
                "name": "Synthetic golden probe key",
                "status": "active",
                "revoked": False,
            }
        ]
    )
    with pytest.raises(RuntimeError):
        mk.revoke_key(conn, 1)
    assert conn.keys[0]["revoked"] is False
