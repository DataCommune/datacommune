"""Summary of the report: points to check, split by scale, and key figures.

A point to check is a theme that answered (``ok`` or ``zero``) with the tone
``vigilance``; a reassuring point answered with the tone ``rassurant``. A
theme that does not concern the address is said apart (04.7-17, as the
front does). ``zero`` counts as an answer: the competitor has
no such state and serves its zeros as ``ok`` (04.7-01, « Décisions »).

The points are split by the real granularity of the theme (CONTEXT,
« Invariants »): a fact of the commune, the IRIS or the departement is never
counted as a fact of the address, nor a radius wider than 1 km (UI-SPEC
§ 4.2, aligned in 04.7-17 on the rule the front applied).

The four key figures are read from one theme each, by the ids of its
figures; the figure takes the state of its theme, and a theme that answered
without that figure is a gap.
"""

from collections.abc import Sequence

from app.rapport.schemas import (
    ETATS_SANS_REPONSE,
    ETATS_SERVIS,
    SECTIONS,
    ChiffreCle,
    EtatTheme,
    Granularite,
    IdChiffreCle,
    ParSection,
    PointResume,
    ResumeRapport,
    Theme,
)

NIVEAUX_ADRESSE = frozenset({"adresse", "batiment", "parcelle"})
# A radius counts for the address up to 1 km; wider is the area.
RAYON_ADRESSE_MAX_M = 1000


def est_de_l_adresse(granularite: Granularite) -> bool:
    if granularite.niveau in NIVEAUX_ADRESSE:
        return True
    return (
        granularite.niveau == "rayon"
        and granularite.rayon_m is not None
        and granularite.rayon_m <= RAYON_ADRESSE_MAX_M
    )


# (key figure, label, theme id, figure ids in the order served).
CHIFFRES_CLES: tuple[tuple[IdChiffreCle, str, str, tuple[str, ...]], ...] = (
    ("prix_m2_median", "Prix médian au m²", "prix_commune", ("maison", "appartement")),
    ("loyer_m2", "Loyer au m²", "loyers", ("appartement", "maison")),
    ("dpe_dominant", "DPE dominant à l'adresse", "dpe", ("classe_dominante",)),
    ("annee_construction", "Année de construction", "construction", ("annee",)),
)


def _point(theme: Theme) -> PointResume:
    return PointResume(
        theme=theme.id,
        titre=theme.titre,
        verdict=theme.verdict,
        granularite=theme.granularite,
    )


def est_point_a_verifier(theme: Theme) -> bool:
    return theme.etat in ETATS_SERVIS and theme.ton == "vigilance"


def est_rassurant(theme: Theme) -> bool:
    """A served favourable reading; a theme that does not concern the
    address is said apart (``non_concernes``), as the front does."""
    return theme.etat in ETATS_SERVIS and theme.ton == "rassurant"


def chiffre_cle(
    id_: IdChiffreCle, libelle: str, theme_id: str, ids: tuple[str, ...], themes
) -> ChiffreCle:
    theme = next((t for t in themes if t.id == theme_id), None)
    if theme is None:
        return ChiffreCle(
            id=id_, libelle=libelle, theme=theme_id, etat="manque", motif="theme_absent"
        )
    chiffres = [c for i in ids for c in theme.chiffres if c.id == i]
    etat: EtatTheme = theme.etat
    motif = theme.motif
    if etat == "ok" and not chiffres:
        etat, motif = "manque", "chiffre_absent"
    return ChiffreCle(
        id=id_,
        libelle=libelle,
        theme=theme_id,
        etat=etat,
        granularite=theme.granularite,
        chiffres=chiffres if etat == "ok" else [],
        motif=motif,
    )


def _sources_en_panne(pannes: Sequence[Theme]) -> list[str]:
    """Keys of the sources of the themes in outage: the live ones when the
    theme has some, else all of them."""
    cles = []
    for theme in pannes:
        directes = [s.cle for s in theme.sources if s.mode == "direct"]
        cles.extend(directes or [s.cle for s in theme.sources])
    return list(dict.fromkeys(cles))


def resumer(themes: Sequence[Theme]) -> ResumeRapport:
    """The summary of *themes*, read as served (tones already aligned)."""
    points = [t for t in themes if est_point_a_verifier(t)]
    rassurants = [t for t in themes if est_rassurant(t)]
    pannes = [t for t in themes if t.etat == "panne"]
    lus = [t for t in themes if t.etat not in ETATS_SANS_REPONSE]
    return ResumeRapport(
        points_adresse=[_point(t) for t in points if est_de_l_adresse(t.granularite)],
        points_commune=[
            _point(t) for t in points if not est_de_l_adresse(t.granularite)
        ],
        rassurants=[_point(t) for t in rassurants],
        rassurants_adresse=[
            _point(t) for t in rassurants if est_de_l_adresse(t.granularite)
        ],
        rassurants_commune=[
            _point(t) for t in rassurants if not est_de_l_adresse(t.granularite)
        ],
        pannes=[_point(t) for t in pannes],
        sources_en_panne=_sources_en_panne(pannes),
        non_concernes=[_point(t) for t in themes if t.etat == "non_concerne"],
        partiels=[_point(t) for t in themes if t.pannes_partielles],
        sans_valeur=[
            _point(t) for t in themes if t.etat in ("manque", "hors_couverture")
        ],
        en_cours=[_point(t) for t in themes if t.etat == "en_cours"],
        lus=len(lus),
        lus_adresse=sum(1 for t in lus if est_de_l_adresse(t.granularite)),
        lus_secteur=sum(1 for t in lus if not est_de_l_adresse(t.granularite)),
        total_criteres=len(themes),
        par_section={
            section: ParSection(
                vigilance=sum(1 for t in points if t.section == section),
                pannes=sum(1 for t in pannes if t.section == section),
                lus=sum(1 for t in lus if t.section == section),
                total=sum(1 for t in themes if t.section == section),
            )
            for section in SECTIONS
        },
        chiffres_cles=[
            chiffre_cle(id_, libelle, theme_id, ids, themes)
            for id_, libelle, theme_id, ids in CHIFFRES_CLES
        ],
    )


def resume_hors_couverture() -> ResumeRapport:
    """An address out of coverage: no theme read, every key figure out."""
    return ResumeRapport(
        points_adresse=[],
        points_commune=[],
        rassurants=[],
        chiffres_cles=[
            ChiffreCle(id=id_, libelle=libelle, theme=theme_id, etat="hors_couverture")
            for id_, libelle, theme_id, _ in CHIFFRES_CLES
        ],
    )
