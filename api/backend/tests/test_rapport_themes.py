"""Themes of the address report, one tone rule each, without a database.

Each theme reuses a reader of the address page or a dataset query; here the
reader is replaced by its answer, and the test checks the state, the tone
(the written rule of the theme), the scale and the attachment. An outage is
never a zero; a fact of the commune keeps the scale ``commune``; the DPE of
several buildings says so and lowers the confidence.
"""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import ProgrammingError

from app.adresse import lieu
from app.adresse.blocs import Bloc
from app.adresse.relations import PointBan
from app.adresse.risques import RisquesCommune
from app.adresse.schemas import (
    CommuneRef,
    DepartementRef,
    ElementLieu,
    GroupeTheme,
    IdentiteAdresse,
    RelationParcelle,
    RepereReferenceCommune,
    RepereVentes,
    SelectionPlusRecent,
    ThemeLieu,
    ValeurLieu,
    ValeurTheme,
)
from app.common.proxy_coverage import ProxySource
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.connectivite.queries import EligibiliteAdresse
from app.datasets.provenance import ProvenanceFacts
from app.dpe.queries import DpeInventoryAggregate
from app.rapport import partage, socle
from app.rapport.partage import ErrialLu
from app.rapport.schemas import Theme
from app.rapport.socle import ContexteRapport, Memo, PanneAmont
from app.rapport.themes import (
    antennes_5g,
    argiles,
    batiment,
    construction,
    copropriete,
    dpe,
    equipements,
    fibre,
    fiscalite,
    icpe_commune,
    loyers,
    parcelle,
    pollution_sols,
    prix_commune,
    radon,
    revenus,
    risques_commune,
    transports,
    urbanisme,
    ventes_parcelle,
    zonage_abc,
)
from app.rapport.themes.construction import BdnbLu
from app.scope.dependencies import RouteHosting
from app.scope.model import InstanceScope

pytestmark = pytest.mark.unit


async def _acted(_session, _tables):
    return True


@pytest.fixture(autouse=True)
def _publication_acted(monkeypatch):
    """The fake sessions answer the theme reads only: the publication guard of
    the imported tables is proven on PostgreSQL (``test_bdnb_pipeline.py``,
    ``test_rnb_liens_elargissement.py``)."""
    from app.adresse import blocs

    monkeypatch.setattr(blocs, "publication_actee", _acted)


AS_OF = date(2026, 10, 30)
BAN_ID = "44109_zz04_00002"
PARCELLE = "44109000LB0369"
HEBERGE = DatasetHosting(etat="heberge", departements=["44", "85"])
HORS = DatasetHosting(
    etat="hors_couverture", motif=OutOfCoverageReason.DATASET_NON_HEBERGE
)
UNIQUE = RelationParcelle(
    etat="unique",
    id_parcelle=PARCELLE,
    nombre_candidats=1,
    rattachement="parcelle_du_point",
)
POINT = PointBan(lon=-1.55, lat=47.21)
AMBIGUE = RelationParcelle(
    etat="ambigue", nombre_candidats=2, motif="parcelles_multiples"
)
CLES = (
    "altimetrie",
    "axes_transport",
    "delinquance",
    "eau_potable",
    "annuaire_education",
    "qualite_air",
    "filosofi_carreaux",
    "iris",
    "zone_tendue",
    "encadrement_communes",
    "peb",
    "geologie",
    "ban",
    "cadastre",
    "commune",
    "dvf",
    "urbanisme",
    "loyers",
    "zonage_abc",
    "fiscalite",
    "georisques",
    "errial",
    "dpe",
    "rnb",
    "batiments",
    "bdnb",
    "copropriete",
    "connectivite",
    "transports",
    "gares",
    "equipements",
    "antennes",
    "revenus",
    "scolaire",
)


class _Session:
    @asynccontextmanager
    async def begin_nested(self):
        yield


def _contexte(
    *,
    relation=UNIQUE,
    point=POINT,
    hors=(),
    amonts=None,
) -> ContexteRapport:
    return ContexteRapport(
        session=_Session(),  # type: ignore[arg-type]
        identite=IdentiteAdresse(
            ban_id=BAN_ID,
            libelle="2 Rue des Essais, 44000 Nantes",
            numero="2",
            nom_voie="Rue des Essais",
            code_postal="44000",
            commune=CommuneRef(code_insee="44109", nom="Nantes"),
            departement=DepartementRef(code="44", nom="Loire-Atlantique"),
            parcelle=relation,
        ),
        point=point,
        hosting=RouteHosting({k: HORS if k in hors else HEBERGE for k in CLES}),
        scope=InstanceScope(datasets=frozenset(CLES), departements=("44", "85")),
        as_of=AS_OF,
        memo=Memo(),
        amonts=amonts or {},
    )


def _valeur(etat="value", **champs) -> ValeurLieu:
    return ValeurLieu(field_id="f", etat=etat, **champs)


def _champ(field_id, etat="value", **champs) -> ValeurTheme:
    return ValeurTheme(field_id=field_id, etat=etat, **champs)


def _lieu(cle, champs, etat="value", motif=None) -> ThemeLieu:
    groupes = [GroupeTheme(cle="lieu", champs=champs)] if champs else []
    return ThemeLieu(cle=cle, titre="T", etat=etat, motif=motif, groupes=groupes)


def _bloc_fixe(monkeypatch, module, par_source: dict):
    async def faux(session, sources, lecteur):
        valeur = par_source[sources[0]]
        if isinstance(valeur, str):
            return Bloc(panne=valeur)
        return Bloc(valeur=valeur)

    monkeypatch.setattr(module, "lire_bloc", faux)


@pytest.fixture(autouse=True)
def _publication_validee(monkeypatch):
    """Every journaled source is published, unless a test refuses one."""
    from app.adresse import blocs

    async def validee(session, datasets):
        return None

    monkeypatch.setattr(blocs, "guard_publication", validee)


@pytest.fixture(autouse=True)
def _sans_provenance(monkeypatch):
    async def aucune(session, cle):
        return None

    for module in (socle, copropriete, equipements, antennes_5g):
        monkeypatch.setattr(module, "faits_de_provenance", aucune)


# --- Prix et cadastre ----------------------------------------------------------


