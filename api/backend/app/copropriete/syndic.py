"""Who manages a co-ownership, as the registry (RNIC) says it.

A volunteer or cooperative manager is a co-owner, a natural person: its name
is personal data and is never served. Only a professional manager (a
company, or a professional working in its own name, whose activity is
public) is named, by every route that serves the registry: the address
report (04.7-08) and ``GET /v1/copropriete``.
"""

import unicodedata

# Types of manager of the registry, read without accents nor case.
TYPES_SYNDIC = {
    "professionnel": "professionnel",
    "benevole": "benevole",
    "cooperatif": "cooperatif",
}


def type_syndic(valeur: object) -> str:
    """« professionnel », « benevole », « cooperatif » or « non_connu »."""
    texte = unicodedata.normalize("NFKD", str(valeur or "")).encode("ascii", "ignore")
    return TYPES_SYNDIC.get(texte.decode().strip().lower(), "non_connu")


def nom_syndic_public(type_: object, nom: object) -> str | None:
    """The name of a professional manager; None for any other type."""
    if type_syndic(type_) != "professionnel" or not isinstance(nom, str):
        return None
    return nom.strip() or None
