"""« Eau du robinet » : 12 months of the sanitary control of the tap water
of the commune (Hub'Eau, ARS), and its hardness.

Granularity: commune. The ARS samples each distribution unit (UDI) of the
commune; which unit serves the address is not published, so the unit is
named only when the commune has one, and every unit goes in the detail. A
sample is read once, by a tracer parameter that carries the conformity of
the whole sample: Escherichia coli for the bacteriological limits, pH for
the physico-chemical ones. Quality references (comfort indicators) are not
limits and are not read. Only the conclusions « C » and « N » count; a
sample without one is said apart and never read as conform. A rate shown is
rounded down (94.96 % is « 94,9 % », never the threshold itself).

Tone rule, on the ARS conclusion of each sample and on its time rather than
on a raw count (a single old breach among 883 samples is not an alert):
- vigilance when a sample of the last 30 days breaks a quality limit (a
  fact, even on a partial read, then said as « at least » what the page
  shows), or, on complete pages, when the latest
  concluded sample does or the conformity rate of the 12 months falls below
  95 % for either nature: below it, more than one control in twenty breaks
  a limit over the year (the rates of conformity are the indicators P101.1
  and P102.1 of the public water services);
- rassurant otherwise, when both natures have concluded samples and every
  page (samples, units) is complete; an older isolated breach stays in the
  verdict and the detail, with its date;
- a gap otherwise. The hardness is informative.

A partial page shows what it read under names of its own (``*_lus``), never
as the rate or the latest control of the year.
"""

import math
from datetime import timedelta

from app.rapport.libelles import arrondi
from app.rapport.partage import Conformite, EauLue, eau_au_point
from app.rapport.schemas import VERDICT_MAX, Chiffre, Theme
from app.rapport.socle import (
    ContexteRapport,
    DefinitionTheme,
    date_longue,
    granularite,
    nombre,
    pluriel,
    sans_reponse,
    sources_de,
    theme,
)

RECENTS_J = 30
TAUX_MIN = 95.0
# Hardness classes in French degrees, as the ARS write them.
DURETES = ((7, "très douce"), (15, "douce"), (25, "moyennement dure"), (42, "dure"))


def durete(degres: float) -> str:
    return next((nom for borne, nom in DURETES if degres < borne), "très dure")


def taux(c: Conformite) -> float | None:
    """Share of the concluded samples that conform."""
    if not c.conclus:
        return None
    return 100 * c.conformes / c.conclus


def plancher(t: float) -> float:
    """A rate rounded down to a tenth: never above the rate itself."""
    return math.floor(t * 10) / 10


def taux_affiche(c: Conformite) -> float | None:
    t = taux(c)
    return None if t is None else plancher(t)


def non_conformes(bact: Conformite, pc: Conformite) -> list[str]:
    """Dates of the samples breaking a limit of either nature, oldest first;
    a sample breaking both is one breach."""
    return sorted({code: d for c in (bact, pc) for d, code in c.non_conformes}.values())


def alerte(bact: Conformite, pc: Conformite, depuis: str) -> str | None:
    """The verdict of a vigilance, or None (*depuis*: first day of the last
    30 days)."""
    complets = bact.complet and pc.complet
    recentes = [d for d in non_conformes(bact, pc) if d >= depuis]
    if recentes and complets:
        return (
            f"{pluriel(len(recentes), 'contrôle')} hors limites de qualité en "
            f"30 jours, le dernier le {date_longue(recentes[-1])}."
        )
    if recentes:
        # A fact of the page read, not the count of the 30 days.
        return (
            f"Lecture partielle : au moins {pluriel(len(recentes), 'contrôle')} "
            "hors limites de qualité en 30 jours, le plus récent lu le "
            f"{date_longue(recentes[-1])}."
        )
    # A partial page holds neither the latest control nor the year's rate.
    derniers = [(c.dernier, c.dernier_conforme) for c in (bact, pc) if c.dernier]
    if complets and derniers:
        instant = max(d for d, _ in derniers)
        if any(d == instant and conforme is False for d, conforme in derniers):
            return (
                f"{dernier_controle(bact, pc).capitalize()} hors limites de qualité, "
                f"le {date_longue(instant.date().isoformat())}."
            )
    for nature, c in (("bactériologique", bact), ("physico-chimique", pc)):
        t = taux(c)
        if c.complet and t is not None and t < TAUX_MIN:
            return (
                f"Conformité {nature} de {nombre(plancher(t))} % sur 12 mois, "
                "sous le seuil de 95 %."
            )
    return None


def dernier_controle(bact: Conformite, pc: Conformite) -> str:
    """« dernier contrôle conclu » when a later sample has no conclusion."""
    conclus = [c.dernier for c in (bact, pc) if c.dernier]
    lus = [c.dernier_lu for c in (bact, pc) if c.dernier_lu]
    plus_tard = bool(conclus and lus) and max(lus) > max(conclus)
    return "dernier contrôle conclu" if plus_tard else "dernier contrôle"


