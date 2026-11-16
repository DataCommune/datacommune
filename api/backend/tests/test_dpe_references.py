"""Opaque, target-bound DPE reference contracts (Phase 58.1, D-07/D-08)."""

import base64
import json
import zlib
from datetime import UTC, date, datetime

import itsdangerous.timed as dangerous_timed
import pytest

from app.dpe import references as refs
from app.dpe.references import (
    DPE_REFERENCE_MAX_AGE_SECONDS,
    DpeContextReference,
    DpeCursorReference,
    DpeDocumentReference,
    InvalidReferenceError,
    RefreshRequiredError,
    issue_context,
    issue_cursor,
    issue_document,
    verify_context,
    verify_cursor,
    verify_document,
)

pytestmark = pytest.mark.unit

_ADDRESS_ID = 731
_DEPARTMENT = "44"
_BAN_ID = "44109_zz01_00002"
_ADDRESSES_GENERATION = 16543
_DIAGNOSTICS_GENERATION = 16544
_DOCUMENT_ID = 90210
_DPE_NUMBER = "DPE-SECRET-ADEME-0001"


def _issue_context(**overrides: object) -> str:
    values: dict[str, object] = {
        "address_id": _ADDRESS_ID,
        "department": _DEPARTMENT,
        "ban_id": _BAN_ID,
        "addresses_generation": _ADDRESSES_GENERATION,
        "diagnostics_generation": _DIAGNOSTICS_GENERATION,
    }
    values.update(overrides)
    return issue_context(**values)  # type: ignore[arg-type]


def _decode_unsigned_payload(reference: str) -> dict[str, object]:
    """Decode only the readable payload, without validating its signature."""
    value = reference.rsplit(".", 2)[0]
    compressed = value.startswith(".")
    encoded = value[1:] if compressed else value
    encoded += "=" * (-len(encoded) % 4)
    raw = base64.urlsafe_b64decode(encoded)
    if compressed:
        raw = zlib.decompress(raw)
    payload = json.loads(raw)
    assert isinstance(payload, dict)
    return payload


def _tamper(reference: str) -> str:
    offset = 1 if reference.startswith(".") else 0
    replacement = "A" if reference[offset] != "A" else "B"
    return reference[:offset] + replacement + reference[offset + 1 :]


def test_reference_max_age_is_exactly_thirty_minutes():
    assert DPE_REFERENCE_MAX_AGE_SECONDS == 1800


def test_context_round_trip_keeps_only_technical_target_data():
    decoded = verify_context(_issue_context())

    assert decoded == DpeContextReference(
        address_id=_ADDRESS_ID,
        department=_DEPARTMENT,
        ban_fingerprint=decoded.ban_fingerprint,
        addresses_generation=_ADDRESSES_GENERATION,
        diagnostics_generation=_DIAGNOSTICS_GENERATION,
    )
    assert len(decoded.ban_fingerprint) == 64
    decoded.assert_current(
        address_id=_ADDRESS_ID,
        department=_DEPARTMENT,
        ban_id=_BAN_ID,
        addresses_generation=_ADDRESSES_GENERATION,
        diagnostics_generation=_DIAGNOSTICS_GENERATION,
    )


def test_document_and_cursor_round_trip_are_tied_to_the_context():
    context_token = _issue_context()
    context = verify_context(context_token)

    document = verify_document(
        issue_document(
            context_token,
            document_id=_DOCUMENT_ID,
            dpe_number=_DPE_NUMBER,
        ),
        context=context,
    )
    cursor = verify_cursor(
        issue_cursor(
            context,
            anchor_id=_DOCUMENT_ID,
            dpe_number=_DPE_NUMBER,
        ),
        context=context,
    )

    assert isinstance(document, DpeDocumentReference)
    assert document.document_id == _DOCUMENT_ID
    document.assert_current(document_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)
    assert isinstance(cursor, DpeCursorReference)
    assert cursor.anchor_id == _DOCUMENT_ID
    cursor.assert_current(anchor_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)