async def test_prix_commune_is_a_neutral_fact_of_the_commune(monkeypatch):
    async def repere(*args):
        return RepereReferenceCommune(
            maisons=_valeur(valeur=2450.0, periode="2023-07/2025-06"),
            appartements=_valeur("insufficient_sample", motif="effectif_insuffisant"),
        )

    monkeypatch.setattr(prix_commune, "repere_reference_commune", repere)
    theme = await prix_commune.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "commune",
    )
    assert [c.id for c in theme.chiffres] == ["maison"]
    assert theme.chiffres[0].millesime == "2023-07/2025-06"
    assert "2\u202f450 €/m² pour une maison" in theme.verdict
    assert theme.details["absences"] == {"appartement": "effectif_insuffisant"}


async def test_a_median_price_is_written_as_the_commune_fiche_writes_it(monkeypatch):
    async def repere(*args):
        return RepereReferenceCommune(
            maisons=_valeur(valeur=3384.5, periode="2023-07/2025-06"),
            appartements=_valeur(valeur=2449.5, periode="2023-07/2025-06"),
        )

    monkeypatch.setattr(prix_commune, "repere_reference_commune", repere)
    theme = await prix_commune.lire(_contexte())
    assert [c.valeur for c in theme.chiffres] == [3385, 2450]
    assert theme.verdict == (
        f"Prix médian dans la commune : 3{chr(0x202F)}385 €/m² pour une maison, "
        f"2{chr(0x202F)}450 €/m² pour un appartement."
    )


async def test_prix_commune_in_outage_is_never_a_zero(monkeypatch):
    async def repere(*args):
        panne = _valeur("temporary_error", motif="lecture_indisponible")
        return RepereReferenceCommune(maisons=panne, appartements=panne)

    monkeypatch.setattr(prix_commune, "repere_reference_commune", repere)
    theme = await prix_commune.lire(_contexte())
    assert (theme.etat, theme.ton, theme.chiffres) == ("panne", "info", [])


async def test_ventes_parcelle_counts_sales_without_any_price(monkeypatch):
    async def repere(session, relation, hosting):
        return RepereVentes(
            nombre=_valeur(valeur=3, periode="2020-01/2025-06"),
            derniere=_valeur(valeur="2023-03-14"),
        )

    monkeypatch.setattr(ventes_parcelle, "repere_ventes", repere)
    theme = await ventes_parcelle.lire(_contexte())
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert theme.verdict == (
        "3 ventes enregistrées sur la parcelle de l'adresse, la dernière en mars 2023."
    )
    assert theme.rattachement.cle == PARCELLE and theme.rattachement.confiance == "sure"
    assert not any("prix" in c.id or c.unite.startswith("euro") for c in theme.chiffres)


async def test_ventes_parcelle_zero_and_several_parcels(monkeypatch):
    async def zero(session, relation, hosting):
        return RepereVentes(nombre=_valeur(valeur=0), derniere=_valeur("not_available"))

    monkeypatch.setattr(ventes_parcelle, "repere_ventes", zero)
    theme = await ventes_parcelle.lire(_contexte())
    assert (theme.etat, theme.ton) == ("zero", "neutre")
    ambigu = await ventes_parcelle.lire(_contexte(relation=AMBIGUE))
    assert (ambigu.etat, ambigu.motif, ambigu.rattachement.candidats) == (
        "manque",
        "parcelles_multiples",
        2,
    )
    hors = await ventes_parcelle.lire(_contexte(hors=("dvf",)))
    assert hors.etat == "hors_couverture"


def _batiment_parcelle(monkeypatch, champs, etat="value"):
    async def lu(session, contexte):
        return _lieu("batiment_parcelle", champs, etat=etat)

    monkeypatch.setattr(lieu, "theme_batiment_parcelle", lu)


async def test_parcelle_serves_its_area_and_its_key(monkeypatch):
    _batiment_parcelle(
        monkeypatch, [_champ(lieu.FIELD_CONTENANCE, valeur=512, periode="2025-01")]
    )
    theme = await parcelle.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "parcelle",
    )
    # The area is the one of the parcel of the point, never of the whole plot.
    assert theme.verdict == (
        "Le point de l'adresse est sur une parcelle cadastrale de 512 m² ; "
        "un terrain peut en réunir plusieurs."
    )
    assert theme.rattachement.cle == PARCELLE
    assert theme.sources[0].millesime == "2025-01"


async def test_parcelle_on_several_parcels_attributes_nothing(monkeypatch):
    _batiment_parcelle(monkeypatch, [], etat="not_available")
    theme = await parcelle.lire(_contexte(relation=AMBIGUE))
    assert theme.etat == "manque" and "plusieurs parcelles" in theme.verdict
    assert theme.rattachement.confiance == "incertaine"


def _gpu(zones, prescriptions, statut="disponible"):
    def feature(libelle):
        return {"properties": {"libelle": libelle}}

    return {
        "zones_plu": [feature(z) for z in zones],
        "prescriptions": [feature(p) for p in prescriptions],
        "sources": {
            "zones_plu": {"statut": statut, "complet": True},
            "prescriptions": {"statut": statut, "complet": True},
        },
    }


@pytest.mark.parametrize(
    ("prescriptions", "ton"), [(["Élément de patrimoine"], "vigilance"), ([], "neutre")]
)
async def test_urbanisme_tone_rule(prescriptions, ton):
    theme = await urbanisme.lire(
        _contexte(amonts={"urbanisme": _gpu(["UA"], prescriptions)})
    )
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "adresse")
    assert theme.verdict.startswith("Zone UA au point de l'adresse")


async def test_prescriptions_down_without_zone_is_still_said():
    gpu = _gpu([], [])
    gpu["sources"]["prescriptions"] = {"statut": "indisponible"}
    theme = await urbanisme.lire(_contexte(amonts={"urbanisme": gpu}))
    assert theme.etat == "manque"
    assert theme.pannes_partielles == ["prescriptions"]


async def test_prescriptions_down_is_a_partial_outage():
    gpu = _gpu(["UA"], [])
    gpu["sources"]["prescriptions"] = {"statut": "indisponible"}
    theme = await urbanisme.lire(_contexte(amonts={"urbanisme": gpu}))
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert theme.pannes_partielles == ["prescriptions"]
    assert theme.verdict.endswith("prescriptions non lues.")


async def test_urbanisme_without_document_is_a_gap_and_outage_is_an_outage():
    vide = await urbanisme.lire(_contexte(amonts={"urbanisme": _gpu([], [])}))
    assert (vide.etat, vide.motif) == ("manque", "aucun_document_publie")
    panne = await urbanisme.lire(_contexte(amonts={"urbanisme": PanneAmont()}))
    assert (panne.etat, panne.ton) == ("panne", "info")


