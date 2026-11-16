"""Short-lived opaque references for the address DPE inventory.

The signed payload is readable by its holder. It therefore carries only
technical row identifiers, relation generations and keyed fingerprints. Raw
BAN keys, ADEME document numbers and addresses never enter a reference.

References expire after 30 minutes and are deliberately not permanent links.
An import, restore, relation swap or application-secret rotation can require a
fresh inventory even before that limit. A valid signature alone never
authorizes a database read: callers must validate the returned target and
fingerprints against rows read under the generation locks.
"""

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import ClassVar, NoReturn, cast
from zoneinfo import ZoneInfo

from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer

from app.config import settings

DPE_REFERENCE_MAX_AGE_SECONDS = 1800

_REFERENCE_VERSION = 2
_REFERENCE_SECRET = settings.jwt_secret_key

_CONTEXT_PURPOSE = "context"
_DOCUMENT_PURPOSE = "document"
_CURSOR_PURPOSE = "cursor"

_CONTEXT_SALT = "dpe-reference-context-v1"
_DOCUMENT_SALT = "dpe-reference-document-v1"
_CURSOR_SALT = "dpe-reference-cursor-v1"

_BAN_FINGERPRINT_DOMAIN = b"dpe-reference-ban-v1"
_DOCUMENT_FINGERPRINT_DOMAIN = b"dpe-reference-document-v1"
_CURSOR_FINGERPRINT_DOMAIN = b"dpe-reference-cursor-v1"

_COMMON_PAYLOAD_KEYS = frozenset({"v", "p", "a", "d", "ah", "ag", "dg", "ad"})
_ITEM_PAYLOAD_KEYS = _COMMON_PAYLOAD_KEYS | {"r", "rh"}
_HEX_DIGITS = frozenset("0123456789abcdef")


def _serializer(salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        _REFERENCE_SECRET,
        salt=salt,
        signer_kwargs={"digest_method": hashlib.sha256},
    )


_CONTEXT_SERIALIZER = _serializer(_CONTEXT_SALT)
_DOCUMENT_SERIALIZER = _serializer(_DOCUMENT_SALT)
_CURSOR_SERIALIZER = _serializer(_CURSOR_SALT)


class DpeReferenceError(ValueError):
    """Base error carrying only a stable, non-reflective public code."""

    code: ClassVar[str]

    def __init__(self) -> None:
        super().__init__(self.code)


class InvalidReferenceError(DpeReferenceError):
    """The signature, shape, purpose or requested target is invalid."""

    code = "invalid_reference"


class RefreshRequiredError(DpeReferenceError):
    """The reference expired or no longer matches the current data snapshot."""

    code = "refresh_required"


def _raise_invalid() -> NoReturn:
    raise InvalidReferenceError


def _raise_refresh() -> NoReturn:
    raise RefreshRequiredError


def paris_today() -> date:
    """Return the server's business date through one injectable clock."""
    return datetime.now(ZoneInfo("Europe/Paris")).date()


def _reference_date(value: object) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str):
        _raise_invalid()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise InvalidReferenceError from None
    if parsed.isoformat() != value:
        _raise_invalid()
    return parsed


def _assert_date(value: date, as_of_date: date | None = None) -> None:
    if type(value) is not date or (
        as_of_date is not None and type(as_of_date) is not date
    ):
        _raise_invalid()
    if value != paris_today() or (as_of_date is not None and value != as_of_date):
        _raise_refresh()


def _fingerprint(domain: bytes, source_value: str) -> str:
    if not isinstance(source_value, str) or not source_value:
        _raise_invalid()
    domain_key = hmac.new(
        _REFERENCE_SECRET.encode("utf-8"), domain, hashlib.sha256
    ).digest()
    return hmac.new(
        domain_key,
        source_value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _current_fingerprint(domain: bytes, source_value: str) -> str:
    try:
        return _fingerprint(domain, source_value)
    except InvalidReferenceError:
        _raise_refresh()


def _positive_int(value: object) -> int:
    if type(value) is not int or value <= 0:
        _raise_invalid()
    return cast(int, value)


def _department(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) not in (2, 3)
        or not value.isascii()
        or not value.isalnum()
        or value != value.upper()
    ):
        _raise_invalid()
    return value