@pytest.mark.parametrize("kind", ["context", "document", "cursor"])
def test_tampered_references_are_rejected_with_a_constant_error(kind: str):
    context_token = _issue_context()
    context = verify_context(context_token)
    references = {
        "context": context_token,
        "document": issue_document(
            context, document_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER
        ),
        "cursor": issue_cursor(context, anchor_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER),
    }

    with pytest.raises(InvalidReferenceError) as caught:
        if kind == "context":
            verify_context(_tamper(references[kind]))
        elif kind == "document":
            verify_document(_tamper(references[kind]), context=context)
        else:
            verify_cursor(_tamper(references[kind]), context=context)

    assert caught.value.code == "invalid_reference"
    assert str(caught.value) == "invalid_reference"
    assert references[kind] not in str(caught.value)


def test_each_reference_finality_has_an_independent_signature_domain():
    context_token = _issue_context()
    context = verify_context(context_token)
    document_token = issue_document(
        context, document_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER
    )
    cursor_token = issue_cursor(context, anchor_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)

    with pytest.raises(InvalidReferenceError):
        verify_context(document_token)
    with pytest.raises(InvalidReferenceError):
        verify_document(context_token, context=context)
    with pytest.raises(InvalidReferenceError):
        verify_cursor(document_token, context=context)
    with pytest.raises(InvalidReferenceError):
        verify_document(cursor_token, context=context)


def test_expired_reference_requires_a_refresh(monkeypatch: pytest.MonkeyPatch):
    issued_at = 1_800_000_000
    monkeypatch.setattr(dangerous_timed.time, "time", lambda: issued_at)
    token = _issue_context()
    monkeypatch.setattr(
        dangerous_timed.time,
        "time",
        lambda: issued_at + DPE_REFERENCE_MAX_AGE_SECONDS + 1,
    )

    with pytest.raises(RefreshRequiredError) as caught:
        verify_context(token)

    assert caught.value.code == "refresh_required"
    assert str(caught.value) == "refresh_required"
    assert token not in str(caught.value)


@pytest.mark.parametrize("kind", ["document", "cursor"])
def test_reference_from_another_target_is_invalid(kind: str):
    source = verify_context(_issue_context())
    other = verify_context(_issue_context(address_id=_ADDRESS_ID + 1))
    token = (
        issue_document(source, document_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)
        if kind == "document"
        else issue_cursor(source, anchor_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)
    )

    with pytest.raises(InvalidReferenceError):
        if kind == "document":
            verify_document(token, context=other)
        else:
            verify_cursor(token, context=other)


def test_reused_document_id_with_a_different_fingerprint_requires_refresh():
    context = verify_context(_issue_context())
    document = verify_document(
        issue_document(context, document_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER),
        context=context,
    )

    with pytest.raises(RefreshRequiredError):
        document.assert_current(
            document_id=_DOCUMENT_ID,
            dpe_number="DPE-REUSED-ID-DIFFERENT-SOURCE",
        )


def test_changed_target_fingerprint_or_generation_requires_refresh():
    context = verify_context(_issue_context())

    with pytest.raises(RefreshRequiredError):
        context.assert_current(
            address_id=_ADDRESS_ID,
            department=_DEPARTMENT,
            ban_id="44109_REUSED_ADDRESS_ID",
            addresses_generation=_ADDRESSES_GENERATION,
            diagnostics_generation=_DIAGNOSTICS_GENERATION,
        )
    with pytest.raises(RefreshRequiredError):
        context.assert_current(
            address_id=_ADDRESS_ID,
            department=_DEPARTMENT,
            ban_id=_BAN_ID,
            addresses_generation=_ADDRESSES_GENERATION + 1,
            diagnostics_generation=_DIAGNOSTICS_GENERATION,
        )