def _requete(lignes):
    async def requete(session, codes):
        return lignes

    return requete


async def test_loyers_neutral_fact_of_the_commune(monkeypatch):
    monkeypatch.setattr(
        loyers,
        "loyers_par_codes",
        _requete(
            [
                {
                    "code_insee": "44109",
                    "type_habitat": "appartement",
                    "loyer_m2": 13.42,
                    "annee": 2025,
                },
                {
                    "code_insee": "44109",
                    "type_habitat": "maison",
                    "loyer_m2": 11.0,
                    "annee": 2025,
                },
            ]
        ),
    )
    theme = await loyers.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "commune",
    )
    assert [(c.id, c.valeur) for c in theme.chiffres] == [
        ("appartement", 13.4),
        ("maison", 11.0),
    ]
    assert theme.details["mention"].startswith("Estimations ANIL")


@pytest.mark.parametrize(
    ("brut", "valeur", "ecrit"),
    [
        # NUMERIC(6, 2): 14.35 of the commune fiche, 14,4 under its contract.
        # `round(float)` gave 14.3 on the address and 14,4 on the commune.
        (Decimal("14.35"), 14.4, "14,4"),
        (Decimal("14.45"), 14.5, "14,5"),
        (Decimal("14.34"), 14.3, "14,3"),
        (Decimal("11.00"), 11.0, "11"),
        (14.35, 14.4, "14,4"),
    ],
)
async def test_a_rent_has_one_rounding_rule(monkeypatch, brut, valeur, ecrit):
    ligne = {
        "code_insee": "44109",
        "type_habitat": "maison",
        "loyer_m2": brut,
        "annee": 2025,
    }
    monkeypatch.setattr(loyers, "loyers_par_codes", _requete([ligne]))
    theme = await loyers.lire(_contexte())
    assert [c.valeur for c in theme.chiffres] == [valeur]
    assert f"{ecrit} €/m² pour une maison" in theme.verdict


async def test_a_rent_estimated_on_a_wider_area_says_so(monkeypatch):
    ligne = {
        "code_insee": "44109",
        "type_habitat": "maison",
        "loyer_m2": 9.0,
        "annee": 2025,
        "type_prediction": "epci",
        "id_zone": "Z44",
    }
    monkeypatch.setattr(loyers, "loyers_par_codes", _requete([ligne]))
    theme = await loyers.lire(_contexte())
    assert "à partir d'un territoire plus large" in theme.verdict
    assert theme.details["types"]["maison"]["id_zone"] == "Z44"


async def test_loyers_absent_is_a_gap_and_outage_is_an_outage(monkeypatch):
    monkeypatch.setattr(loyers, "loyers_par_codes", _requete([]))
    assert (await loyers.lire(_contexte())).etat == "manque"

    async def panne(session, codes):
        raise RuntimeError

    async def bloc_en_panne(session, sources, lecteur):
        return Bloc(panne="lecture_indisponible")

    monkeypatch.setattr(socle, "lire_bloc", bloc_en_panne)
    theme = await loyers.lire(_contexte())
    assert (theme.etat, theme.motif) == ("panne", "lecture_indisponible")
    assert (await loyers.lire(_contexte(hors=("loyers",)))).etat == "hors_couverture"


async def test_an_arrondissement_falls_back_to_its_commune(monkeypatch):
    lignes = [{"code_insee": "75056", "zone": "Abis", "date_arrete": "2023-07-05"}]
    monkeypatch.setattr(zonage_abc, "zonage_par_codes", _requete(lignes))
    contexte = _contexte()
    contexte = contexte.__class__(
        **{
            **contexte.__dict__,
            "identite": contexte.identite.model_copy(
                update={"commune": CommuneRef(code_insee="75107", nom="Paris 7e")}
            ),
        }
    )
    theme = await zonage_abc.lire(contexte)
    assert theme.details["territoire"] == "75056"
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "commune",
    )


async def test_a_tax_rate_has_one_writing_on_its_row(monkeypatch):
    # Nantes served « 53,4 % » in the verdict and « 53,37 % » as the headline.
    ligne = {"code_insee": "44109", "taux_tfpb": 53.37, "annee": 2025}
    monkeypatch.setattr(fiscalite, "fiscalite_par_codes", _requete([ligne]))
    theme = await fiscalite.lire(_contexte())
    assert theme.chiffres[0].valeur == 53.37
    assert theme.verdict.endswith(" : 53,37 % en 2025.")


async def test_a_tax_rate_is_rounded_as_the_commune_fiche_rounds_it(monkeypatch):
    ligne = {"code_insee": "44109", "taux_tfpb": 39.125, "annee": 2025}
    monkeypatch.setattr(fiscalite, "fiscalite_par_codes", _requete([ligne]))
    theme = await fiscalite.lire(_contexte())
    assert theme.chiffres[0].valeur == 39.13  # `round`: 39.12
    assert theme.verdict.endswith(" : 39,13 % en 2025.")


async def test_fiscalite_and_revenus_are_neutral_facts_of_the_commune(monkeypatch):
    monkeypatch.setattr(
        fiscalite,
        "fiscalite_par_codes",
        _requete([{"code_insee": "44109", "taux_tfpb": 46.39, "annee": 2024}]),
    )
    monkeypatch.setattr(
        revenus,
        "revenus_par_codes",
        _requete([{"code_insee": "44109", "revenu_median": 24310, "annee": 2021}]),
    )
    # The commune alone: no cell holds the point (04.7-07).
    sans_carreau = {
        "revenus": partage.RevenusAmont(
            carreau=partage.CarreauLu(carreau=None, motif="aucun_carreau"),
            iris=None,
        )
    }
    for module in (fiscalite, revenus):
        theme = await module.lire(_contexte(amonts=dict(sans_carreau)))
        assert (theme.etat, theme.ton, theme.granularite.niveau) == (
            "ok",
            "neutre",
            "commune",
        )
    monkeypatch.setattr(
        revenus,
        "revenus_par_codes",
        _requete([{"code_insee": "44109", "revenu_median": None, "annee": 2021}]),
    )
    assert (await revenus.lire(_contexte(amonts=dict(sans_carreau)))).etat == "manque"