def _stored_fingerprint(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != hashlib.sha256().digest_size * 2
        or any(character not in _HEX_DIGITS for character in value)
    ):
        _raise_invalid()
    return value


@dataclass(frozen=True, slots=True)
class _DpeTargetReference:
    address_id: int
    department: str
    ban_fingerprint: str
    addresses_generation: int
    diagnostics_generation: int
    as_of_date: date = field(default_factory=lambda: paris_today(), kw_only=True)


@dataclass(frozen=True, slots=True)
class DpeContextReference(_DpeTargetReference):
    """Decoded inventory target; still requires a current-row assertion."""

    def assert_current(
        self,
        *,
        address_id: int,
        department: str,
        ban_id: str,
        addresses_generation: int,
        diagnostics_generation: int,
        as_of_date: date | None = None,
    ) -> None:
        """Require the current locked target to match this snapshot."""
        _assert_date(self.as_of_date, as_of_date)
        expected_fingerprint = _current_fingerprint(_BAN_FINGERPRINT_DOMAIN, ban_id)
        fingerprint_matches = hmac.compare_digest(
            self.ban_fingerprint, expected_fingerprint
        )
        target_matches = (
            type(address_id) is int
            and isinstance(department, str)
            and type(addresses_generation) is int
            and type(diagnostics_generation) is int
            and self.address_id == address_id
            and self.department == department
            and self.addresses_generation == addresses_generation
            and self.diagnostics_generation == diagnostics_generation
        )
        if not target_matches or not fingerprint_matches:
            _raise_refresh()


@dataclass(frozen=True, slots=True)
class DpeDocumentReference(_DpeTargetReference):
    """Decoded document target tied to one inventory context."""

    document_id: int
    document_fingerprint: str

    def assert_current(
        self, *, document_id: int, dpe_number: str, as_of_date: date | None = None
    ) -> None:
        """Refuse a row ID reused for another ADEME source document."""
        _assert_date(self.as_of_date, as_of_date)
        expected_fingerprint = _current_fingerprint(
            _DOCUMENT_FINGERPRINT_DOMAIN, dpe_number
        )
        fingerprint_matches = hmac.compare_digest(
            self.document_fingerprint, expected_fingerprint
        )
        if (
            type(document_id) is not int
            or self.document_id != document_id
            or not fingerprint_matches
        ):
            _raise_refresh()


@dataclass(frozen=True, slots=True)
class DpeCursorReference(_DpeTargetReference):
    """Decoded keyset anchor tied to one inventory context."""

    anchor_id: int
    anchor_fingerprint: str

    def assert_current(
        self, *, anchor_id: int, dpe_number: str, as_of_date: date | None = None
    ) -> None:
        """Refuse an anchor ID reused for another ADEME source document."""
        _assert_date(self.as_of_date, as_of_date)
        expected_fingerprint = _current_fingerprint(
            _CURSOR_FINGERPRINT_DOMAIN, dpe_number
        )
        fingerprint_matches = hmac.compare_digest(
            self.anchor_fingerprint, expected_fingerprint
        )
        if (
            type(anchor_id) is not int
            or self.anchor_id != anchor_id
            or not fingerprint_matches
        ):
            _raise_refresh()


def _common_payload(context: DpeContextReference, purpose: str) -> dict[str, int | str]:
    return {
        "v": _REFERENCE_VERSION,
        "p": purpose,
        "a": context.address_id,
        "d": context.department,
        "ah": context.ban_fingerprint,
        "ag": context.addresses_generation,
        "dg": context.diagnostics_generation,
        "ad": context.as_of_date.isoformat(),
    }


