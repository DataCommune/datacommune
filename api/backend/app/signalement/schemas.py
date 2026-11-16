"""Shapes of an error report sent from the site.

Every field is plain text with its own bound: nothing here is ever rendered as
HTML or Markdown. The address of the visitor is not a field, on purpose: a
report is stored without it.
"""

import re
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
)

COMMENTAIRE_MIN = 10
COMMENTAIRE_MAX = 2000
EMAIL_MAX = 254
PAGE_MAX = 300
TITRE_MAX = 200
LIGNES_MAX = 600
SOURCE_MAX = 200
VERSION_MAX = 64
USER_AGENT_MAX = 400
LARGEUR_MAX = 20_000

# Everything below the space but tab and line feed, and DEL: PostgreSQL refuses
# NUL in text, and the others have no place in a message read by a person.
_CONTROLES = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
# A path of the site: one leading slash, never the `//host` of another origin.
_CHEMIN = re.compile(r"^/(?!/)[^\s\\]*$")
_VERSION = re.compile(r"^[0-9A-Za-z._-]+$")


def _texte(valeur: object) -> object:
    """Line ends unified, control characters removed, edges trimmed."""
    if not isinstance(valeur, str):
        return valeur
    return _CONTROLES.sub("", valeur.replace("\r\n", "\n")).strip()


def _texte_ou_absent(valeur: object) -> object:
    """An optional field left empty is absent, not an empty string."""
    nettoye = _texte(valeur)
    return None if nettoye == "" else nettoye


def _chemin_seul(valeur: object) -> object:
    """The path of the page, without its query string and fragment."""
    nettoye = _texte_ou_absent(valeur)
    if not isinstance(nettoye, str):
        return nettoye
    return re.split(r"[?#]", nettoye, maxsplit=1)[0]


def _chemin_du_site(valeur: str | None) -> str | None:
    if valeur is not None and not _CHEMIN.fullmatch(valeur):
        raise ValueError("not a path of the site")
    return valeur


def _version(valeur: str | None) -> str | None:
    if valeur is not None and not _VERSION.fullmatch(valeur):
        raise ValueError("not a version")
    return valeur


Texte = Annotated[str, BeforeValidator(_texte)]
TexteFacultatif = Annotated[str | None, BeforeValidator(_texte_ou_absent)]


class SignalementDepot(BaseModel):
    """What the form of the site sends: the message and its context."""

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    commentaire: Texte = Field(min_length=COMMENTAIRE_MIN, max_length=COMMENTAIRE_MAX)
    email: Annotated[
        EmailStr | None,
        BeforeValidator(_texte_ou_absent),
        AfterValidator(lambda v: v.lower() if v else v),
    ] = Field(default=None, max_length=EMAIL_MAX)
    page: Annotated[
        str | None, BeforeValidator(_chemin_seul), AfterValidator(_chemin_du_site)
    ] = Field(default=None, max_length=PAGE_MAX)
    titre: TexteFacultatif = Field(default=None, max_length=TITRE_MAX)
    lignes: TexteFacultatif = Field(default=None, max_length=LIGNES_MAX)
    source: TexteFacultatif = Field(default=None, max_length=SOURCE_MAX)
    version_site: Annotated[
        str | None, BeforeValidator(_texte_ou_absent), AfterValidator(_version)
    ] = Field(default=None, max_length=VERSION_MAX)
    largeur: int | None = Field(default=None, ge=0, le=LARGEUR_MAX)
    user_agent: TexteFacultatif = Field(default=None, max_length=USER_AGENT_MAX)
    # Honeypot: a field no person sees. Filled, the report is dropped.
    piege: str | None = Field(default=None, max_length=TITRE_MAX)


class SignalementRecu(BaseModel):
    """Answer of a report: a random reference, nothing that can be enumerated."""

    reference: str
