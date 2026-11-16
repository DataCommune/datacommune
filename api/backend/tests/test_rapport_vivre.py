"""Living-here themes of the address report (04.7-06), without a database.

Delinquency is a fact of the commune against its department, the same year;
a value the SSMSI does not publish is a gap, never a zero. Tap water is the
sanitary control of the commune's distribution units over 12 months. Schools
are the nearest public ones by level, with their IPS and added value, and
the sector college of the address.
"""

import itertools
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from app.adresse import lieu
from app.rapport import partage
from app.rapport.partage import (
    Conformite,
    DelinquanceLue,
    EauLue,
    EcolesLues,
    Etablissement,
    IndicateurDelinquance,
    Reseau,
)
from app.rapport.themes import delinquance, eau_potable, ecoles
from tests.test_rapport_themes import AS_OF, POINT, _contexte

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _caches_vides():
    partage.vider_caches()
    yield
    partage.vider_caches()


def _amont(contexte):
    from app.rapport.socle import ContexteAmont, Memo

    return ContexteAmont(
        identite=contexte.identite,
        point=contexte.point,
        hosting=contexte.hosting,
        as_of=AS_OF,
        memo=Memo(),
    )


# --- Délinquance -----------------------------------------------------------------


def _communes(*lignes) -> bytes:
    """The communal SSMSI file as data.gouv.fr serves it (parquet), from
    ``(code, annee, indicateur, nombre, taux, est_diffuse)`` rows."""
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "CODGEO_2026": pa.array([ligne[0] for ligne in lignes], pa.string()),
            "annee": pa.array([ligne[1] for ligne in lignes], pa.int32()),
            "indicateur": pa.array(
                [ligne[2] for ligne in lignes], pa.string()
            ).dictionary_encode(),
            "nombre": pa.array([ligne[3] for ligne in lignes], pa.int32()),
            "taux_pour_mille": pa.array([ligne[4] for ligne in lignes], pa.float64()),
            "est_diffuse": pa.array([ligne[5] for ligne in lignes], pa.string()),
        }
    )
    tampon = io.BytesIO()
    pq.write_table(table, tampon)
    return tampon.getvalue()


def _departements(*lignes) -> bytes:
    """The departmental SSMSI file as data.gouv.fr serves it (csv: BOM,
    semicolons, quoted cells, decimal comma), from ``(code, annee,
    indicateur, nombre, taux)`` rows; None is an empty cell."""

    def cellule(valeur):
        texte = "" if valeur is None else str(valeur).replace(".", ",")
        return f'"{texte}"'

    entete = ";".join(
        f'"{nom}"'
        for nom in ("Code_departement", "Code_region", "annee", "indicateur",
                    "nombre", "taux_pour_mille")
    )  # fmt: skip
    corps = [
        ";".join(cellule(v) for v in (code, "52", annee, nom, nombre, taux))
        for code, annee, nom, nombre, taux in lignes
    ]
    return ("﻿" + "\n".join([entete, *corps]) + "\n").encode("utf-8")


CAMBRIOLAGES = "Cambriolages de logement"
NANTES_2025 = ("44109", 2025, CAMBRIOLAGES, 1227, 6.3368504, "diff")
LA_ROCHE_2025 = ("85191", 2025, CAMBRIOLAGES, 210, 4.6, "diff")
LOIRE_ATLANTIQUE_2025 = ("44", 2025, CAMBRIOLAGES, 5053, 6.3213546)


def _fichiers(mock, communes, departements=(LOIRE_ATLANTIQUE_2025,)):
    """The two SSMSI files served; returns their routes."""
    route_communes = mock.get(partage.SSMSI_COMMUNES_FICHIER).mock(
        return_value=httpx.Response(200, content=_communes(*communes))
    )
    route_departements = mock.get(partage.SSMSI_DEPARTEMENTS_FICHIER).mock(
        return_value=httpx.Response(200, content=_departements(*departements))
    )
    return route_communes, route_departements


def _a(code_insee, nom):
    from app.adresse.schemas import CommuneRef

    base = _contexte()
    return replace(
        base,
        identite=base.identite.model_copy(
            update={"commune": CommuneRef(code_insee=code_insee, nom=nom)}
        ),
    )


def test_an_indicator_under_the_diffusion_threshold_is_never_a_zero():
    base = partage.base_communale_de(
        _communes(
            ("44109", 2025, CAMBRIOLAGES, 0, 0.0, "diff"),
            ("44109", 2025, "Vols avec armes", None, None, "ndiff"),
        )
    )
    lus = base.indicateurs("44109")
    assert (lus[0].nombre, lus[0].taux_pour_mille, lus[0].diffuse) == (0, 0.0, True)
    assert (lus[1].nombre, lus[1].taux_pour_mille, lus[1].diffuse) == (
        None,
        None,
        False,
    )
    # A department has no diffusion column: an empty cell is a gap, not a zero.
    deps = partage.base_departementale_de(
        _departements(("44", 2025, CAMBRIOLAGES, 0, 0), ("44", 2025, "x", None, None))
    )
    zero, vide = deps[("44", 2025)]
    assert (zero.nombre, zero.taux_pour_mille, zero.diffuse) == (0, 0.0, True)
    assert (vide.nombre, vide.taux_pour_mille, vide.diffuse) == (None, None, False)


def test_the_latest_year_is_read_for_each_commune():
    base = partage.base_communale_de(
        _communes(
            ("44109", 2024, CAMBRIOLAGES, 1300, 6.7, "diff"),
            NANTES_2025,
            # A commune the latest edition no longer lists keeps its own year.
            ("85191", 2023, CAMBRIOLAGES, 200, 4.4, "diff"),
        )
    )
    assert [i.annee for i in base.indicateurs("44109")] == [2025]
    assert [i.annee for i in base.indicateurs("85191")] == [2023]
    assert base.indicateurs("49007") == []


@pytest.mark.parametrize(
    "lire",
    [
        lambda: partage.base_communale_de(b"not a parquet file"),
        lambda: partage.base_communale_de(_departements(LOIRE_ATLANTIQUE_2025)),
        lambda: partage.base_communale_de(
            _communes(("44109", 2025, CAMBRIOLAGES, 1, 1.0, "peut-etre"))
        ).indicateurs("44109"),
        lambda: partage.base_departementale_de(b'"a";"b"\n"1";"2"\n'),
        lambda: partage.base_departementale_de(
            _departements(("44", 2025, CAMBRIOLAGES, 10, "abc"))
        ),
        lambda: partage.base_departementale_de(_departements()),
        lambda: partage.base_departementale_de(b"\xff\xfe not utf-8"),
    ],
    ids=["not-parquet", "csv-as-parquet", "diffusion", "columns", "rate",
         "empty", "encoding"],
)  # fmt: skip
def test_an_unreadable_ssmsi_file_is_an_outage(lire):
    from app.common.exceptions import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError):
        lire()