def _context_from_payload(
    payload: object,
    *,
    purpose: str,
    expected_keys: frozenset[str],
    as_of_date: date | None = None,
) -> DpeContextReference:
    if not isinstance(payload, dict) or type(payload.get("v")) is not int:
        _raise_invalid()
    version = payload["v"]
    keys = expected_keys - {"ad"} if version == 1 else expected_keys
    if (
        set(payload) != keys
        or version not in (1, _REFERENCE_VERSION)
        or payload.get("p") != purpose
    ):
        _raise_invalid()
    # Validate the entire authenticated legacy shape before offering refresh.
    if "r" in expected_keys:
        _positive_int(payload.get("r"))
        _stored_fingerprint(payload.get("rh"))
    context = DpeContextReference(
        address_id=_positive_int(payload.get("a")),
        department=_department(payload.get("d")),
        ban_fingerprint=_stored_fingerprint(payload.get("ah")),
        addresses_generation=_positive_int(payload.get("ag")),
        diagnostics_generation=_positive_int(payload.get("dg")),
        as_of_date=(
            paris_today() if version == 1 else _reference_date(payload.get("ad"))
        ),
    )
    if version == 1:
        _raise_refresh()
    _assert_date(context.as_of_date, as_of_date)
    return context


def _loads(
    reference: str, serializer: URLSafeTimedSerializer, purpose: str
) -> dict[str, object]:
    if not isinstance(reference, str) or not reference:
        _raise_invalid()
    expired = False
    try:
        payload = serializer.loads(
            reference,
            max_age=DPE_REFERENCE_MAX_AGE_SECONDS,
        )
    except SignatureExpired:
        # TTL never turns a malformed authenticated object into a refreshable
        # reference. Decode again with signature verification but without age.
        expired = True
        try:
            payload = serializer.loads(reference)
        except (BadData, TypeError, ValueError, OverflowError):
            raise InvalidReferenceError from None
    except (BadData, TypeError, ValueError, OverflowError):
        raise InvalidReferenceError from None
    if not isinstance(payload, dict):
        _raise_invalid()
    if expired:
        _context_from_payload(
            payload,
            purpose=purpose,
            expected_keys=(
                _COMMON_PAYLOAD_KEYS
                if purpose == _CONTEXT_PURPOSE
                else _ITEM_PAYLOAD_KEYS
            ),
        )
        _raise_refresh()
    return payload


def _coerce_context(
    context: str | DpeContextReference,
    *,
    as_of_date: date | None = None,
) -> DpeContextReference:
    if isinstance(context, str):
        return verify_context(context, as_of_date=as_of_date)
    if not isinstance(context, DpeContextReference):
        _raise_invalid()
    _assert_date(context.as_of_date, as_of_date)
    return context


def _assert_same_context(
    reference: _DpeTargetReference,
    context: DpeContextReference,
) -> None:
    fingerprint_matches = hmac.compare_digest(
        reference.ban_fingerprint, context.ban_fingerprint
    )
    target_matches = (
        reference.address_id == context.address_id
        and reference.department == context.department
        and reference.addresses_generation == context.addresses_generation
        and reference.diagnostics_generation == context.diagnostics_generation
        and reference.as_of_date == context.as_of_date
    )
    if not target_matches or not fingerprint_matches:
        _raise_invalid()


def issue_context(
    *,
    address_id: int,
    department: str,
    ban_id: str,
    addresses_generation: int,
    diagnostics_generation: int,
    as_of_date: date | None = None,
) -> str:
    """Issue a 30-minute context without embedding its source BAN key."""
    current_date = paris_today() if as_of_date is None else as_of_date
    _assert_date(current_date)
    context = DpeContextReference(
        address_id=_positive_int(address_id),
        department=_department(department),
        ban_fingerprint=_fingerprint(_BAN_FINGERPRINT_DOMAIN, ban_id),
        addresses_generation=_positive_int(addresses_generation),
        diagnostics_generation=_positive_int(diagnostics_generation),
        as_of_date=current_date,
    )
    return _CONTEXT_SERIALIZER.dumps(_common_payload(context, _CONTEXT_PURPOSE))


def verify_context(
    reference: str, *, as_of_date: date | None = None
) -> DpeContextReference:
    """Verify and decode a context, or raise a stable typed error."""
    return _context_from_payload(
        _loads(reference, _CONTEXT_SERIALIZER, _CONTEXT_PURPOSE),
        purpose=_CONTEXT_PURPOSE,
        expected_keys=_COMMON_PAYLOAD_KEYS,
        as_of_date=as_of_date,
    )