def test_readable_payloads_never_contain_source_identifiers_or_address():
    private_value = "PRIVATE-ADDRESS-AND-SOURCE-ID"
    context_token = issue_context(
        address_id=_ADDRESS_ID,
        department=_DEPARTMENT,
        ban_id=private_value,
        addresses_generation=_ADDRESSES_GENERATION,
        diagnostics_generation=_DIAGNOSTICS_GENERATION,
    )
    context = verify_context(context_token)
    tokens = (
        context_token,
        issue_document(context, document_id=_DOCUMENT_ID, dpe_number=private_value),
        issue_cursor(context, anchor_id=_DOCUMENT_ID, dpe_number=private_value),
    )

    for token in tokens:
        decoded = json.dumps(_decode_unsigned_payload(token), sort_keys=True)
        assert private_value not in decoded
        assert _BAN_ID not in decoded
        assert _DPE_NUMBER not in decoded
        assert "rue privée" not in decoded


def test_ban_document_and_cursor_fingerprints_use_distinct_hmac_domains():
    same_private_value = "SAME-PRIVATE-SOURCE-VALUE"
    context_token = issue_context(
        address_id=_ADDRESS_ID,
        department=_DEPARTMENT,
        ban_id=same_private_value,
        addresses_generation=_ADDRESSES_GENERATION,
        diagnostics_generation=_DIAGNOSTICS_GENERATION,
    )
    context = verify_context(context_token)
    document_token = issue_document(
        context,
        document_id=_DOCUMENT_ID,
        dpe_number=same_private_value,
    )
    cursor_token = issue_cursor(
        context,
        anchor_id=_DOCUMENT_ID,
        dpe_number=same_private_value,
    )

    fingerprints = {
        _decode_unsigned_payload(context_token)["ah"],
        _decode_unsigned_payload(document_token)["rh"],
        _decode_unsigned_payload(cursor_token)["rh"],
    }
    assert len(fingerprints) == 3


@pytest.fixture
def fixed_day(monkeypatch):
    today = date(2026, 9, 7)
    monkeypatch.setattr(refs, "paris_today", lambda: today)
    return today


@pytest.mark.parametrize("kind", ["context", "document", "cursor"])
def test_signed_date_is_common_and_midnight_invalidates_all_paths(
    kind, fixed_day, monkeypatch
):
    token = _issue_context(as_of_date=fixed_day)
    context = verify_context(token, as_of_date=fixed_day)
    if kind == "document":
        token = issue_document(
            context,
            document_id=_DOCUMENT_ID,
            dpe_number=_DPE_NUMBER,
            as_of_date=fixed_day,
        )
        decoded = verify_document(token, context=context, as_of_date=fixed_day)
    elif kind == "cursor":
        token = issue_cursor(
            context,
            anchor_id=_DOCUMENT_ID,
            dpe_number=_DPE_NUMBER,
            as_of_date=fixed_day,
        )
        decoded = verify_cursor(token, context=context, as_of_date=fixed_day)
    else:
        decoded = context
    assert _decode_unsigned_payload(token)["v"] == 2
    assert _decode_unsigned_payload(token)["ad"] == "2026-09-07"
    assert decoded.as_of_date == fixed_day
    monkeypatch.setattr(refs, "paris_today", lambda: date(2026, 9, 8))
    with pytest.raises(RefreshRequiredError):
        if kind == "context":
            verify_context(token, as_of_date=fixed_day)
        else:
            getattr(refs, f"verify_{kind}")(
                token, context=context, as_of_date=fixed_day
            )
    with pytest.raises(RefreshRequiredError):
        if kind == "context":
            decoded.assert_current(
                address_id=_ADDRESS_ID,
                department=_DEPARTMENT,
                ban_id=_BAN_ID,
                addresses_generation=_ADDRESSES_GENERATION,
                diagnostics_generation=_DIAGNOSTICS_GENERATION,
                as_of_date=fixed_day,
            )
        else:
            decoded.assert_current(
                **{
                    f"{'document' if kind == 'document' else 'anchor'}_id": _DOCUMENT_ID
                },
                dpe_number=_DPE_NUMBER,
                as_of_date=fixed_day,
            )
    with pytest.raises(RefreshRequiredError):
        issue_document(context, document_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)
    with pytest.raises(RefreshRequiredError):
        issue_cursor(context, anchor_id=_DOCUMENT_ID, dpe_number=_DPE_NUMBER)
    with pytest.raises(RefreshRequiredError):
        _issue_context(as_of_date=fixed_day)