@pytest.mark.parametrize(
    ("commune", "departement", "ton"),
    [(9.5, 6.3, "vigilance"), (3.1, 6.3, "rassurant"), (6.0, 6.3, "neutre"),
     (6.0, None, "neutre"), (2.9, 6.0, "rassurant"), (9.1, 6.0, "vigilance"),
     # The plan says « dépasse » and « sous »: the bounds themselves are neutral.
     (3.0, 6.0, "neutre"), (9.0, 6.0, "neutre"),
     # A departmental zero is a rate, not a missing one.
     (1.0, 0.0, "vigilance"), (0.0, 0.0, "neutre")],
)  # fmt: skip
def test_the_tone_compares_burglaries_with_the_department(commune, departement, ton):
    assert delinquance.ton_delinquance(commune, departement) == ton


def _delinquance(
    commune_taux=6.3, dep_taux=6.3, diffuse=True, annee=2025, zone="44109"
):
    cambriolage = IndicateurDelinquance(
        "Cambriolages de logement",
        annee,
        1227 if diffuse else None,
        commune_taux if diffuse else None,
        diffuse,
    )
    return DelinquanceLue(
        annee=annee,
        commune=[cambriolage],
        departement=[
            IndicateurDelinquance(
                "Cambriolages de logement", annee, 5053, dep_taux, True
            )
        ],
        zone=zone,
    )


async def test_burglaries_are_said_for_the_commune_against_its_department():
    theme = await delinquance.lire(
        _contexte(amonts={"delinquance": _delinquance(9.5, 6.3)})
    )
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "commune",
    )
    assert theme.verdict == (
        "Cambriolages de logement en 2025 : 9,5 ‰ dans la commune, 6,3 ‰ dans "
        "le département."
    )
    assert theme.sources[0].millesime == "2025"
    assert [c.valeur for c in theme.chiffres] == [9.5, 6.3]


async def test_a_rate_has_one_rounding_rule_in_figures_and_verdict():
    theme = await delinquance.lire(
        _contexte(amonts={"delinquance": _delinquance(6.25, 6.35)})
    )
    assert [c.valeur for c in theme.chiffres] == [6.3, 6.4]  # `round`: 6.2, 6.3
    assert theme.verdict == (
        "Cambriolages de logement en 2025 : 6,3 ‰ dans la commune, 6,4 ‰ dans "
        "le département."
    )


async def test_the_rate_of_an_arrondissement_is_said_for_the_arrondissement():
    # Paris 12e, SSMSI 2025 (probe of 2026-10-30): 3.5 per 1 000 dwellings.
    theme = await delinquance.lire(
        _contexte(amonts={"delinquance": _delinquance(3.5236347, 6.3, zone="75112")})
    )
    assert theme.verdict == (
        "Cambriolages de logement en 2025 : 3,5 ‰ dans l'arrondissement, 6,3 ‰ "
        "dans le département."
    )
    assert theme.chiffres[0].libelle == (
        "Cambriolages pour 1 000 logements, arrondissement"
    )
    assert theme.details["zone"] == {"code": "75112", "niveau": "arrondissement"}
    tue = await delinquance.lire(
        _contexte(amonts={"delinquance": _delinquance(diffuse=False, zone="75112")})
    )
    assert "non publiés pour l'arrondissement" in tue.verdict


async def test_an_undisclosed_rate_is_a_gap():
    theme = await delinquance.lire(
        _contexte(amonts={"delinquance": _delinquance(diffuse=False)})
    )
    assert (theme.etat, theme.motif, theme.ton) == ("manque", "non_diffuse", "info")
    assert "seuil de diffusion" in theme.verdict
    assert "pour la commune" in theme.verdict


async def test_a_commune_absent_from_the_files_and_an_outage_are_told_apart():
    absente = await delinquance.lire(
        _contexte(
            amonts={
                "delinquance": DelinquanceLue(annee=None, commune=[], departement=[])
            }
        )
    )
    assert (absente.etat, absente.motif) == ("manque", "commune_absente")
    panne = await delinquance.lire(_contexte(amonts={"delinquance": lieu.PanneAmont()}))
    assert panne.etat == "panne"


async def test_the_department_is_read_on_the_year_of_the_commune():
    with respx.mock() as mock:
        _fichiers(
            mock,
            communes=[NANTES_2025, ("44109", 2024, CAMBRIOLAGES, 1300, 6.7, "diff")],
            departements=[
                ("44", 2024, CAMBRIOLAGES, 5400, 7.0),
                LOIRE_ATLANTIQUE_2025,
                ("85", 2025, CAMBRIOLAGES, 1000, 3.6),
            ],
        )
        lu = await delinquance.THEME.amont(_amont(_contexte()))
    assert lu.annee == 2025 and len(lu.commune) == 1
    assert [(i.annee, i.taux_pour_mille) for i in lu.departement] == [(2025, 6.3213546)]


async def test_an_arrondissement_is_read_first_then_its_commune():
    paris_12 = ("75112", 2025, CAMBRIOLAGES, 450, 3.5236347, "diff")
    paris = ("75056", 2025, CAMBRIOLAGES, 6000, 5.1, "diff")
    with respx.mock() as mock:
        _fichiers(mock, communes=[paris_12, paris])
        lu = await delinquance.THEME.amont(_amont(_a("75112", "Paris 12e")))
    assert (lu.zone, lu.commune[0].taux_pour_mille) == ("75112", 3.5236347)
    partage.vider_caches()
    with respx.mock() as mock:
        _fichiers(mock, communes=[paris])
        lu = await delinquance.THEME.amont(_amont(_a("75112", "Paris 12e")))
    # No row for the arrondissement itself: the rows came from its commune.
    assert (lu.zone, lu.commune[0].taux_pour_mille) == ("75056", 5.1)