async def test_a_refused_publication_is_the_outage_of_its_theme(monkeypatch):
    from fastapi import HTTPException

    from app.adresse import blocs

    gardees: list[list[str]] = []

    async def refus(session, datasets):
        gardees.append(list(datasets))
        raise HTTPException(503, "refus")

    monkeypatch.setattr(blocs, "guard_publication", refus)
    for module, requete in (
        (loyers, "loyers_par_codes"),
        (zonage_abc, "zonage_par_codes"),
        (fiscalite, "fiscalite_par_codes"),
        (revenus, "revenus_par_codes"),
    ):
        monkeypatch.setattr(module, requete, _requete([{"code_insee": "44109"}]))
        theme = await module.lire(_contexte())
        assert (theme.etat, theme.motif) == ("panne", "publication_non_validee")
    theme = await equipements.lire(_contexte())
    assert (theme.etat, theme.motif) == ("panne", "publication_non_validee")
    assert gardees == [
        ["loyers"],
        ["zonage_abc"],
        ["fiscalite"],
        ["revenus"],
        ["equipements"],
    ]


# --- Risques -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exposition", "ton"),
    [
        ("Exposition forte", "vigilance"),
        ("Exposition moyenne", "vigilance"),
        ("Faible", "neutre"),
        ("Exposition nulle", "rassurant"),
        ("Pas d'exposition", "rassurant"),
    ],
)
async def test_argiles_tone_rule(exposition, ton):
    theme = await argiles.lire(
        _contexte(amonts={"argiles": _valeur(valeur=exposition, periode="2026-10-30")})
    )
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "adresse")
    assert theme.sources[0].mode == "direct"


async def test_argiles_states():
    manque = await argiles.lire(_contexte(amonts={"argiles": _valeur("not_reported")}))
    assert manque.etat == "manque"
    panne = await argiles.lire(
        _contexte(
            amonts={"argiles": _valeur("temporary_error", motif="amont_indisponible")}
        )
    )
    assert (panne.etat, panne.ton) == ("panne", "info")
    plantage = await argiles.lire(_contexte(amonts={"argiles": PanneAmont()}))
    assert plantage.etat == "panne"


def _risques_commune(gaspar=None, radon_=None, icpe=None) -> RisquesCommune:
    complet = ProxySource(statut="disponible", complet=True, pages_lues=1)
    return RisquesCommune(
        lignes={"gaspar": gaspar, "radon": radon_, "icpe": icpe},
        couverture={"gaspar": complet, "radon": complet, "icpe": complet},
        total={
            "gaspar": None,
            "radon": None,
            "icpe": len(icpe) if icpe is not None else None,
        },
    )


async def test_risques_commune_counts_at_the_scale_of_the_commune():
    lu = _risques_commune(
        gaspar=[
            {
                "risques_detail": [
                    {"libelle_risque_long": "Inondation"},
                    {"libelle_risque_long": "Séisme"},
                ]
            }
        ]
    )
    theme = await risques_commune.lire(_contexte(amonts={"risques_commune": lu}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "vigilance",
        "commune",
    )
    assert theme.verdict == "Risques recensés sur la commune : inondation, séisme."
    vide = await risques_commune.lire(
        _contexte(amonts={"risques_commune": _risques_commune(gaspar=[])})
    )
    assert vide.etat == "manque"


@pytest.mark.parametrize(
    ("classe", "ton"), [("3", "vigilance"), ("2", "neutre"), ("1", "rassurant")]
)
async def test_radon_tone_rule(classe, ton):
    lu = _risques_commune(radon_=[{"classe_potentiel": classe}])
    theme = await radon.lire(_contexte(amonts={"radon": lu}))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "commune")


async def test_icpe_commune_is_neutral_and_zero_is_a_zero():
    lu = _risques_commune(icpe=[{"x": 1}, {"x": 2}])
    theme = await icpe_commune.lire(_contexte(amonts={"icpe_commune": lu}))
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    zero = await icpe_commune.lire(
        _contexte(amonts={"icpe_commune": _risques_commune(icpe=[])})
    )
    assert (zero.etat, zero.ton) == ("zero", "neutre")
    panne = await icpe_commune.lire(
        _contexte(amonts={"icpe_commune": lieu.PanneAmont()})
    )
    assert panne.etat == "panne"
    hors = await icpe_commune.lire(_contexte(hors=("georisques",)))
    assert hors.etat == "hors_couverture"


def _errial(
    sismicite_=None, pollution=None, concordance=True, parcelle="000-LB-369@44109"
) -> ErrialLu:
    return ErrialLu(
        parcelle=parcelle,
        concordance=concordance,
        resume={"sismicite": sismicite_, "pollution": pollution or {}},
    )


POLLUTION_ZERO = {
    "basias_sur_parcelle": 0, "basol_sur_parcelle": 0, "sis_sur_parcelle": 0,
    "basias_proximite": 2, "basol_proximite": 0, "sis_proximite": 0,
}  # fmt: skip


async def test_pollution_sols_tone_rule_and_parcel_concordance():
    zero = await pollution_sols.lire(
        _contexte(amonts={"pollution_sols": _errial(pollution=POLLUTION_ZERO)})
    )
    assert (zero.etat, zero.ton, zero.granularite.niveau) == (
        "zero",
        "rassurant",
        "parcelle",
    )
    assert zero.rattachement.confiance == "sure"
    pollue = {**POLLUTION_ZERO, "basol_sur_parcelle": 1}
    theme = await pollution_sols.lire(
        _contexte(amonts={"pollution_sols": _errial(pollution=pollue)})
    )
    assert (theme.etat, theme.ton) == ("ok", "vigilance")
    inconnu = await pollution_sols.lire(
        _contexte(
            amonts={"pollution_sols": _errial(pollution={"basias_sur_parcelle": None})}
        )
    )
    assert (inconnu.etat, inconnu.motif) == ("panne", "amont_indisponible")


async def test_an_unreadable_nearby_count_never_reassures():
    proches_illisibles = {**POLLUTION_ZERO, "basias_proximite": None}
    theme = await pollution_sols.lire(
        _contexte(amonts={"pollution_sols": _errial(pollution=proches_illisibles)})
    )
    assert (theme.etat, theme.ton) == ("zero", "neutre")
    assert theme.pannes_partielles == ["proximite"]