def issue_document(
    context: str | DpeContextReference,
    *,
    document_id: int,
    dpe_number: str,
    as_of_date: date | None = None,
) -> str:
    """Issue a document reference bound to an inventory target."""
    decoded_context = _coerce_context(context, as_of_date=as_of_date)
    payload = _common_payload(decoded_context, _DOCUMENT_PURPOSE)
    payload.update(
        {
            "r": _positive_int(document_id),
            "rh": _fingerprint(_DOCUMENT_FINGERPRINT_DOMAIN, dpe_number),
        }
    )
    return _DOCUMENT_SERIALIZER.dumps(payload)


def verify_document(
    reference: str,
    *,
    context: DpeContextReference,
    as_of_date: date | None = None,
) -> DpeDocumentReference:
    """Verify a document and require it to belong to ``context``."""
    payload = _loads(reference, _DOCUMENT_SERIALIZER, _DOCUMENT_PURPOSE)
    decoded_context = _context_from_payload(
        payload,
        purpose=_DOCUMENT_PURPOSE,
        expected_keys=_ITEM_PAYLOAD_KEYS,
        as_of_date=as_of_date,
    )
    context = _coerce_context(context, as_of_date=as_of_date)
    _assert_same_context(decoded_context, context)
    return DpeDocumentReference(
        address_id=decoded_context.address_id,
        department=decoded_context.department,
        ban_fingerprint=decoded_context.ban_fingerprint,
        addresses_generation=decoded_context.addresses_generation,
        diagnostics_generation=decoded_context.diagnostics_generation,
        as_of_date=decoded_context.as_of_date,
        document_id=_positive_int(payload.get("r")),
        document_fingerprint=_stored_fingerprint(payload.get("rh")),
    )


def issue_cursor(
    context: str | DpeContextReference,
    *,
    anchor_id: int,
    dpe_number: str,
    as_of_date: date | None = None,
) -> str:
    """Issue a keyset cursor without embedding the ADEME sort value."""
    decoded_context = _coerce_context(context, as_of_date=as_of_date)
    payload = _common_payload(decoded_context, _CURSOR_PURPOSE)
    payload.update(
        {
            "r": _positive_int(anchor_id),
            "rh": _fingerprint(_CURSOR_FINGERPRINT_DOMAIN, dpe_number),
        }
    )
    return _CURSOR_SERIALIZER.dumps(payload)


def verify_cursor(
    reference: str,
    *,
    context: DpeContextReference,
    as_of_date: date | None = None,
) -> DpeCursorReference:
    """Verify a cursor and require it to belong to ``context``."""
    payload = _loads(reference, _CURSOR_SERIALIZER, _CURSOR_PURPOSE)
    decoded_context = _context_from_payload(
        payload,
        purpose=_CURSOR_PURPOSE,
        expected_keys=_ITEM_PAYLOAD_KEYS,
        as_of_date=as_of_date,
    )
    context = _coerce_context(context, as_of_date=as_of_date)
    _assert_same_context(decoded_context, context)
    return DpeCursorReference(
        address_id=decoded_context.address_id,
        department=decoded_context.department,
        ban_fingerprint=decoded_context.ban_fingerprint,
        addresses_generation=decoded_context.addresses_generation,
        diagnostics_generation=decoded_context.diagnostics_generation,
        as_of_date=decoded_context.as_of_date,
        anchor_id=_positive_int(payload.get("r")),
        anchor_fingerprint=_stored_fingerprint(payload.get("rh")),
    )


__all__ = [
    "DPE_REFERENCE_MAX_AGE_SECONDS",
    "DpeContextReference",
    "DpeCursorReference",
    "DpeDocumentReference",
    "DpeReferenceError",
    "InvalidReferenceError",
    "RefreshRequiredError",
    "issue_context",
    "issue_cursor",
    "issue_document",
    "paris_today",
    "verify_context",
    "verify_cursor",
    "verify_document",
]
