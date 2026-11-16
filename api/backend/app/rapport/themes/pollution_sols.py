"""« Sols pollués » : polluted or former industrial sites on the parcel (ERRIAL).

Granularity: parcel. ERRIAL counts, for the parcel it finds at the BAN point,
the former industrial sites (BASIAS), the polluted sites (BASOL) and the soil
information sectors (SIS) on the parcel and nearby; the classified
installations on the parcel are in the details.

A conclusion about the parcel is served only when the ERRIAL parcel is the
parcel of the address (``unique`` relation, same commune, section and
number): another parcel, or an address without a single parcel, is a gap,
never a reassurance nor a point to check. A count ERRIAL could not compute
(``None``, its sub-source unavailable) is an outage.

Tone rule: vigilance when at least one site or sector is on the parcel;
rassurant when none is (a measured zero) and every nearby count was read;
neutre when one of them was not (a partial outage).
"""

from app.rapport.partage import errial, errial_lu
from app.rapport.schemas import Chiffre, Rattachement, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    granularite,
    nombre,
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)

SUR_PARCELLE = ("basias_sur_parcelle", "basol_sur_parcelle", "sis_sur_parcelle")
A_PROXIMITE = ("basias_proximite", "basol_proximite", "sis_proximite")
ENTITE = "parcelle_errial"


async def lire(contexte: ContexteRapport) -> Theme:
    lu = errial_lu(contexte, THEME)
    if isinstance(lu, Theme):
        return lu
    sources = sources_de(THEME, {"errial": contexte.as_of.isoformat()})
    if lu.concordance is not True:
        motif = (
            "parcelle_errial_differente"
            if lu.concordance is False
            else "parcelle_non_verifiee"
        )
        return sans_reponse(
            THEME,
            "manque",
            motif,
            verdict=(
                "ERRIAL ne décrit pas avec certitude la parcelle de l'adresse : "
                "aucune conclusion n'est tirée."
            ),
            rattachement=Rattachement(
                entite=ENTITE, cle=lu.parcelle, confiance="incertaine", motif=motif
            ),
            sources=sources,
        )
    rattachement = Rattachement(entite=ENTITE, cle=lu.parcelle, confiance="sure")
    pollution = lu.resume.get("pollution") or {}
    sur = [pollution.get(cle) for cle in SUR_PARCELLE]
    if any(valeur is None for valeur in sur):
        # ERRIAL serves None for a sub-source it could not read.
        return sans_reponse(
            THEME,
            "panne",
            "amont_indisponible",
            rattachement=rattachement,
            sources=sources,
        )
    total = sum(v for v in sur if v is not None)
    proches = [pollution.get(cle) for cle in A_PROXIMITE]
    details = {
        **{cle: pollution.get(cle) for cle in (*SUR_PARCELLE, *A_PROXIMITE)},
        "icpe_sur_parcelle": pollution.get("icpe_sur_parcelle"),
        "icpe_proximite": pollution.get("icpe_proximite"),
    }
    pannes = ["proximite"] if None in proches else []
    if total == 0:
        total_proches = sum(v for v in proches if v is not None)
        suite = (
            f" ; {pluriel(total_proches, 'site')} à proximité"
            if total_proches and None not in proches
            else ""
        )
        return theme(
            THEME,
            etat="zero",
            ton="neutre" if pannes else "rassurant",
            verdict=(
                "Aucun site pollué ou ancien site industriel recensé sur la "
                f"parcelle{suite}."
            ),
            details=details,
            rattachement=rattachement,
            sources=sources,
            pannes_partielles=pannes,
        )
    return theme(
        THEME,
        etat="ok",
        ton="vigilance",
        verdict=(
            f"{nombre(total)} sites pollués ou anciens sites industriels "
            "recensés sur la parcelle."
            if total > 1
            else "Un site pollué ou ancien site industriel recensé sur la parcelle."
        ),
        chiffres=[
            Chiffre(
                id="sur_parcelle",
                libelle="Sites sur la parcelle",
                valeur=total,
                unite="count",
                millesime=contexte.as_of.isoformat(),
            )
        ],
        details=details,
        rattachement=rattachement,
        sources=sources,
        pannes_partielles=pannes,
    )


THEME = DefinitionTheme(
    id="pollution_sols",
    section="risques",
    ordre=60,
    titre="Sols pollués",
    granularite=granularite("parcelle"),
    sources=("errial",),
    lire=lire,
    amont=errial,
    rattachement_entite=ENTITE,
)