async def test_the_first_page_of_a_commune_never_asked_reads_its_delinquency(
    monkeypatch,
):
    # 2026-11-07, first page after a deployment: « source indisponible », then
    # 6.3 ‰ a few seconds later. The tabular API answers a query in 7 to 12 s
    # when it is not in its cache (MISS, 5.2 M rows scanned), then in 0.04 s
    # (probe 2026-11-09): the first page of any commune nobody asked about
    # lately was an outage, though the source was up. The tabular API is
    # served here as it behaves, to catch a return to per-commune queries.
    import asyncio

    monkeypatch.setattr(partage, "DEADLINE_S", 0.2)
    deja_demandees: set[str] = set()

    async def tabulaire(requete):
        if str(requete.url) not in deja_demandees:  # MISS of the upstream cache
            deja_demandees.add(str(requete.url))
            await asyncio.sleep(1.0)
        ligne = {"indicateur": CAMBRIOLAGES, "annee": 2025, "nombre": 1227,
                 "taux_pour_mille": 6.3368504, "est_diffuse": "diff"}  # fmt: skip
        return httpx.Response(200, json={"data": [ligne], "meta": {"total": 1}})

    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith="https://tabular-api.data.gouv.fr/").mock(
            side_effect=tabulaire
        )
        _fichiers(mock, communes=[NANTES_2025, LA_ROCHE_2025])
        # The start of the instance (lifespan), then its first two pages.
        await partage.prechauffer_delinquance()
        nantes = await delinquance.THEME.amont(_amont(_contexte()))
        roche = await delinquance.THEME.amont(_amont(_a("85191", "La Roche-sur-Yon")))
    assert isinstance(nantes, DelinquanceLue), nantes
    assert isinstance(roche, DelinquanceLue), roche
    assert (nantes.annee, nantes.commune[0].taux_pour_mille) == (2025, 6.3368504)
    assert (roche.annee, roche.commune[0].taux_pour_mille) == (2025, 4.6)


@pytest.mark.parametrize("fichier", ["communes", "departements"])
async def test_an_unreachable_file_is_an_outage_never_kept(fichier):
    with respx.mock() as mock:
        routes = dict(
            zip(
                ("communes", "departements"),
                _fichiers(mock, [NANTES_2025]),
                strict=True,
            )
        )
        routes[fichier].mock(
            side_effect=[
                httpx.Response(503),
                httpx.Response(
                    200,
                    content=_communes(NANTES_2025)
                    if fichier == "communes"
                    else _departements(LOIRE_ATLANTIQUE_2025),
                ),
            ]
        )
        premiere = await delinquance.THEME.amont(_amont(_contexte()))
        seconde = await delinquance.THEME.amont(_amont(_contexte()))
    assert isinstance(premiere, lieu.PanneAmont)
    assert isinstance(seconde, DelinquanceLue)
    assert seconde.commune[0].taux_pour_mille == 6.3368504
    assert seconde.departement[0].taux_pour_mille == 6.3213546


async def test_a_file_that_is_not_the_ssmsi_base_is_an_outage():
    with respx.mock() as mock:
        _fichiers(mock, [NANTES_2025])
        mock.get(partage.SSMSI_COMMUNES_FICHIER).mock(
            return_value=httpx.Response(200, content=b"not a parquet file")
        )
        lu = await delinquance.THEME.amont(_amont(_contexte()))
    assert isinstance(lu, lieu.PanneAmont)


async def test_the_files_are_downloaded_once_for_several_pages():
    with respx.mock() as mock:
        communes, departements = _fichiers(mock, [NANTES_2025, LA_ROCHE_2025])
        nantes = await delinquance.THEME.amont(_amont(_contexte()))
        roche = await delinquance.THEME.amont(_amont(_a("85191", "La Roche-sur-Yon")))
    assert (nantes.commune[0].taux_pour_mille, roche.commune[0].taux_pour_mille) == (
        6.3368504,
        4.6,
    )
    assert (communes.call_count, departements.call_count) == (1, 1)


async def test_a_base_past_its_day_answers_while_it_is_read_again(monkeypatch):
    import asyncio

    from app.config import settings

    with respx.mock() as mock:
        communes, _ = _fichiers(mock, [NANTES_2025])
        await partage.prechauffer_delinquance()
        # A day later, the producer is down.
        monkeypatch.setattr(settings, "proxy_cache_ttl_s", 0)
        communes.mock(return_value=httpx.Response(503))
        lu = await delinquance.THEME.amont(_amont(_a("44109", "Nantes")))
        for _ in range(5):  # let the background reading fail
            await asyncio.sleep(0)
    assert isinstance(lu, DelinquanceLue)
    assert communes.call_count == 2


# --- Eau du robinet --------------------------------------------------------------


_CODES = itertools.count()


def _t(texte):
    """An instant from « 2026-10-20 » or « 2026-10-20T18:00:00Z » (UTC)."""
    instant = datetime.fromisoformat(texte)
    return instant if instant.tzinfo else instant.replace(tzinfo=UTC)


def _conformite(
    prelevements=10,
    dates_non_conformes=(),
    complet=True,
    dernier="2026-10-20",
    dernier_conforme=True,
    sans_conclusion=0,
    codes=None,
    dernier_lu=None,
):
    """*prelevements* samples, of which the breaches at *dates_non_conformes*
    (each its own sample unless *codes* says otherwise) and
    *sans_conclusion* without an ARS conclusion; the others conform."""
    dates = sorted(dates_non_conformes, reverse=True)
    codes = codes or [f"P{next(_CODES)}" for _ in dates]
    conformes = prelevements - len(dates) - sans_conclusion
    conclus = conformes + len(dates)
    return Conformite(
        prelevements=prelevements,
        conformes=conformes,
        non_conformes=tuple(zip(dates, codes, strict=True)),
        dernier=_t(dernier) if conclus else None,
        dernier_conforme=dernier_conforme if conclus else None,
        dernier_lu=_t(dernier_lu or dernier) if prelevements else None,
        complet=complet,
    )


def _eau(
    bact=None,
    pc=None,
    reseaux=None,
    durete=12.3,
    reseaux_complets=True,
    prelevements=None,
):
    """By default every sample of the smaller nature also carries the other
    one, as most do: the samples counted once are those of the larger."""
    bact = bact or _conformite(419)
    pc = pc or _conformite(421)
    return EauLue(
        reseaux=reseaux
        if reseaux is not None
        else [
            Reseau("044000138", "SECTEUR-NANTES"),
            Reseau("044000115", "SECTEUR-REZE"),
        ],
        annee_reseaux="2026",
        bacteriologique=bact,
        physico_chimique=pc,
        durete_f=durete,
        durete_date="2025-12-30",
        prelevements=max(bact.conclus, pc.conclus)
        if prelevements is None
        else prelevements,
        reseaux_complets=reseaux_complets,
    )