@pytest.mark.parametrize("kind", ["context", "document", "cursor"])
@pytest.mark.parametrize(
    "mutation,error",
    [
        ({"ad": "2026-09-06"}, RefreshRequiredError),
        ({"ad": "2026-09-08"}, RefreshRequiredError),
        ({"ad": "20260907"}, InvalidReferenceError),
        ({"ad": "2026-09-07T00:00:00Z"}, InvalidReferenceError),
        ({"ad": 1788739200}, InvalidReferenceError),
        ({"ad": None}, InvalidReferenceError),
        ({"v": True}, InvalidReferenceError),
        ({"v": 2.0}, InvalidReferenceError),
        ({"private": "SECRET"}, InvalidReferenceError),
    ],
)
def test_dated_payload_rejects_wrong_date_or_shape(kind, mutation, error, fixed_day):
    context = verify_context(_issue_context())
    payload = _decode_unsigned_payload(_issue_context())
    payload["p"] = kind
    if kind != "context":
        payload.update(r=_DOCUMENT_ID, rh="a" * 64)
    payload.update(mutation)
    token = getattr(refs, f"_{kind.upper()}_SERIALIZER").dumps(payload)
    with pytest.raises(error):
        getattr(refs, f"verify_{kind}")(
            token, **({} if kind == "context" else {"context": context})
        )


@pytest.mark.parametrize("kind", ["context", "document", "cursor"])
@pytest.mark.parametrize(
    "mutation",
    [
        {},
        {"v": True},
        {"a": True},
        {"p": "wrong"},
        {"secret": "PRIVATE"},
        {"rh": "invalid"},
    ],
)
def test_only_authentic_well_formed_v1_tokens_require_refresh(
    kind, mutation, fixed_day
):
    context = verify_context(_issue_context())
    payload = _decode_unsigned_payload(_issue_context())
    payload.pop("ad")
    payload.update(v=1, p=kind)
    if kind != "context":
        payload.update(r=_DOCUMENT_ID, rh="a" * 64)
    payload.update(mutation)
    serializer = getattr(refs, f"_{kind.upper()}_SERIALIZER")
    token = serializer.dumps(payload)
    kwargs = {} if kind == "context" else {"context": context}
    with pytest.raises(InvalidReferenceError if mutation else RefreshRequiredError):
        getattr(refs, f"verify_{kind}")(token, **kwargs)
    with pytest.raises(InvalidReferenceError):
        getattr(refs, f"verify_{kind}")(_tamper(token), **kwargs)


@pytest.mark.parametrize(
    "hour,expected", [(21, date(2026, 9, 7)), (22, date(2026, 9, 8))]
)
def test_clock_uses_paris_calendar_not_utc(monkeypatch, hour, expected):
    class Clock:
        @staticmethod
        def now(tz):
            assert str(tz) == "Europe/Paris"
            return datetime(2026, 9, 7, hour, 59, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(refs, "datetime", Clock)
    assert refs.paris_today() == expected


@pytest.mark.parametrize("kind", ["context", "document", "cursor"])
@pytest.mark.parametrize("version", [1, 2])
@pytest.mark.parametrize("malformed", [False, True])
def test_expiry_does_not_hide_an_invalid_authenticated_payload(
    kind, version, malformed, fixed_day, monkeypatch
):
    timestamp = 1_800_000_000
    monkeypatch.setattr(dangerous_timed.time, "time", lambda: timestamp)
    context = verify_context(_issue_context())
    payload = _decode_unsigned_payload(_issue_context())
    payload.update(v=version, p=kind)
    if version == 1:
        payload.pop("ad")
    if kind != "context":
        payload.update(r=_DOCUMENT_ID, rh="a" * 64)
    if malformed:
        payload["a"] = True
    token = getattr(refs, f"_{kind.upper()}_SERIALIZER").dumps(payload)
    monkeypatch.setattr(dangerous_timed.time, "time", lambda: timestamp + 1801)
    with pytest.raises(InvalidReferenceError if malformed else RefreshRequiredError):
        getattr(refs, f"verify_{kind}")(
            token, **({} if kind == "context" else {"context": context})
        )
