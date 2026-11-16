"""« Construction » : year of construction of the building groups of the parcel (BDNB).

Granularity: building. The groups are read in ``bdnb_batiment_parcelle``, the
import of the BDNB open export (plan 04.7-19), by the parcel that contains
the BAN point (``unique`` relation only, N-02), never by the BAN key alone:
every relation of the parcel, principal or not. The API is never called, so
two millesimes never mix on one report.

One building group on the parcel: its year, ``probable`` (the group stands on
the parcel of the point). Several groups: the year is served only when they
all share it, however many they are; otherwise it is a gap with the exact
number of candidates. The detailed list stays bounded to ten groups
(``liste_incomplete`` beyond).

Tone rule: vigilance when the year is known and before 1949 (a lead exposure
report is required for a sale or a lease of a dwelling built before 1949),
else neutre.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adresse.blocs import lire_bloc, periode_de_source
from app.datasets.provenance import ProvenanceFacts
from app.datasets.queries import get_provenance_facts
from app.rapport.schemas import Chiffre, Rattachement, Theme, Ton
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    parcelle_unique,
    sans_reponse,
    sources_de,
    theme,
)

ENTITE = "batiment_groupe_bdnb_de_la_parcelle"
ANNEE_PLOMB = 1949
DATASET = "bdnb_batiment_parcelle"
LISTE_MAX = 10

# One pass on idx_bdnb_bp_parcelle_groupe: the total, the distinct years (NULL
# included) and the first ten groups, principal relations first.
_GROUPES_DE_LA_PARCELLE = text(f"""
    WITH groupes AS MATERIALIZED (
        SELECT batiment_groupe_id, parcelle_principale, annee_construction,
               mat_mur_txt, mat_toit_txt
        FROM bdnb_batiment_parcelle
        WHERE parcelle_id = :parcelle
    ),
    resume AS (
        SELECT count(*) AS total,
               array_agg(DISTINCT annee_construction) AS annees
        FROM groupes
    )
    SELECT r.total, r.annees, g.batiment_groupe_id, g.annee_construction,
           g.mat_mur_txt, g.mat_toit_txt
    FROM resume r
    LEFT JOIN LATERAL (
        SELECT * FROM groupes
        ORDER BY parcelle_principale DESC, batiment_groupe_id
        LIMIT {LISTE_MAX}
    ) g ON TRUE
    ORDER BY g.parcelle_principale DESC, g.batiment_groupe_id
""")


@dataclass(frozen=True)
class BdnbLu:
    """BDNB building groups of the parcel read in the import.

    ``lignes`` holds at most ten groups; ``total`` counts them all and
    ``annees`` is the set of their years, ``None`` for a group without one.
    """

    lignes: list[dict]
    total: int
    annees: frozenset[int | None]
    faits: ProvenanceFacts | None


async def lire_import(session: AsyncSession, id_parcelle: str) -> BdnbLu:
    """The groups of *id_parcelle* and the provenance facts of the import.

    Unlike the other themes of the report, the metadata of the import is not
    read through ``faits_de_provenance``, whose own savepoint turns a SQL error
    into an unknown period: here the metadata decides whether the value exists
    (is the departement imported?). A read error is therefore the outage of
    the block (``lecture_indisponible``), while a missing metadata row is an
    import absent (``import_absent``); the two never mix.
    """
    faits = (await get_provenance_facts(session, [DATASET])).get(DATASET)
    lignes = (
        (await session.execute(_GROUPES_DE_LA_PARCELLE, {"parcelle": id_parcelle}))
        .mappings()
        .all()
    )
    total = int(lignes[0]["total"]) if lignes else 0
    annees = lignes[0]["annees"] if lignes and total else []
    return BdnbLu(
        lignes=[
            {
                "batiment_groupe_id": ligne["batiment_groupe_id"],
                "annee_construction": ligne["annee_construction"],
                "mat_mur_txt": ligne["mat_mur_txt"],
                "mat_toit_txt": ligne["mat_toit_txt"],
            }
            for ligne in lignes
            if ligne["batiment_groupe_id"] is not None
        ],
        total=total,
        annees=frozenset(annees or []),
        faits=faits,
    )


def departement_importe(faits: ProvenanceFacts | None, departement: str) -> bool:
    """Whether the last publication of the import covers *departement*."""
    return (
        faits is not None
        and bool(faits.effective_departments)
        and departement in (faits.effective_departments or [])
    )


def ton_construction(annee: int) -> Ton:
    return "vigilance" if annee < ANNEE_PLOMB else "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    hebergement = contexte.hosting.hostings["bdnb"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources_de(THEME)
        )
    parcelle = parcelle_unique(THEME, contexte.identite.parcelle)
    if isinstance(parcelle, Theme):
        return parcelle
    session = contexte.session
    bloc = await lire_bloc(session, ["bdnb"], lambda: lire_import(session, parcelle))
    if bloc.en_panne or bloc.valeur is None:
        return sans_reponse(THEME, "panne", bloc.panne, sources=sources_de(THEME))
    lu = bloc.valeur
    millesime = periode_de_source(lu.faits)
    sources = sources_de(THEME, {"bdnb": millesime})
    if not departement_importe(lu.faits, contexte.identite.departement.code):
        return sans_reponse(THEME, "panne", "import_absent", sources=sources)
    groupes: list[dict[str, Any]] = lu.lignes
    details = {"groupes": groupes, "liste_incomplete": lu.total > len(groupes)}
    if lu.total == 0:
        return sans_reponse(
            THEME,
            "manque",
            "aucun_batiment_bdnb",
            verdict="La BDNB ne recense aucun bâtiment sur la parcelle de l'adresse.",
            details=details,
            sources=sources,
        )
    if lu.total > 1 and len(lu.annees) != 1:
        return sans_reponse(
            THEME,
            "manque",
            "plusieurs_batiments_sur_la_parcelle",
            verdict=(
                f"La BDNB recense {lu.total} bâtiments sur la parcelle : "
                "aucune année n'est retenue."
            ),
            details=details,
            rattachement=Rattachement(
                entite=ENTITE,
                confiance="incertaine",
                candidats=lu.total,
                motif="plusieurs_batiments_sur_la_parcelle",
            ),
            sources=sources,
        )
    (annee,) = lu.annees
    rattachement = Rattachement(
        entite=ENTITE,
        cle=groupes[0]["batiment_groupe_id"] if lu.total == 1 else None,
        confiance="probable",
        candidats=lu.total if lu.total > 1 else None,
        motif="meme_annee_pour_tous" if lu.total > 1 else None,
    )
    if annee is None:
        return sans_reponse(
            THEME,
            "manque",
            "annee_non_renseignee",
            verdict="La BDNB ne renseigne pas l'année de construction du bâtiment.",
            details=details,
            rattachement=rattachement,
            sources=sources,
        )
    verdict = (
        f"Construit en {annee}, avant 1949 : un constat de risque d'exposition "
        "au plomb est exigé."
        if annee < ANNEE_PLOMB
        else f"Construit en {annee} selon la BDNB."
    )
    return theme(
        THEME,
        etat="ok",
        ton=ton_construction(annee),
        verdict=verdict,
        chiffres=[
            Chiffre(
                id="annee",
                libelle="Année de construction",
                valeur=annee,
                unite="annee",
                millesime=millesime,
            )
        ],
        details=details,
        rattachement=rattachement,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="construction",
    section="batiment",
    ordre=30,
    titre="Construction",
    granularite=granularite("batiment"),
    sources=("bdnb",),
    lire=lire,
    rattachement_entite=ENTITE,
)