async def _theme_eau(lu):
    return await eau_potable.lire(_contexte(amonts={"eau_potable": lu}))


async def test_twelve_conform_months_are_reassuring():
    theme = await _theme_eau(_eau())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "rassurant",
        "commune",
    )
    assert theme.verdict == (
        "Eau conforme aux limites de qualité : 419 contrôles bactériologiques en "
        "12 mois."
    )
    assert theme.details["durete"] == "douce"
    assert [r["nom"] for r in theme.details["reseaux"]] == [
        "SECTEUR-NANTES",
        "SECTEUR-REZE",
    ]


async def test_the_hardness_is_rounded_half_up():
    theme = await _theme_eau(_eau(durete=12.35))
    assert [c.valeur for c in theme.chiffres] == [12.4]  # `round`: 12.3


async def test_one_old_breach_among_883_samples_is_said_but_does_not_alert():
    # Paris 12e on 2026-10-30: one physico-chemical breach six months ago,
    # among 883 samples each read for both natures (once, not twice).
    lu = replace(
        _eau(bact=_conformite(883), pc=_conformite(883, ("2026-04-30",))),
        prelevements=883,
    )
    theme = await _theme_eau(lu)
    assert theme.ton == "rassurant"
    assert theme.verdict == (
        "Eau conforme au dernier contrôle ; 1 contrôle hors limites en 12 mois "
        "sur 883, le dernier le 30 avril 2026."
    )
    assert theme.details["physico_chimique"]["dates_non_conformes"] == ["2026-04-30"]
    # 882 / 883 = 99.887 %: a rate is shown rounded down, never above itself.
    assert theme.details["physico_chimique"]["taux_conformite"] == 99.8


async def test_a_breach_at_the_latest_control_alerts():
    # The latest control, older than 30 days, is still the latest one.
    theme = await _theme_eau(
        _eau(
            bact=_conformite(
                419, ("2026-09-01",), dernier="2026-09-01", dernier_conforme=False
            ),
            pc=_conformite(421, dernier="2026-08-15"),
        )
    )
    assert (theme.ton, theme.verdict) == (
        "vigilance",
        "Dernier contrôle hors limites de qualité, le 1er septembre 2026.",
    )


async def test_a_breach_of_the_last_30_days_alerts():
    theme = await _theme_eau(_eau(pc=_conformite(421, ("2026-10-10",))))
    assert (theme.ton, theme.verdict) == (
        "vigilance",
        "1 contrôle hors limites de qualité en 30 jours, le dernier le 10 octobre "
        "2026.",
    )
    ancienne = await _theme_eau(_eau(pc=_conformite(421, ("2026-09-29",))))
    assert ancienne.ton == "rassurant"


async def test_a_rate_of_conformity_under_95_percent_alerts():
    sous = await _theme_eau(_eau(bact=_conformite(100, ["2026-03-01"] * 6)))
    assert (sous.ton, sous.verdict) == (
        "vigilance",
        "Conformité bactériologique de 94 % sur 12 mois, sous le seuil de 95 %.",
    )
    au_seuil = await _theme_eau(_eau(bact=_conformite(100, ["2026-03-01"] * 5)))
    assert au_seuil.ton == "rassurant"


async def test_a_rate_just_under_the_threshold_never_reads_as_the_threshold():
    # 1 187 conforming samples of 1 250: 94.96 %, never said « 95 % ».
    theme = await _theme_eau(_eau(bact=_conformite(1250, ["2026-03-01"] * 63)))
    assert theme.verdict == (
        "Conformité bactériologique de 94,9 % sur 12 mois, sous le seuil de 95 %."
    )
    assert theme.details["bacteriologique"]["taux_conformite"] == 94.9


async def test_a_partial_page_proves_no_yearly_rate_nor_latest_control():
    # A page short of rows: its rate is not the year's, its latest sample
    # not the commune's latest control.
    taux = await _theme_eau(_eau(bact=_conformite(10, ("2026-03-01",), complet=False)))
    assert (taux.etat, taux.motif) == ("manque", "lecture_incomplete")
    # What the page read keeps names of its own, never the year's rate.
    assert taux.details["bacteriologique"] == {
        "lecture_complete": False,
        "prelevements_lus": 10,
        "non_conformes_lus": 1,
        "dates_non_conformes_lues": ["2026-03-01"],
    }
    assert taux.details["prelevements"] is None
    dernier = await _theme_eau(
        _eau(
            bact=_conformite(
                419,
                ("2026-09-01",),
                dernier="2026-09-01",
                dernier_conforme=False,
                complet=False,
            ),
            pc=_conformite(421, dernier="2026-08-15"),
        )
    )
    assert (dernier.etat, dernier.motif) == ("manque", "lecture_incomplete")
    # A breach seen in the last 30 days is a fact, whatever the page misses.
    recente = await _theme_eau(
        _eau(bact=_conformite(10, ("2026-10-10",), complet=False))
    )
    assert (recente.ton, recente.verdict) == (
        "vigilance",
        "Lecture partielle : au moins 1 contrôle hors limites de qualité en "
        "30 jours, le plus récent lu le 10 octobre 2026.",
    )


async def test_the_latest_control_is_the_latest_in_time_not_in_day():
    # A breach at 09:00, then a conforming control at 18:00 the same day.
    theme = await _theme_eau(
        _eau(
            bact=_conformite(
                419,
                ("2026-09-01",),
                dernier="2026-09-01T18:00:00Z",
                dernier_conforme=True,
            ),
            pc=_conformite(421, dernier="2026-09-01T18:00:00Z"),
        )
    )
    assert theme.ton == "rassurant"
    assert theme.verdict == (
        "Eau conforme au dernier contrôle ; 1 contrôle hors limites en 12 mois "
        "sur 421, le dernier le 1er septembre 2026."
    )


