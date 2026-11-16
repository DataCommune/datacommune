"""Require a complete, unique address before resolving property datasets."""

import re
from collections.abc import Sequence

from app.ban.queries import _extract_numero, normalize_address_text
from app.common.exceptions import ErreurApiError
from app.common.geo import PLM_CITY_ALIASES, normalize_code_insee

ADDRESS_CANDIDATE_LIMIT = 20
_POSTAL = re.compile(r"\b\d{5}\b")


def _selection_required() -> ErreurApiError:
    """Keep the public problem stable and independent of private input text."""
    return ErreurApiError(
        422,
        "Adresse à préciser",
        "Précisez une adresse complète ou sélectionnez une proposition de la "
        "recherche. Une commune ou une rue seule ne permet pas d'identifier le bien.",
    )


def _commune_labels(row: dict) -> set[str]:
    """Accept established PLM names, not an arbitrary prefix of a commune."""
    commune = normalize_address_text(row.get("nom_commune") or "")
    labels = {commune} if commune else set()
    code = normalize_code_insee(row.get("code_commune") or "")
    for alias, commune_code in PLM_CITY_ALIASES.items():
        if code == commune_code:
            labels.add(alias)
            labels.add(commune.removesuffix(" arrondissement"))
    return labels


def _matches_request(query: str, row: dict) -> bool:
    """Compare number, literal repetition, street and every locality qualifier."""
    raw_number = str(row["numero"]).strip() if row.get("numero") is not None else ""
    if raw_number and not raw_number.isdecimal():
        return False
    postals = _POSTAL.findall(query)
    if postals and (len(postals) != 1 or postals[0] != row.get("code_postal")):
        return False
    requested = " ".join(_POSTAL.sub(" ", query).split())
    voie = normalize_address_text(row.get("nom_voie") or "")
    if not voie or not row.get("ban_id"):
        return False
    rep = row.get("rep") or ""
    if rep and not raw_number:
        return False
    streets = {
        normalize_address_text(f"{raw_number}{rep} {voie}"),
        normalize_address_text(f"{raw_number} {rep} {voie}"),
    }
    complete_labels = {
        f"{street} {city}" for street in streets for city in _commune_labels(row)
    }
    if postals:
        complete_labels.update(streets)
    return requested in complete_labels


def select_address_candidate(query: str, rows: Sequence[dict]) -> dict | None:
    """Return one established address, otherwise an absence or actionable 422.

    A bounded suggestion list cannot prove uniqueness when it reaches its
    sentinel. Unnumbered places require an unnumbered source row, a full place
    name and a locality qualifier, exactly like numbered addresses.
    """
    normalized = normalize_address_text(query)
    without_postal = " ".join(_POSTAL.sub(" ", normalized).split())
    if not rows and _extract_numero(without_postal)[1] is not None:
        return None
    if len(rows) > ADDRESS_CANDIDATE_LIMIT:
        raise _selection_required()
    matches: list[dict] = []
    for row in rows:
        if _matches_request(normalized, row) and row not in matches:
            matches.append(row)
    if len(matches) != 1:
        raise _selection_required()
    return matches[0]
