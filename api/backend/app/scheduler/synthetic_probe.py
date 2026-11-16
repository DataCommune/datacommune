"""Private synthetic golden probe (HEALTH-02, D-01..D-04).

Replays an opaque golden ``case_id`` every day against the FINAL contract of
``app/resolve/router.py`` and asserts the D-03 critical block. It exists to
detect a real regression of the reference journey WITHOUT ever leaking a
private input.

Design (Phase 46 decisions):

- **D-01** — the fixture with the *exact* inputs lives OUTSIDE Git, on the
  production host, mounted read-only. Its location is provided solely via the
  ``SYNTHETIC_PROBE_FIXTURE_PATH`` environment variable; the schema is closed
  and each ``case_id`` is opaque (no whitespace / address-looking text).
- **D-02** — the only values ever logged/alerted are: ``case_id``, a duration,
  invariant booleans and normalized error codes. The raw query, address,
  latitude/longitude, INSEE code, parcel id and raw upstream body are NEVER
  written to a log record or an alert.
- **D-03** — the minimal critical block is: a valid resolution identity, the
  expected ``code_insee``, the expected ``id_parcelle`` and the presence of the
  ``cadastre`` dataset block. ``dvf`` presence is required ONLY for a case
  explicitly flagged ``with_dvf``.
- **D-04** — the probe is wired as a daily ``run_job``/systemd job and reuses
  the existing Apprise alerting. It tests the invariants, not just the HTTP
  status. Any degradation (timeout, 4xx/5xx, false invariant, invalid fixture,
  or a failed Apprise delivery) raises / exits non-zero (fail-closed).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("scheduler.synthetic_probe")

AlertFn = Callable[[Sequence["ProbeFailure"]], bool]

# Opaque id: letters, digits, dot, dash, underscore, colon — no whitespace,
# no address punctuation. Keeps a private input from masquerading as an id.
_OPAQUE_CASE_ID = re.compile(r"^[A-Za-z0-9._:-]+$")

# Closed fixture schema.
_ALLOWED_TOP_KEYS = frozenset({"version", "cases"})
_REQUIRED_CASE_KEYS = frozenset(
    {"case_id", "query", "expected_code_insee", "expected_id_parcelle"}
)
_OPTIONAL_CASE_KEYS = frozenset({"datasets", "with_dvf", "type"})
_ALLOWED_CASE_KEYS = _REQUIRED_CASE_KEYS | _OPTIONAL_CASE_KEYS

_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_BASE_URL = "https://api.datacommune.fr"


# --------------------------------------------------------------------------- #
# Errors (all non-zero conditions)
# --------------------------------------------------------------------------- #
class ProbeError(Exception):
    """Base class for every fail-closed condition of the probe."""


class ProbeConfigError(ProbeError):
    """Fixture missing, unreadable or violating the closed schema."""


class ProbeFailed(ProbeError):  # noqa: N818 — "Failed" reads as a run outcome
    """One or more golden cases failed a D-03 invariant / HTTP / timeout check."""

    def __init__(self, failures: Sequence[ProbeFailure]) -> None:
        self.failures = list(failures)
        codes = ", ".join(f"{f.case_id}:{f.error_code}" for f in self.failures)
        super().__init__(
            f"synthetic probe failed for {len(self.failures)} case(s): {codes}"
        )


class ProbeAlertError(ProbeFailed):
    """The probe failed AND the Apprise alert could not be delivered."""

    def __init__(self, failures: Sequence[ProbeFailure]) -> None:
        super().__init__(failures)
        self.args = (f"{self.args[0]} — AND alert delivery failed",)


# --------------------------------------------------------------------------- #
# Value objects (D-02 allowlist only)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ProbeFailure:
    """A single failed case — carries only D-02 allowlisted fields."""

    case_id: str
    error_code: str
    duration_ms: int


@dataclass
class ProbeReport:
    """Result of a full probe run when every case passed."""

    cases_total: int = 0
    cases_passed: int = 0
    failures: list[ProbeFailure] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Fixture loading + closed-schema validation
# --------------------------------------------------------------------------- #
def _load_fixture(fixture_path: str | None) -> list[dict[str, Any]]:
    """Load and validate the private fixture. Never logs its contents."""
    path_str = fixture_path or os.environ.get("SYNTHETIC_PROBE_FIXTURE_PATH", "")
    if not path_str:
        raise ProbeConfigError("SYNTHETIC_PROBE_FIXTURE_PATH is not set")
    path = Path(path_str)
    if not path.is_file():
        raise ProbeConfigError("fixture file not found")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        # Redact: never surface the file body or path detail.
        raise ProbeConfigError(f"fixture unreadable ({type(exc).__name__})") from None

    if not isinstance(raw, dict) or set(raw) - _ALLOWED_TOP_KEYS:
        raise ProbeConfigError("fixture has unknown or missing top-level keys")
    cases = raw.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProbeConfigError("fixture has no cases")

    for case in cases:
        _validate_case(case)
    return cases


def _validate_case(case: Any) -> None:
    if not isinstance(case, dict):
        raise ProbeConfigError("a case is not an object")
    keys = set(case)
    unknown = keys - _ALLOWED_CASE_KEYS
    if unknown:
        raise ProbeConfigError(f"case has unknown keys: {sorted(unknown)}")
    missing = _REQUIRED_CASE_KEYS - keys
    if missing:
        raise ProbeConfigError(f"case is missing required keys: {sorted(missing)}")
    case_id = case["case_id"]
    if not isinstance(case_id, str) or not _OPAQUE_CASE_ID.match(case_id):
        # Do NOT echo the offending value — it may be a private input.
        raise ProbeConfigError("case_id is not an opaque token")
    if not isinstance(case["query"], str) or not case["query"]:
        raise ProbeConfigError(f"case {case_id}: query must be a non-empty string")
    datasets = case.get("datasets", ["cadastre"])
    if not isinstance(datasets, list) or not all(isinstance(d, str) for d in datasets):
        raise ProbeConfigError(f"case {case_id}: datasets must be a list of strings")
    if not isinstance(case.get("with_dvf", False), bool):
        raise ProbeConfigError(f"case {case_id}: with_dvf must be a boolean")


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #
def _build_client(*, base_url: str, api_key: str, timeout: float) -> httpx.Client:
    """Build the real HTTP client used against the live contract."""
    return httpx.Client(
        base_url=base_url,
        headers={"X-API-Key": api_key},
        timeout=timeout,
    )


# --------------------------------------------------------------------------- #
# Per-case D-03 evaluation
# --------------------------------------------------------------------------- #
def _dataset_present(datasets: Any, name: str) -> bool:
    if not isinstance(datasets, dict):
        return False
    block = datasets.get(name)
    return bool(block)


def _check_case(client: httpx.Client, case: dict[str, Any]) -> ProbeFailure | None:  # noqa: C901
    """Replay one case and evaluate the D-03 critical block.

    Returns ``None`` on success, or a redacted :class:`ProbeFailure`.
    Logs only D-02 fields (case_id, duration, invariant booleans, error code).
    """
    case_id = case["case_id"]
    datasets = case.get("datasets", ["cadastre"])
    if "cadastre" not in datasets:
        datasets = ["cadastre", *datasets]
    with_dvf = bool(case.get("with_dvf", False))
    if with_dvf and "dvf" not in datasets:
        datasets = [*datasets, "dvf"]

    params = {"q": case["query"], "datasets": ",".join(datasets)}
    if case.get("type"):
        params["type"] = case["type"]

    started = time.perf_counter()

    def _elapsed_ms() -> int:
        return int((time.perf_counter() - started) * 1000)

    def _fail(code: str) -> ProbeFailure:
        ms = _elapsed_ms()
        log.error(
            "probe_case_failed",
            extra={"case_id": case_id, "error_code": code, "duration_ms": ms},
        )
        return ProbeFailure(case_id=case_id, error_code=code, duration_ms=ms)

    try:
        resp = client.get("/v1/resolve", params=params)
    except httpx.TimeoutException:
        return _fail("timeout")
    except httpx.HTTPError as exc:
        # Redact: never str(exc) — it may embed the target URL / input.
        return _fail(f"transport_{type(exc).__name__}")

    if resp.status_code != 200:
        return _fail(f"http_{resp.status_code}")

    try:
        body = resp.json()
    except ValueError:
        return _fail("invalid_json")

    donnees = body.get("donnees") if isinstance(body, dict) else None
    # A well-formed exact resolution returns a NON-EMPTY LIST of objects. A 200
    # with a malformed shape (dict, scalar, empty) must fail closed here, never
    # crash on donnees[0] and bypass the alert path (fail-closed).
    if not isinstance(donnees, list) or not donnees:
        return _fail("identity_absent")
    result = donnees[0]
    if not isinstance(result, dict):
        return _fail("result_malformed")
    identity_ok = True
    resolution_ok = result.get("resolution") == "exact"
    code_insee_ok = result.get("code_insee") == case["expected_code_insee"]
    id_parcelle_ok = result.get("id_parcelle") == case["expected_id_parcelle"]
    result_datasets = result.get("datasets")
    cadastre_ok = _dataset_present(result_datasets, "cadastre")
    dvf_ok = (not with_dvf) or _dataset_present(result_datasets, "dvf")

    # D-02: log only the invariant booleans + duration, never the values.
    log.info(
        "probe_case_checked",
        extra={
            "case_id": case_id,
            "duration_ms": _elapsed_ms(),
            "identity_ok": identity_ok,
            "resolution_ok": resolution_ok,
            "code_insee_ok": code_insee_ok,
            "id_parcelle_ok": id_parcelle_ok,
            "cadastre_ok": cadastre_ok,
            "dvf_ok": dvf_ok,
        },
    )

    if not resolution_ok:
        return _fail("resolution_not_exact")
    if not code_insee_ok:
        return _fail("code_insee_mismatch")
    if not id_parcelle_ok:
        return _fail("id_parcelle_mismatch")
    if not cadastre_ok:
        return _fail("cadastre_absent")
    if not dvf_ok:
        return _fail("dvf_absent")
    return None


# --------------------------------------------------------------------------- #
# Alerting (reuses the existing Apprise integration, D-04)
# --------------------------------------------------------------------------- #
def _send_apprise_alert(failures: Sequence[ProbeFailure]) -> bool:
    """Send a sanitized alert via Apprise. Returns delivery success.

    The body carries ONLY case_id + error_code + duration (D-02). If no alert
    URL is configured there is nothing to deliver — treated as a delivery
    failure so the run stays non-zero (fail-closed).
    """
    if not settings.alert_urls:
        log.error("probe_alert_no_channel_configured")
        return False

    import apprise

    ap = apprise.Apprise()
    for url in settings.alert_urls:
        ap.add(url)
    lines = [f"  {f.case_id}: {f.error_code} ({f.duration_ms} ms)" for f in failures]
    delivered = ap.notify(
        title="Synthetic golden probe FAILED",
        body="Golden reference journey regressed:\n" + "\n".join(lines),
        notify_type=apprise.NotifyType.FAILURE,
    )
    return bool(delivered)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_probe(
    *,
    fixture_path: str | None = None,
    client: httpx.Client | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    alert: AlertFn | None = None,
) -> ProbeReport:
    """Replay every golden case; raise on any degradation (fail-closed).

    Returns a :class:`ProbeReport` only when ALL cases pass. On any failure the
    alert is dispatched (D-04) and a :class:`ProbeError` is raised so the
    process exits non-zero. A failed alert delivery raises
    :class:`ProbeAlertError`.
    """
    # D-02 redaction: httpx/httpcore log the full request URL (which carries the
    # private ``q=`` input) at INFO. Silence them so the query never reaches a
    # log record via a third-party logger.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    cases = _load_fixture(fixture_path)
    alert_fn = alert or _send_apprise_alert

    owns_client = client is None
    if owns_client:
        client = _build_client(
            base_url=base_url
            or os.environ.get("SYNTHETIC_PROBE_BASE_URL")
            or settings.api_base_url
            or _DEFAULT_BASE_URL,
            api_key=api_key
            if api_key is not None
            else os.environ.get("SYNTHETIC_PROBE_API_KEY", ""),
            timeout=timeout
            if timeout is not None
            else float(os.environ.get("SYNTHETIC_PROBE_TIMEOUT_S", _DEFAULT_TIMEOUT_S)),
        )

    failures: list[ProbeFailure] = []
    try:
        for case in cases:
            try:
                failure = _check_case(client, case)
            except Exception:  # noqa: BLE001
                # Fail closed on ANY unexpected error: a crash must never bypass
                # the alert path. Redact:
                # log only the opaque, already-validated case_id + a generic code.
                case_id = case["case_id"]
                log.error(  # noqa: TRY400
                    "probe_case_crashed",
                    extra={"case_id": case_id, "error_code": "unexpected_error"},
                )
                failure = ProbeFailure(
                    case_id=case_id, error_code="unexpected_error", duration_ms=0
                )
            if failure is not None:
                failures.append(failure)
    finally:
        if owns_client:
            client.close()

    report = ProbeReport(
        cases_total=len(cases),
        cases_passed=len(cases) - len(failures),
        failures=failures,
    )
    if failures:
        log.error(
            "probe_run_failed",
            extra={"failed": len(failures), "total": len(cases)},
        )
        delivered = alert_fn(failures)
        if not delivered:
            raise ProbeAlertError(failures)
        raise ProbeFailed(failures)

    log.info("probe_run_passed", extra={"total": len(cases)})
    return report


def run_synthetic_probe() -> None:
    """``run_job`` entrypoint: raise on any degradation (non-zero exit)."""
    run_probe()


def main() -> int:
    """CLI wrapper: 0 on success, 1 on any fail-closed condition."""
    try:
        run_probe()
    except ProbeError:
        # Already logged with redacted D-02 fields; the message carries only
        # case_id + error_code (opaque), never a private value.
        log.error("synthetic probe exited non-zero")  # noqa: TRY400
        return 1
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