async def test_a_later_sample_without_conclusion_is_never_the_latest_control():
    # The latest concluded control is not the latest sample: it is said so.
    sans = _conformite(
        419,
        ("2026-04-30",),
        dernier="2026-09-01",
        sans_conclusion=1,
        dernier_lu="2026-10-25",
    )
    theme = await _theme_eau(_eau(bact=sans, pc=_conformite(421, dernier="2026-09-01")))
    assert theme.verdict == (
        "Eau conforme au dernier contrôle conclu ; 1 contrôle hors limites en "
        "12 mois sur 421, le dernier le 30 avril 2026."
    )
    detail = theme.details["bacteriologique"]
    assert (detail["dernier_prelevement"], detail["dernier_controle_conclu"]) == (
        "2026-10-25",
        "2026-09-01",
    )
    hors = await _theme_eau(
        _eau(
            bact=replace(sans, dernier_conforme=False),
            pc=_conformite(421, dernier="2026-08-15"),
        )
    )
    assert hors.verdict == (
        "Dernier contrôle conclu hors limites de qualité, le 1er septembre 2026."
    )


async def test_a_single_unit_is_named_several_are_not():
    seul = await _theme_eau(_eau(reseaux=[Reseau("085000450", "RESERVOIR DU FOUR")]))
    assert seul.verdict.startswith("Réseau RESERVOIR DU FOUR : eau conforme")


async def test_a_partial_list_of_units_is_a_gap_never_a_favourable():
    # Two units announced, one received: the read of the source is partial.
    lu = _eau(
        reseaux=[Reseau("085000450", "RESERVOIR DU FOUR")], reseaux_complets=False
    )
    theme = await _theme_eau(lu)
    assert (theme.etat, theme.motif) == ("manque", "lecture_incomplete")
    assert theme.details["reseaux_complets"] is False


async def test_a_breach_read_for_both_natures_is_one_breach():
    # The same sample breaks a bacteriological and a physico-chemical limit.
    ancienne = await _theme_eau(
        replace(
            _eau(
                bact=_conformite(883, ("2026-04-30",), codes=["P-1"]),
                pc=_conformite(883, ("2026-04-30",), codes=["P-1"]),
            ),
            prelevements=883,
        )
    )
    assert ancienne.verdict == (
        "Eau conforme au dernier contrôle ; 1 contrôle hors limites en 12 mois "
        "sur 883, le dernier le 30 avril 2026."
    )
    recente = await _theme_eau(
        _eau(
            bact=_conformite(419, ("2026-10-10",), codes=["P-2"]),
            pc=_conformite(421, ("2026-10-10",), codes=["P-2"]),
        )
    )
    assert recente.verdict.startswith("1 contrôle hors limites de qualité en 30 jours")


async def test_samples_without_an_ars_conclusion_never_reassure():
    # A conformity neither « C » nor « N » (absent, « S », unknown) concludes
    # nothing: no reassurance on it, and no rate computed with it.
    sans = await _theme_eau(
        _eau(
            bact=_conformite(5, sans_conclusion=5),
            pc=_conformite(5, sans_conclusion=5),
        )
    )
    assert (sans.etat, sans.ton) == ("manque", "info")
    melange = await _theme_eau(_eau(bact=_conformite(100, sans_conclusion=90)))
    assert melange.ton == "rassurant"
    assert melange.details["bacteriologique"]["sans_conclusion"] == 90
    assert melange.details["bacteriologique"]["taux_conformite"] == 100.0
    assert melange.verdict == (
        "Eau conforme aux limites de qualité : 10 contrôles bactériologiques en "
        "12 mois."
    )


@pytest.mark.parametrize(
    ("lu", "motif"),
    [
        (_eau(bact=_conformite(0), pc=_conformite(0)), "aucun_prelevement"),
        (_eau(pc=_conformite(0)), "prelevements_partiels"),
        (_eau(pc=_conformite(4, sans_conclusion=4)), "prelevements_partiels"),
        (_eau(bact=_conformite(10, complet=False)), "lecture_incomplete"),
    ],
)
async def test_without_samples_of_both_natures_on_full_pages_water_is_a_gap(lu, motif):
    theme = await _theme_eau(lu)
    assert (theme.etat, theme.motif) == ("manque", motif)


def test_a_sample_is_counted_once_and_only_a_breach_of_limits_counts():
    cle = "conformite_limites_bact_prelevement"

    def ligne(code, conformite, jour):
        return {
            "code_prelevement": code,
            cle: conformite,
            "date_prelevement": f"{jour}T09:00:00Z",
        }

    lignes = {
        "data": [
            ligne("a", "C", "2026-10-01"),
            ligne("a", "C", "2026-10-01"),
            ligne("b", "N", "2026-04-30"),
            ligne("c", "S", "2026-10-20"),
            ligne("d", None, "2026-10-25"),
        ]
    }
    lu = partage.conformite_de(lignes, cle, True)
    assert (lu.prelevements, lu.conformes, lu.sans_conclusion) == (4, 1, 2)
    assert lu.non_conformes == (("2026-04-30", "b"),)
    # The latest sample with a conclusion, apart from the latest one read.
    assert (lu.dernier, lu.dernier_conforme) == (_t("2026-10-01T09:00:00Z"), True)
    assert lu.dernier_lu == _t("2026-10-25T09:00:00Z")


def test_the_latest_control_is_ordered_by_its_time():
    cle = "conformite_limites_bact_prelevement"
    lignes = [
        {"code_prelevement": "a", cle: "N", "date_prelevement": "2026-09-01T09:00:00Z"},
        {"code_prelevement": "b", cle: "C", "date_prelevement": "2026-09-01T18:00:00Z"},
    ]
    lu = partage.conformite_de({"data": lignes}, cle, True)
    assert (lu.dernier, lu.dernier_conforme) == (_t("2026-09-01T18:00:00Z"), True)
    assert lu.dates_non_conformes == ("2026-09-01",)
    # Instants, not strings: half a second later, or the same instant written
    # with an offset, is ordered as such.
    lignes += [
        {"code_prelevement": "c", cle: "N",
         "date_prelevement": "2026-09-01T18:00:00.500Z"},
        {"code_prelevement": "d", cle: "C",
         "date_prelevement": "2026-09-01 20:00:00.250+02:00"},
    ]  # fmt: skip
    lu = partage.conformite_de({"data": lignes}, cle, True)
    assert (lu.dernier, lu.dernier_conforme) == (
        _t("2026-09-01T18:00:00.500Z"),
        False,
    )