@pytest.mark.parametrize(
    ("concordance", "motif"),
    [(False, "parcelle_errial_differente"), (None, "parcelle_non_verifiee")],
)
@pytest.mark.parametrize(
    "pollution", [POLLUTION_ZERO, {**POLLUTION_ZERO, "sis_sur_parcelle": 2}]
)
async def test_another_errial_parcel_neither_reassures_nor_warns(
    concordance, motif, pollution
):
    theme = await pollution_sols.lire(
        _contexte(
            amonts={
                "pollution_sols": _errial(pollution=pollution, concordance=concordance)
            }
        )
    )
    assert (theme.etat, theme.ton, theme.motif) == ("manque", "info", motif)
    assert theme.rattachement.confiance == "incertaine"


def test_errial_parcel_is_compared_with_the_parcel_of_the_address():
    concorde = partage._parcelle_errial_concorde
    assert concorde("000-LB-369@44109", "44109000LB0369") is True
    assert concorde("000-LB-370@44109", "44109000LB0369") is False
    assert concorde("000-A-12@85191", "85191000A00012") is False
    assert concorde("000-0A-12@85191", "851910000A0012") is True
    assert concorde("000-LB-369@44109", None) is None


# --- DPE et bâtiment -------------------------------------------------------------


def _dpe(monkeypatch, *, distribution, rnb_documents=1, liens=1, total=None):
    total = sum(distribution.values()) if total is None else total
    agregat = DpeInventoryAggregate(
        total=total,
        apartment_document_count=total,
        distribution={**{c: 0 for c in "ABCDEFG"}, "unknown": 0, **distribution},
    )
    selection = SelectionPlusRecent(etat="indeterminee", motif="aucun_document")
    batiments = SimpleNamespace(batiments_rnb=rnb_documents, sans_rnb=0)
    _bloc_fixe(
        monkeypatch,
        dpe,
        {
            "dpe": (agregat, selection, batiments, date(2026, 10, 1)),
            "rnb": (liens, "2026-09") if isinstance(liens, int) else liens,
        },
    )


@pytest.mark.parametrize(
    ("distribution", "ton", "dominante"),
    [
        ({"F": 3, "D": 1}, "vigilance", "F"),
        ({"G": 2}, "vigilance", "G"),
        ({"B": 2, "C": 1}, "rassurant", "B"),
        ({"D": 2, "E": 1}, "neutre", "D"),
        ({"C": 1, "E": 1}, "neutre", None),
    ],
)
async def test_dpe_tone_rule_on_the_dominant_class(
    monkeypatch, distribution, ton, dominante
):
    _dpe(monkeypatch, distribution=distribution)
    theme = await dpe.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "adresse")
    chiffres = {c.id: c.valeur for c in theme.chiffres}
    assert chiffres.get("classe_dominante") == dominante
    assert theme.rattachement.entite == "adresse_saisie_diagnostiqueur"
    assert theme.rattachement.confiance == "probable"


async def test_dpe_of_several_buildings_says_so_and_is_uncertain(monkeypatch):
    _dpe(monkeypatch, distribution={"B": 5, "C": 6}, rnb_documents=3, liens=1)
    theme = await dpe.lire(_contexte())
    assert theme.verdict.startswith("DPE de plusieurs bâtiments à cette adresse")
    assert theme.ton == "vigilance"
    assert (theme.rattachement.confiance, theme.rattachement.candidats) == (
        "incertaine",
        3,
    )
    assert theme.rattachement.motif == "dpe_plusieurs_batiments"


async def test_several_rnb_buildings_at_the_key_lower_the_confidence(monkeypatch):
    _dpe(monkeypatch, distribution={"C": 2}, rnb_documents=0, liens=2)
    theme = await dpe.lire(_contexte())
    assert theme.rattachement.confiance == "incertaine"
    assert theme.rattachement.motif == "plusieurs_batiments_rnb_a_l_adresse"


async def test_an_rnb_outage_never_reassures_and_says_so(monkeypatch):
    _dpe(
        monkeypatch,
        distribution={"B": 2},
        rnb_documents=1,
        liens="lecture_indisponible",
    )
    theme = await dpe.lire(_contexte())
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert theme.pannes_partielles == ["rnb"]
    assert theme.rattachement.motif == "liens_rnb_indisponibles"
    assert {s.cle: s.mode for s in theme.sources}["rnb"] == "import"


async def test_without_building_evidence_a_good_class_is_neutral(monkeypatch):
    _dpe(monkeypatch, distribution={"A": 1, "B": 2}, rnb_documents=0, liens=0)
    theme = await dpe.lire(_contexte())
    assert theme.ton == "neutre"
    assert theme.details["batiments"]["un_seul_batiment_etabli"] is False
    # The RNB link of the BAN key alone never establishes the building.
    _dpe(monkeypatch, distribution={"B": 2}, rnb_documents=0, liens=1)
    assert (await dpe.lire(_contexte())).ton == "neutre"
    _dpe(monkeypatch, distribution={"B": 2}, rnb_documents=1, liens=0)
    servi = await dpe.lire(_contexte())
    assert servi.ton == "rassurant"
    assert {s.cle: s.millesime for s in servi.sources}["rnb"] == "2026-09"


async def test_dpe_zero_outage_and_out_of_coverage(monkeypatch):
    _dpe(monkeypatch, distribution={}, total=0)
    zero = await dpe.lire(_contexte())
    assert (zero.etat, zero.ton, zero.verdict) == (
        "zero",
        "neutre",
        # The import starts in July 2021: the sentence says since when.
        "Aucun DPE depuis juillet 2021 à cette adresse ; "
        "les diagnostics plus anciens ne sont plus valables.",
    )
    _bloc_fixe(monkeypatch, dpe, {"dpe": "publication_non_validee", "rnb": 1})
    panne = await dpe.lire(_contexte())
    assert (panne.etat, panne.motif) == ("panne", "publication_non_validee")
    assert panne.rattachement.confiance == "incertaine"
    assert (await dpe.lire(_contexte(hors=("dpe",)))).etat == "hors_couverture"


def test_dominant_class_and_range():
    assert dpe.classe_dominante({"C": 2, "E": 2}) is None
    assert dpe.classe_dominante({"unknown": 4}) is None
    assert dpe.plage({"B": 1, "F": 2}) == ("B", "F")


