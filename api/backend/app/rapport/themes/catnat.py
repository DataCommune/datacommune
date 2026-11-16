"""« Catastrophes naturelles » : CatNat decrees of the commune (GASPAR).

Granularity: commune (read by INSEE code). Every recognition decree of the
commune (up to five pages of 100), its peril, event dates and publication.
A read cut by the page bound is partial: « no decree » is said only on a
complete read. Recency is the one of the event (its start date), not of the
decree: the figure counts recognised events that started in the last ten
years.

Tone rule: vigilance when at least one recognised event started in the last
ten years; neutral when every recognised event is older; rassurant when
the commune has no recognition decree, on a complete read.
"""

from datetime import date, datetime

from app.rapport.partage import LignesLues, lignes_commune
from app.rapport.schemas import Chiffre, Theme, Ton
from app.rapport.socle import (
    ContexteAmont,
    ContexteRapport,
    DefinitionTheme,
    granularite,
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)

ANNEES_RECENTES = 10
DETAILS_MAX = 5


def jour(texte: object) -> date | None:
    """« 07/10/2024 » as GASPAR writes its dates."""
    try:
        return datetime.strptime(str(texte), "%d/%m/%Y").date()
    except ValueError:
        return None


def il_y_a(jour_: date, annees: int) -> date:
    """The same day *annees* years earlier (29 February gives the 28th)."""
    if (jour_.month, jour_.day) == (2, 29):
        jour_ = jour_.replace(day=28)
    return jour_.replace(year=jour_.year - annees)


def ton_catnat(recents: int, total: int, complet: bool) -> Ton:
    if recents > 0:
        return "vigilance"
    if total > 0:
        return "neutre"
    return "rassurant" if complet else "neutre"


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"georisques": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["georisques"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, LignesLues):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    seuil = il_y_a(contexte.as_of, ANNEES_RECENTES)
    arretes = sorted(
        (
            {
                "risque": ligne.get("libelle_risque_jo"),
                "debut": jour(ligne.get("date_debut_evt")),
                "fin": jour(ligne.get("date_fin_evt")),
                "publication_jo": jour(ligne.get("date_publication_jo")),
            }
            for ligne in lu.lignes
        ),
        key=lambda a: a["debut"] or date.min,
        reverse=True,
    )
    recents = [a for a in arretes if a["debut"] is not None and a["debut"] >= seuil]
    details = {
        "derniers": [
            {cle: (v.isoformat() if isinstance(v, date) else v) for cle, v in a.items()}
            for a in arretes[:DETAILS_MAX]
        ],
        "liste_incomplete": not lu.complet,
    }
    if not arretes:
        if not lu.complet:
            return sans_reponse(
                THEME, "manque", "lecture_incomplete", details=details, sources=sources
            )
        return theme(
            THEME,
            etat="zero",
            ton="rassurant",
            verdict="Aucune catastrophe naturelle reconnue dans la commune.",
            details=details,
            sources=sources,
        )
    total = len(arretes)
    prefixe = "Au moins " if not lu.complet else ""
    verdict = (
        f"{prefixe}{pluriel(total, 'catastrophe naturelle', 'catastrophes naturelles')}"
        f" reconnue{'s' if total > 1 else ''} dans la commune, dont {len(recents)}"
        f" survenue{'s' if len(recents) > 1 else ''} ces {ANNEES_RECENTES}"
        " dernières années."
    )
    return theme(
        THEME,
        etat="ok",
        ton=ton_catnat(len(recents), total, lu.complet),
        verdict=verdict,
        chiffres=[
            Chiffre(
                id="arretes",
                libelle="Arrêtés CatNat",
                valeur=total,
                unite="count",
                millesime=contexte.as_of.isoformat(),
            ),
            Chiffre(
                id="recents",
                libelle=f"Événements des {ANNEES_RECENTES} dernières années",
                valeur=len(recents),
                unite="count",
                millesime=contexte.as_of.isoformat(),
            ),
        ],
        details=details,
        sources=sources,
    )


async def amont(contexte: ContexteAmont) -> object:
    return await lignes_commune(contexte, "/gaspar/catnat", pages_max=5)


THEME = DefinitionTheme(
    id="catnat",
    section="risques",
    ordre=25,
    titre="Catastrophes naturelles",
    granularite=granularite("commune"),
    sources=("georisques",),
    lire=lire,
    amont=amont,
)