@pytest.mark.parametrize("date_", ["", None, "1er septembre"])
async def test_a_sample_without_a_readable_date_is_an_outage_never_kept(date_):
    def repondre(requete):
        if "communes_udi" in str(requete.url):
            return httpx.Response(200, json={"count": 0, "data": []})
        ligne = {
            "code_prelevement": "P1",
            "date_prelevement": date_,
            "conformite_limites_bact_prelevement": "N",
            "conformite_limites_pc_prelevement": "C",
        }
        return httpx.Response(200, json={"count": 1, "data": [ligne]})

    with respx.mock() as mock:
        route = mock.get(url__regex=r".*/communes_udi.*").mock(side_effect=repondre)
        mock.get(url__regex=r".*/resultats_dis.*").mock(side_effect=repondre)
        for _ in range(2):
            lu = await eau_potable.THEME.amont(_amont(_contexte()))
            assert isinstance(lu, lieu.PanneAmont)
    assert route.call_count == 2  # read again, never kept


@pytest.mark.parametrize(("degres", "classe"), [(5, "très douce"), (14.9, "douce"),
                         (20, "moyennement dure"), (45.9, "très dure")])  # fmt: skip
def test_hardness_classes(degres, classe):
    assert eau_potable.durete(degres) == classe


async def test_a_partial_hubeau_page_is_never_complete_and_an_outage_never_kept():
    reponses = {
        "communes_udi": httpx.Response(200, json={"count": 0, "data": []}),
        "resultats_dis": httpx.Response(206, json={"count": 9000, "data": []}),
    }
    with respx.mock() as mock:
        mock.get(url__regex=r".*/communes_udi.*").mock(
            return_value=reponses["communes_udi"]
        )
        mock.get(url__regex=r".*/resultats_dis.*").mock(
            return_value=reponses["resultats_dis"]
        )
        lu = await eau_potable.THEME.amont(_amont(_contexte()))
    assert lu.bacteriologique.complet is False
    with respx.mock() as mock:
        route = mock.get(url__startswith=partage.HUBEAU_URL).mock(
            return_value=httpx.Response(503)
        )
        appels = []
        for _ in range(2):
            assert isinstance(
                await eau_potable.THEME.amont(_amont(_contexte())), lieu.PanneAmont
            )
            appels.append(route.call_count)
    assert appels[1] > appels[0] > 0  # the outage is read again, never kept


# --- Écoles -----------------------------------------------------------------------


def _etab(niveau, metres, uai="0440001A", **autres):
    return Etablissement(uai=uai, nom=f"{niveau} {uai}", niveau=niveau,
                         distance_m=metres, **autres)  # fmt: skip


def _ecoles(**par_niveau):
    return EcolesLues(par_niveau={n: par_niveau.get(n, []) for n in ecoles.NIVEAUX})


@pytest.fixture
def sans_secteur(monkeypatch):
    async def aucun(contexte):
        return None, False

    monkeypatch.setattr(ecoles, "college_de_secteur_lu", aucun)


def test_a_school_distance_is_written_as_the_headline_writes_it():
    assert ecoles.distance(1250) == "1,3 km"  # `round`: 1,2 km, the row said 1,3
    assert ecoles.distance(2000) == "2 km"


async def test_the_nearest_public_school_of_each_level_is_said(sans_secteur):
    lu = _ecoles(
        elementaire=[_etab("elementaire", 431, ips=124.4)],
        college=[_etab("college", 558, uai="0440010B")],
        lycee=[_etab("lycee", 1890, uai="0440022K")],
    )
    theme = await ecoles.lire(_contexte(amonts={"ecoles": lu}))
    assert (theme.etat, theme.ton, theme.granularite.rayon_m) == (
        "ok",
        "neutre",
        10_000,
    )
    assert theme.verdict == (
        "Publics les plus proches : école élémentaire à 431 m, collège à 558 m, "
        "lycée général à 1,9 km."
    )
    assert theme.details["elementaire"]["ips"] == 124.4


async def test_a_missing_level_is_said_and_the_verdict_stays_bounded(sans_secteur):
    lu = _ecoles(elementaire=[_etab("elementaire", 564)])
    theme = await ecoles.lire(_contexte(amonts={"ecoles": lu}))
    assert theme.verdict == (
        "Publics les plus proches : école élémentaire à 564 m, collège : aucun à "
        "10 km, lycée général : aucun à 10 km."
    )
    assert len(theme.verdict) <= 140
    rien = await ecoles.lire(_contexte(amonts={"ecoles": _ecoles()}))
    assert (rien.etat, rien.motif) == ("manque", "aucune_ecole_publique")


async def test_the_sector_college_is_named_when_it_is_among_the_nearest(monkeypatch):
    async def secteur(contexte):
        return "0440020H", False

    monkeypatch.setattr(ecoles, "college_de_secteur_lu", secteur)
    lu = _ecoles(
        college=[
            _etab("college", 558, uai="0440010B"),
            _etab("college", 1411, uai="0440020H", ips=139.3),
        ]
    )
    theme = await ecoles.lire(_contexte(amonts={"ecoles": lu}))
    assert theme.details["college_de_secteur"]["parmi_les_plus_proches"] is True
    assert theme.details["college_de_secteur"]["distance_m"] == 1411
    lu = _ecoles(college=[_etab("college", 558, uai="0440010B")])
    theme = await ecoles.lire(_contexte(amonts={"ecoles": lu}))
    assert theme.details["college_de_secteur"] == {
        "uai": "0440020H",
        "parmi_les_plus_proches": False,
    }


async def test_the_sector_college_needs_one_college_named_by_the_school_map(
    monkeypatch,
):
    reponses = []
    panne = []

    async def faux_bloc(session, sources, lecteur):
        if panne:
            return SimpleNamespace(en_panne=True, valeur=None)
        return SimpleNamespace(en_panne=False, valeur=await lecteur())

    async def faux_secteur(session, code_insee, nom_voie, numero):
        assert (code_insee, nom_voie, numero) == ("44109", "Rue des Essais", 2)
        return reponses

    monkeypatch.setattr(ecoles, "lire_bloc", faux_bloc)
    monkeypatch.setattr(ecoles, "colleges_du_secteur", faux_secteur)
    reponses[:] = ["0440020H"]
    assert await ecoles.college_de_secteur_lu(_contexte()) == ("0440020H", False)
    reponses[:] = ["0440020H", "0440030J"]
    assert await ecoles.college_de_secteur_lu(_contexte()) == (None, False)
    hors = _contexte(hors=("scolaire",))
    assert await ecoles.college_de_secteur_lu(hors) == (None, False)
    panne.append(True)
    assert await ecoles.college_de_secteur_lu(_contexte()) == (None, True)