async def test_batiment_at_the_point_and_on_the_parcel(monkeypatch):
    _batiment_parcelle(
        monkeypatch,
        [
            _champ(lieu.FIELD_BATIMENTS, valeur=2, periode="2025-03"),
            _champ(lieu.FIELD_BATIMENTS_HORS_ANNEXE, valeur=1),
            _champ(lieu.FIELD_HAUTEUR, valeur=12.0),
            _champ(lieu.FIELD_ETAGES, valeur=4),
            _champ(lieu.FIELD_RNB, valeur="RNB000000001"),
        ],
    )
    theme = await batiment.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "batiment",
    )
    assert theme.verdict == (
        "Bâtiment de 12 m et 4 étages au point de l'adresse ; "
        "2 bâtiments sur la parcelle."
    )
    assert theme.rattachement.confiance == "sure"
    assert theme.details["rnb"]["identifiant"] == "RNB000000001"


async def test_an_rnb_outage_survives_every_early_return(monkeypatch):
    panne_rnb = _champ(lieu.FIELD_RNB, "temporary_error", motif="lecture_indisponible")
    _batiment_parcelle(monkeypatch, [panne_rnb], etat="not_available")
    sans_parcelle = await batiment.lire(_contexte(relation=AMBIGUE))
    assert sans_parcelle.pannes_partielles == ["rnb"]
    _batiment_parcelle(
        monkeypatch,
        [_champ(lieu.FIELD_BATIMENTS, "out_of_coverage", motif="x"), panne_rnb],
    )
    hors = await batiment.lire(_contexte())
    assert (hors.etat, hors.pannes_partielles) == ("hors_couverture", ["rnb"])


async def test_batiment_without_a_single_building_at_the_point(monkeypatch):
    _batiment_parcelle(
        monkeypatch,
        [
            _champ(lieu.FIELD_BATIMENTS, valeur=3),
            _champ(
                lieu.FIELD_HAUTEUR,
                "not_available",
                motif=lieu.MOTIF_BATIMENT_NON_UNIQUE,
            ),
        ],
    )
    theme = await batiment.lire(_contexte())
    assert theme.rattachement.confiance == "incertaine"
    assert "n'est pas identifié" in theme.verdict


IMPORT_44_85 = ProvenanceFacts(
    source_period_label="2026-02.b", effective_departments=["44", "85"]
)


def _bdnb(*annees, total=None, faits=IMPORT_44_85) -> BdnbLu:
    """The groups of the parcel as the import reads them: the first ten in
    ``lignes``, every year in ``annees``."""
    lignes = [
        {"batiment_groupe_id": f"g{i}", "annee_construction": a}
        for i, a in enumerate(annees)
    ]
    return BdnbLu(
        lignes=lignes[:10],
        total=len(annees) if total is None else total,
        annees=frozenset(annees),
        faits=faits,
    )


async def _construction(monkeypatch, lu, **contexte) -> Theme:
    _bloc_fixe(monkeypatch, construction, {"bdnb": lu})
    return await construction.lire(_contexte(**contexte))


@pytest.mark.parametrize(
    ("annee", "ton"), [(1932, "vigilance"), (1948, "vigilance"), (1949, "neutre")]
)
async def test_construction_tone_rule(monkeypatch, annee, ton):
    theme = await _construction(monkeypatch, _bdnb(annee))
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "batiment")
    assert theme.chiffres[0].valeur == annee
    assert theme.chiffres[0].millesime == "2026-02.b"
    assert (theme.rattachement.confiance, theme.rattachement.cle) == ("probable", "g0")
    assert [(s.cle, s.mode, s.millesime) for s in theme.sources] == [
        ("bdnb", "import", "2026-02.b")
    ]


async def test_construction_out_of_coverage_reads_nothing(monkeypatch):
    theme = await _construction(monkeypatch, "jamais lu", hors=("bdnb",))
    assert (theme.etat, theme.motif) == ("hors_couverture", "dataset_non_heberge")
    sans_parcelle = await _construction(monkeypatch, "jamais lu", relation=AMBIGUE)
    assert (sans_parcelle.etat, sans_parcelle.motif) == (
        "manque",
        "parcelles_multiples",
    )


@pytest.mark.parametrize(
    "faits",
    [
        None,  # no dataset_metadata row for the import
        ProvenanceFacts(source_period_label="2026-02.b"),  # NULL departements
        ProvenanceFacts(source_period_label="2026-02.b", effective_departments=[]),
        ProvenanceFacts(source_period_label="2026-02.b", effective_departments=["85"]),
    ],
    ids=["sans_metadonnees", "departements_null", "departements_vides", "autre"],
)
async def test_construction_departement_not_imported_is_an_outage(monkeypatch, faits):
    """Never « aucun bâtiment »: the rows are absent because the import is."""
    theme = await _construction(monkeypatch, _bdnb(total=0, faits=faits))
    assert (theme.etat, theme.motif) == ("panne", "import_absent")
    assert theme.ton == "info" and theme.chiffres == []


async def test_construction_unreadable_block_is_lecture_indisponible(monkeypatch):
    theme = await _construction(monkeypatch, "lecture_indisponible")
    assert (theme.etat, theme.motif) == ("panne", "lecture_indisponible")


class _Lignes:
    def __init__(self, lignes):
        self._lignes = lignes

    def __iter__(self):
        return iter(SimpleNamespace(_mapping=ligne) for ligne in self._lignes)

    def mappings(self):
        return self

    def all(self):
        return list(self._lignes)


class _SessionSql(_Session):
    """The two reads of the block: the facts of the import, the groups."""

    def __init__(self, groupes=(), metadonnees=None):
        self.groupes = list(groupes)
        self.metadonnees = metadonnees

    async def execute(self, statement, params=None):
        if "dataset_metadata" in str(statement):
            if isinstance(self.metadonnees, Exception):
                raise self.metadonnees
            return _Lignes(self.metadonnees or [])
        return _Lignes(self.groupes)


_METADONNEES_44_85 = [
    {
        "dataset_name": "bdnb_batiment_parcelle",
        "source_period_label": "2026-02.b",
        "source_updated_at": None,
        "effective_departments": ["44", "85"],
        "last_success_at": None,
    }
]


def _groupes(*annees):
    """Rows of the groups query: the summary on every row, ten groups at most."""
    tries = sorted(set(annees), key=lambda a: (a is None, a))
    return [
        {
            "total": len(annees),
            "annees": tries,
            "batiment_groupe_id": f"g{i:02d}",
            "annee_construction": annee,
            "mat_mur_txt": "BETON",
            "mat_toit_txt": None,
        }
        for i, annee in enumerate(annees[:10])
    ]