def _conformite(c: Conformite) -> dict:
    if not c.complet:
        # A page short of rows: what it shows, never the figures of the year.
        return {
            "lecture_complete": False,
            "prelevements_lus": c.prelevements,
            "non_conformes_lus": len(c.non_conformes),
            "dates_non_conformes_lues": list(c.dates_non_conformes),
        }
    return {
        "prelevements": c.prelevements,
        "non_conformes": len(c.non_conformes),
        "sans_conclusion": c.sans_conclusion,
        "dates_non_conformes": list(c.dates_non_conformes),
        "taux_conformite": taux_affiche(c),
        "dernier_prelevement": c.dernier_lu.date().isoformat()
        if c.dernier_lu
        else None,
        "dernier_controle_conclu": c.dernier.date().isoformat() if c.dernier else None,
        "dernier_conforme": c.dernier_conforme,
        "lecture_complete": c.complet,
    }


def _borne(*candidats: str) -> str:
    return next((v for v in candidats if len(v) <= VERDICT_MAX), candidats[-1])


def verdict_conforme(lu: EauLue) -> str:
    bact, pc = lu.bacteriologique, lu.physico_chimique
    reseau = (
        f"Réseau {lu.reseaux[0].nom} : "
        if len(lu.reseaux) == 1 and lu.reseaux[0].nom
        else ""
    )
    anciennes = non_conformes(bact, pc)
    if anciennes:
        suite = (
            f"conforme au {dernier_controle(bact, pc)} ; "
            f"{pluriel(len(anciennes), 'contrôle')} hors limites en 12 mois sur "
            f"{nombre(lu.prelevements)}, le dernier le {date_longue(anciennes[-1])}."
        )
    else:
        suite = (
            "conforme aux limites de qualité : "
            f"{nombre(bact.conclus)} contrôles bactériologiques en 12 mois."
        )
    return _borne(f"{reseau}eau {suite}", f"Eau {suite}")


async def lire(contexte: ContexteRapport) -> Theme:
    sources = sources_de(THEME, {"eau_potable": contexte.as_of.isoformat()})
    hebergement = contexte.hosting.hostings["eau_potable"]
    if hebergement.etat != "heberge":
        return sans_reponse(
            THEME, "hors_couverture", hebergement.motif, sources=sources
        )
    lu = contexte.amonts.get(THEME.id)
    if not isinstance(lu, EauLue):
        return sans_reponse(THEME, "panne", "amont_indisponible", sources=sources)
    bact, pc = lu.bacteriologique, lu.physico_chimique
    details = {
        "reseaux": [{"code": r.code, "nom": r.nom} for r in lu.reseaux],
        "reseaux_complets": lu.reseaux_complets,
        "annee_reseaux": lu.annee_reseaux,
        "bacteriologique": _conformite(bact),
        "physico_chimique": _conformite(pc),
        "durete_f": lu.durete_f,
        "durete": durete(lu.durete_f) if lu.durete_f is not None else None,
        "durete_date": lu.durete_date,
        "prelevements": lu.prelevements if bact.complet and pc.complet else None,
    }
    chiffres = (
        [
            Chiffre(
                id="durete",
                libelle="Dureté (titre hydrotimétrique)",
                valeur=float(arrondi(lu.durete_f, 1)),
                unite="degre_francais",
                millesime=lu.durete_date,
            )
        ]
        if lu.durete_f is not None
        else []
    )
    depuis = (contexte.as_of - timedelta(days=RECENTS_J)).isoformat()
    vigilance = alerte(bact, pc, depuis)
    if vigilance:
        return theme(
            THEME,
            etat="ok",
            ton="vigilance",
            verdict=vigilance,
            chiffres=chiffres,
            details=details,
            sources=sources,
        )
    if not (bact.complet and pc.complet and lu.reseaux_complets):
        return sans_reponse(
            THEME, "manque", "lecture_incomplete", details=details, sources=sources
        )
    if not (bact.conclus and pc.conclus):
        return sans_reponse(
            THEME,
            "manque",
            "aucun_prelevement"
            if not (bact.prelevements or pc.prelevements)
            else "prelevements_partiels",
            details=details,
            sources=sources,
        )
    verdict = verdict_conforme(lu)
    return theme(
        THEME,
        etat="ok",
        ton="rassurant",
        verdict=verdict[0].upper() + verdict[1:],
        chiffres=chiffres,
        details=details,
        sources=sources,
    )


THEME = DefinitionTheme(
    id="eau_potable",
    section="vivre",
    ordre=62,
    titre="Eau du robinet",
    granularite=granularite("commune"),
    sources=("eau_potable",),
    lire=lire,
    amont=eau_au_point,
)