async def test_an_outage_of_the_school_map_is_said_and_keeps_the_report_out_of_cache(
    monkeypatch,
):
    async def en_panne(contexte):
        return None, True

    monkeypatch.setattr(ecoles, "college_de_secteur_lu", en_panne)
    lu = _ecoles(college=[_etab("college", 558, uai="0440010B")])
    theme = await ecoles.lire(_contexte(amonts={"ecoles": lu}))
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert theme.pannes_partielles == ["scolaire"]
    assert theme.details["college_de_secteur"] is None


async def test_an_outage_of_the_directory_is_an_outage(sans_secteur):
    theme = await ecoles.lire(_contexte(amonts={"ecoles": lieu.PanneAmont()}))
    assert theme.etat == "panne"


def test_schools_are_sorted_by_distance_and_unplaced_ones_left_out():
    donnees = {
        "results": [
            {"identifiant_de_l_etablissement": "B", "latitude": POINT.lat + 0.009,
             "longitude": POINT.lon},
            {"identifiant_de_l_etablissement": "A", "latitude": POINT.lat + 0.001,
             "longitude": POINT.lon},
            {"identifiant_de_l_etablissement": "C", "latitude": None,
             "longitude": None},
        ]
    }  # fmt: skip
    lus = partage.etablissements_de(donnees, "college", POINT)
    assert [(e.uai, e.distance_m) for e in lus] == [("A", 111), ("B", 1001)]


async def test_the_directory_query_asks_open_public_colleges_by_distance():
    vides = httpx.Response(200, json={"results": []})
    with respx.mock() as mock:
        route = mock.get(url__startswith="https://data.education.gouv.fr").mock(
            return_value=vides
        )
        lu = await ecoles.THEME.amont(_amont(_contexte()))
    ou = [parse_qs(urlsplit(str(c.request.url)).query)["where"][0] for c in route.calls]
    assert any("code_nature='340'" in w for w in ou)
    assert all("statut_public_prive='Public'" in w for w in ou)
    assert all("etat='OUVERT'" in w for w in ou)
    assert all(lu.par_niveau[n] == [] for n in ecoles.NIVEAUX)


async def test_ips_and_added_value_come_from_the_latest_year_of_each_set():
    # Real rows of 2026-10-31: collège Sophie-Germain, lycées Clemenceau
    # (LEGT) and Les Bourdonnières (LPO), in Nantes. The sets « à partir de
    # 2023 » serve up to 2025-2026; those « 2022 » stop at 2022-2023.
    def repondre(requete):
        jeu = str(requete.url).split("/datasets/")[1].split("/")[0]
        lignes = {
            "fr-en-ips-colleges-ap2023": [
                {"uai": "0440536U", "rentree_scolaire": "2024-2025", "ips": 93.6,
                 "ips_national_public": 100.9},
                {"uai": "0440536U", "rentree_scolaire": "2025-2026", "ips": 92.1,
                 "ips_national_public": 100.8},
                {"uai": "0440536U", "rentree_scolaire": "2023-2024", "ips": 92.4,
                 "ips_national_public": 100.9},
            ],
            "fr-en-indicateurs-valeur-ajoutee-colleges": [
                {"uai": "0440536U", "session": "2025", "taux_de_reussite_g": 73.0,
                 "va_du_taux_de_reussite_g": -9.0},
            ],
            "fr-en-ips-lycees-ap2023": [
                {"uai": "0440021J", "rentree_scolaire": "2025-2026",
                 "type_de_lycee": "LEGT", "ips_voie_gt": "136.1",
                 "ips_national_legt_public": 115.2,
                 "ips_national_lpo_public": 102.0},
                {"uai": "0441552Y", "rentree_scolaire": "2025-2026",
                 "type_de_lycee": "LPO", "ips_voie_gt": "117.9",
                 "ips_national_legt_public": 115.2,
                 "ips_national_lpo_public": 102.0},
            ],
            "fr-en-indicateurs-de-resultat-des-lycees-gt_v2": [
                {"uai": "0440021J", "annee": "2025", "taux_reu_total": 98.0,
                 "va_reu_total": -1.0},
            ],
        }.get(jeu, [])  # fmt: skip
        return httpx.Response(200, json={"results": lignes})

    with respx.mock() as mock:
        mock.get(url__startswith="https://data.education.gouv.fr").mock(
            side_effect=repondre
        )
        profils = await partage._profils(
            {
                "college": [_etab("college", 558, uai="0440536U")],
                "lycee": [
                    _etab("lycee", 189, uai="0440021J"),
                    _etab("lycee", 2400, uai="0441552Y"),
                ],
            }
        )
    college, lycee = profils["college"][0], profils["lycee"][0]
    assert (
        college.ips,
        college.ips_rentree,
        college.ips_national_public,
        college.valeur_ajoutee,
    ) == (92.1, "2025-2026", 100.8, -9.0)
    # A lycée is set against the public lycées of its own type (LEGT here).
    assert (lycee.ips, lycee.ips_national_public, lycee.ips_rentree) == (
        136.1,
        115.2,
        "2025-2026",
    )
    assert (lycee.taux_reussite, lycee.session) == (98.0, "2025")
    polyvalent = profils["lycee"][1]
    assert (polyvalent.ips, polyvalent.ips_national_public) == (117.9, 102.0)


# IPS sets of data.education.gouv.fr that no longer receive a rentrée
# (catalogue of 2026-10-31: titles « (2016-2021) » and « (2022) »).
IPS_FIGES = {
    "fr-en-ips_ecoles_v2",
    "fr-en-ips_colleges",
    "fr-en-ips_lycees",
    "fr-en-ips-colleges-ap2022",
    "fr-en-ips-lycees-ap2022",
}


async def test_no_frozen_ips_set_is_read():
    # A set that stopped at an old rentrée would serve an old IPS as the
    # latest one: every level reads a set that still receives rentrées.
    with respx.mock() as mock:
        route = mock.get(url__startswith="https://data.education.gouv.fr").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        await partage._profils(
            {
                n: [_etab(n, 100, uai=f"044000{i}A")]
                for i, n in enumerate(ecoles.NIVEAUX)
            }
        )
    jeux = {
        str(c.request.url).split("/datasets/")[1].split("/")[0] for c in route.calls
    }
    ips = {j for j in jeux if j.startswith("fr-en-ips")}
    assert len(ips) == 3, ips
    assert not ips & IPS_FIGES, ips & IPS_FIGES