async def _construction_sql(session) -> Theme:
    contexte = _contexte()
    return await construction.lire(replace(contexte, session=session))


async def test_construction_metadata_sql_error_is_never_import_absent():
    """A SQL error on dataset_metadata is the outage of the block
    (``lecture_indisponible``), never an import absent: the facts are read by
    ``get_provenance_facts`` inside the block, not ``faits_de_provenance``."""
    erreur = ProgrammingError("SELECT", {}, Exception("relation absente"))
    theme = await _construction_sql(_SessionSql(_groupes(1975), metadonnees=erreur))
    assert (theme.etat, theme.motif) == ("panne", "lecture_indisponible")


async def test_construction_reads_the_import_rows_and_facts():
    theme = await _construction_sql(
        _SessionSql(_groupes(1975), metadonnees=_METADONNEES_44_85)
    )
    assert (theme.etat, theme.chiffres[0].valeur) == ("ok", 1975)
    assert theme.sources[0].millesime == "2026-02.b"
    vide = await _construction_sql(
        _SessionSql(
            [{"total": 0, "annees": None, "batiment_groupe_id": None,
              "annee_construction": None, "mat_mur_txt": None,
              "mat_toit_txt": None}],
            metadonnees=_METADONNEES_44_85,
        )
    )  # fmt: skip
    assert (vide.etat, vide.motif) == ("manque", "aucun_batiment_bdnb")
    assert vide.details == {"groupes": [], "liste_incomplete": False}


async def test_construction_counts_every_group_beyond_ten():
    """D8: the total is exact beyond the ten groups listed; one shared year is
    served however many groups there are."""
    memes = await _construction_sql(
        _SessionSql(_groupes(*([1990] * 12)), metadonnees=_METADONNEES_44_85)
    )
    assert (memes.etat, memes.chiffres[0].valeur) == ("ok", 1990)
    assert (memes.rattachement.candidats, memes.rattachement.motif) == (
        12,
        "meme_annee_pour_tous",
    )
    assert len(memes.details["groupes"]) == 10
    assert memes.details["liste_incomplete"] is True
    differentes = await _construction_sql(
        _SessionSql(_groupes(*([1990] * 11), 1991), metadonnees=_METADONNEES_44_85)
    )
    assert (differentes.etat, differentes.motif) == (
        "manque",
        "plusieurs_batiments_sur_la_parcelle",
    )
    assert differentes.rattachement.candidats == 12
    assert differentes.verdict.startswith("La BDNB recense 12 bâtiments")


async def test_construction_never_picks_one_building_among_several(monkeypatch):
    differentes = await _construction(monkeypatch, _bdnb(1900, 1980))
    assert (differentes.etat, differentes.motif) == (
        "manque",
        "plusieurs_batiments_sur_la_parcelle",
    )
    assert (differentes.rattachement.candidats, differentes.rattachement.confiance) == (
        2,
        "incertaine",
    )
    # A group without year differs from a group with one (NULL included).
    avec_inconnue = await _construction(monkeypatch, _bdnb(1975, None))
    assert (avec_inconnue.etat, avec_inconnue.rattachement.candidats) == ("manque", 2)
    memes = await _construction(monkeypatch, _bdnb(1975, 1975))
    assert (memes.etat, memes.rattachement.motif) == ("ok", "meme_annee_pour_tous")
    assert memes.rattachement.cle is None


async def test_construction_year_not_reported_and_no_building(monkeypatch):
    sans_annee = await _construction(monkeypatch, _bdnb(None))
    assert (sans_annee.etat, sans_annee.motif) == ("manque", "annee_non_renseignee")
    assert (sans_annee.rattachement.confiance, sans_annee.rattachement.cle) == (
        "probable",
        "g0",
    )
    aucun = await _construction(monkeypatch, _bdnb())
    assert (aucun.etat, aucun.motif) == ("manque", "aucun_batiment_bdnb")
    assert aucun.verdict.startswith("La BDNB ne recense aucun bâtiment")


async def test_copropriete_exact_parcel_join(monkeypatch):
    registre = {
        "nb_lots_total": 24,
        "nb_lots_habitation_strict": 18,
        "numero_immatriculation": "AA1",
    }
    _bloc_fixe(monkeypatch, copropriete, {"copropriete": [registre]})
    theme = await copropriete.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == (
        "ok",
        "neutre",
        "parcelle",
    )
    assert theme.verdict == "Copropriété immatriculée de 24 lots, dont 18 d'habitation."
    _bloc_fixe(monkeypatch, copropriete, {"copropriete": []})
    zero = await copropriete.lire(_contexte())
    assert (zero.etat, zero.rattachement.cle) == ("zero", PARCELLE)
    # What the registry cannot know is said beside the answer, found or not.
    for lu in (theme, zero):
        assert lu.details["mention"] == (
            "Le registre cite au plus trois parcelles par copropriété ; "
            "une copropriété non immatriculée n'y figure pas."
        )


# --- Vivre ici -------------------------------------------------------------------


def _reseaux(monkeypatch, champs):
    async def lu(session, contexte):
        return _lieu("reseaux_transports", champs)

    async def sans_gare(contexte):
        return None

    monkeypatch.setattr(lieu, "theme_reseaux_transports", lu)
    monkeypatch.setattr(transports, "gare_proche", sans_gare)


@pytest.mark.parametrize(
    ("eligible", "ton"), [(True, "rassurant"), (False, "vigilance")]
)
async def test_fibre_tone_rule(monkeypatch, eligible, ton):
    _lignes_cable(monkeypatch)
    _reseaux(
        monkeypatch, [_champ(lieu.FIELD_FIBRE, valeur=eligible, periode="2026-T2")]
    )
    theme = await fibre.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.niveau) == ("ok", ton, "adresse")


def _lignes_cable(monkeypatch, *lignes):
    """The offers published for the address, read again when it has no fibre."""
    _bloc_fixe(
        monkeypatch,
        fibre,
        {"connectivite": EligibiliteAdresse(etat="unique", lignes=lignes)},
    )


@pytest.mark.parametrize("debit", ["THD100", "THD1G"])
async def test_a_fast_cable_network_without_fibre_is_said_and_is_no_vigilance(
    monkeypatch, debit
):
    _reseaux(monkeypatch, [_champ(lieu.FIELD_FIBRE, valeur=False, periode="2026-T2")])
    _lignes_cable(
        monkeypatch,
        {"technologie": "CU", "debit_max": "HD3"},
        {"technologie": "COAX", "debit_max": debit},
    )
    theme = await fibre.lire(_contexte())
    assert (theme.etat, theme.ton) == ("ok", "neutre")
    assert theme.verdict == (
        "Pas de fibre jusqu'au logement publiée pour cette adresse ; "
        "un réseau câblé à très haut débit l'est."
    )


@pytest.mark.parametrize(
    "lignes",
    [
        ({"technologie": "CU", "debit_max": "HD3"},),
        # A slow cable network or a fast satellite offer is no fast cable.
        ({"technologie": "COAX", "debit_max": "THD30"},),
        ({"technologie": "SAT", "debit_max": "THD100"},),
    ],
)
async def test_without_fibre_nor_fast_cable_the_vigilance_stays(monkeypatch, lignes):
    _reseaux(monkeypatch, [_champ(lieu.FIELD_FIBRE, valeur=False, periode="2026-T2")])
    _lignes_cable(monkeypatch, *lignes)
    theme = await fibre.lire(_contexte())
    assert (theme.etat, theme.ton) == ("ok", "vigilance")
    assert theme.verdict.startswith("Pas d'offre fibre publiée pour cette adresse")


async def test_an_unreadable_cable_offer_never_lifts_the_vigilance(monkeypatch):
    _reseaux(monkeypatch, [_champ(lieu.FIELD_FIBRE, valeur=False, periode="2026-T2")])
    _bloc_fixe(monkeypatch, fibre, {"connectivite": "publication_non_validee"})
    assert (await fibre.lire(_contexte())).ton == "vigilance"


@pytest.mark.parametrize("manquant", ["numero", "nom_voie"])
async def test_an_address_without_number_or_street_reads_no_cable_offer(
    monkeypatch, manquant
):
    """Such an address cannot be matched in the eligibility data: the offers
    are not read at all, and the vigilance stays."""
    _reseaux(monkeypatch, [_champ(lieu.FIELD_FIBRE, valeur=False, periode="2026-T2")])

    async def jamais_lu(*args, **kwargs):
        raise AssertionError("the cable offers must not be read")

    monkeypatch.setattr(fibre, "lire_bloc", jamais_lu)
    contexte = _contexte()
    contexte = replace(
        contexte, identite=contexte.identite.model_copy(update={manquant: None})
    )
    assert (await fibre.lire(contexte)).ton == "vigilance"


async def test_fibre_without_offer_is_a_gap(monkeypatch):
    _reseaux(monkeypatch, [_champ(lieu.FIELD_FIBRE, "not_reported")])
    assert (await fibre.lire(_contexte())).etat == "manque"


@pytest.mark.parametrize(
    ("distance", "ton"), [(180, "rassurant"), (500, "rassurant"), (740, "neutre")]
)
async def test_transports_tone_rule(monkeypatch, distance, ton):
    arret = ElementLieu(libelle="Commerce", distance_m=distance, nature="arret")
    _reseaux(
        monkeypatch,
        [_champ(lieu.FIELD_ARRETS, valeur=1, elements=[arret], rayon_m=1000)],
    )
    theme = await transports.lire(_contexte())
    assert (theme.etat, theme.ton) == ("ok", ton)
    assert (theme.granularite.niveau, theme.granularite.rayon_m) == ("rayon", 1000)


async def test_stops_not_hosted_are_never_no_stop(monkeypatch):
    _reseaux(
        monkeypatch, [_champ(lieu.FIELD_ARRETS, valeur=0, elements=[], rayon_m=1000)]
    )
    theme = await transports.lire(_contexte(hors=("transports",)))
    assert (theme.etat, theme.motif) == ("hors_couverture", "categorie_non_couverte")
    arret = ElementLieu(libelle="Gare", distance_m=900, nature="gare")
    _reseaux(
        monkeypatch,
        [_champ(lieu.FIELD_ARRETS, valeur=1, elements=[arret], rayon_m=1000)],
    )
    partiel = await transports.lire(_contexte(hors=("transports",)))
    assert partiel.etat == "ok"
    assert partiel.details["categories_non_couvertes"] == ["transports"]


async def test_no_stop_within_a_kilometre_is_a_zero_to_check(monkeypatch):
    _reseaux(
        monkeypatch, [_champ(lieu.FIELD_ARRETS, valeur=0, elements=[], rayon_m=1000)]
    )
    theme = await transports.lire(_contexte())
    assert (theme.etat, theme.ton) == ("zero", "vigilance")


async def test_equipements_tone_rule(monkeypatch):
    complet = {
        "commerces": 12,
        "sante_social": 4,
        "enseignement": 2,
        "sports_loisirs_culture": 3,
    }
    _bloc_fixe(monkeypatch, equipements, {"equipements": (complet, False)})
    theme = await equipements.lire(_contexte())
    assert (theme.etat, theme.ton) == ("ok", "rassurant")
    _bloc_fixe(monkeypatch, equipements, {"equipements": ({"commerces": 1}, False)})
    assert (await equipements.lire(_contexte())).ton == "neutre"
    _bloc_fixe(monkeypatch, equipements, {"equipements": ({}, False)})
    assert (await equipements.lire(_contexte())).etat == "zero"
    _bloc_fixe(monkeypatch, equipements, {"equipements": ({}, True)})
    deborde = await equipements.lire(_contexte())
    assert deborde.etat == "hors_couverture"
    _bloc_fixe(monkeypatch, equipements, {"equipements": (None, False)})
    poids = await equipements.lire(_contexte())
    assert (poids.etat, poids.motif) == ("manque", "poids_incomplets")


async def test_antennes_tone_rule(monkeypatch):
    _bloc_fixe(monkeypatch, antennes_5g, {"antennes": (5, 2, False)})
    theme = await antennes_5g.lire(_contexte())
    assert (theme.etat, theme.ton, theme.granularite.rayon_m) == (
        "ok",
        "rassurant",
        1000,
    )
    _bloc_fixe(monkeypatch, antennes_5g, {"antennes": (3, 0, False)})
    zero = await antennes_5g.lire(_contexte())
    assert (zero.etat, zero.ton) == ("zero", "neutre")
    _bloc_fixe(monkeypatch, antennes_5g, {"antennes": "lecture_indisponible"})
    assert (await antennes_5g.lire(_contexte())).etat == "panne"