async def test_a_school_without_added_value_has_no_session():
    with respx.mock() as mock:
        mock.get(url__startswith="https://data.education.gouv.fr").mock(
            return_value=httpx.Response(200, json={"results": []})
        )
        profils = await partage._profils(
            {
                "college": [_etab("college", 558, uai="0440010B")],
                "lycee": [_etab("lycee", 189, uai="0440022K")],
            }
        )
    assert profils["college"][0].session is None
    assert profils["lycee"][0].session is None


async def test_two_addresses_of_one_cache_key_keep_their_own_distance():
    # Both points round to (-1.55, 47.21); the school is 500 m from the
    # first, 501 m from the second.
    ecole = {"identifiant_de_l_etablissement": "0440001A", "latitude": 47.2145,
             "longitude": -1.55}  # fmt: skip

    def repondre(requete):
        if "fr-en-annuaire-education" in str(requete.url):
            return httpx.Response(200, json={"results": [ecole]})
        return httpx.Response(200, json={"results": []})

    distances = []
    with respx.mock() as mock:
        mock.get(url__startswith="https://data.education.gouv.fr").mock(
            side_effect=repondre
        )
        for lat in (47.210004, 47.209996):
            contexte = _contexte(point=replace(POINT, lat=lat))
            lu = await ecoles.THEME.amont(_amont(contexte))
            distances.append(lu.par_niveau["elementaire"][0].distance_m)
    assert distances == [500, 501]


async def test_the_profiles_of_the_same_schools_are_read_once():
    # Two colleges, nearest first from one point, last from the other.
    colleges = [
        {"identifiant_de_l_etablissement": "0440010B", "latitude": 47.215,
         "longitude": -1.55},
        {"identifiant_de_l_etablissement": "0440020H", "latitude": 47.205,
         "longitude": -1.55},
    ]  # fmt: skip

    def repondre(requete):
        if "fr-en-annuaire-education" in str(requete.url):
            return httpx.Response(200, json={"results": colleges})
        return httpx.Response(200, json={"results": []})

    ordres = []
    with respx.mock() as mock:
        route = mock.get(url__startswith="https://data.education.gouv.fr").mock(
            side_effect=repondre
        )
        for lat in (47.212, 47.208):
            contexte = _contexte(point=replace(POINT, lat=lat))
            lu = await ecoles.THEME.amont(_amont(contexte))
            ordres.append([e.uai for e in lu.par_niveau["college"]])
    assert ordres == [["0440010B", "0440020H"], ["0440020H", "0440010B"]]
    ips = [c for c in route.calls if "fr-en-ips-colleges" in str(c.request.url)]
    assert len(ips) == 1


# --- Couverture et Données & méthode ------------------------------------------------


@pytest.mark.parametrize(
    ("module", "cle"),
    [
        (delinquance, "delinquance"),
        (eau_potable, "eau_potable"),
        (ecoles, "annuaire_education"),
    ],
)
async def test_until_the_scope_hosts_them_they_are_out_of_coverage_without_call(
    module, cle
):
    contexte = _contexte(hors=(cle,))
    with respx.mock(assert_all_called=False) as mock:
        assert await module.THEME.amont(_amont(contexte)) is None
    assert mock.calls.call_count == 0
    theme = await module.lire(contexte)
    assert (theme.etat, theme.ton) == ("hors_couverture", "info")


async def test_the_datasets_page_serves_the_three_sources_with_their_licence(client):
    from unittest.mock import AsyncMock, patch

    from app.common.source_catalog import DATASET_CATALOG

    with patch("app.datasets.router.get_all_datasets", new=AsyncMock(return_value=[])):
        response = await client.get("/v1/datasets")
    assert response.status_code == 200, response.text
    servis = {jeu["nom"]: jeu for jeu in response.json()["donnees"]}
    for cle in ("delinquance", "eau_potable", "annuaire_education"):
        entree = DATASET_CATALOG[cle]
        assert servis[cle]["reutilisation"]["licence"] == entree.licence
        assert servis[cle]["provenance"]["producteur"] == entree.producteur


async def test_a_stalled_hubeau_call_is_tried_once_more():
    with respx.mock() as mock:
        route = mock.get(url__regex=r".*/communes_udi.*").mock(
            side_effect=[
                httpx.ReadTimeout("calé"),
                httpx.Response(200, json={"count": 0, "data": []}),
            ]
        )
        donnees, complet = await partage._get_hubeau("communes_udi", {})
    assert route.call_count == 2
    assert (donnees["data"], complet) == ([], True)


async def test_a_sample_read_for_both_natures_is_counted_once():
    def repondre(requete):
        if "communes_udi" in str(requete.url):
            return httpx.Response(200, json={"count": 0, "data": []})
        ligne = {
            "code_prelevement": "P1",
            "date_prelevement": "2026-10-01T09:00:00Z",
            "conformite_limites_bact_prelevement": "C",
            "conformite_limites_pc_prelevement": "C",
        }
        return httpx.Response(200, json={"count": 1, "data": [ligne]})

    with respx.mock() as mock:
        mock.get(url__startswith=partage.HUBEAU_URL).mock(side_effect=repondre)
        lu = await eau_potable.THEME.amont(_amont(_contexte()))
    assert lu.bacteriologique.prelevements == lu.physico_chimique.prelevements == 1
    assert lu.prelevements == 1


async def test_a_partial_list_of_units_is_said_and_never_kept():
    udi = {"annee": "2026", "code_reseau": "085000450", "nom_reseau": "FOUR"}

    def repondre(requete):
        if "communes_udi" in str(requete.url):
            return httpx.Response(200, json={"count": 2, "data": [udi]})
        return httpx.Response(200, json={"count": 0, "data": []})

    with respx.mock() as mock:
        route = mock.get(url__regex=r".*/communes_udi.*").mock(side_effect=repondre)
        mock.get(url__regex=r".*/resultats_dis.*").mock(side_effect=repondre)
        lu = await eau_potable.THEME.amont(_amont(_contexte()))
        await eau_potable.THEME.amont(_amont(_contexte()))
    assert lu.reseaux_complets is False
    assert route.call_count == 2  # read again, never kept for a day
