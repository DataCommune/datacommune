"""Sales of the parcel of the point, without a database (04-05, D-115).

The price of a recent isolated sale never leaves the API: the decision is
taken here (``minimiser``), anchored on the last imported DVF date and never
on the system clock, and a sale whose isolation cannot be established is
withheld too. Nothing of a dwelling and no mutation identifier is served.
"""

import json
from datetime import date
from decimal import Decimal

import pytest

from app.adresse import ventes
from app.adresse.schemas import CarteVente, MinimisationVente, VentesParcelle
from app.common.schemas import DatasetHosting, OutOfCoverageReason
from app.dvf.queries import POSITION_CONNUE_SQL
from app.scope.dependencies import RouteHosting

pytestmark = pytest.mark.unit

FIN_DVF = date(2025, 11, 20)
HEBERGE = DatasetHosting(etat="heberge", departements=["85"])
NON_HEBERGE = DatasetHosting(
    etat="hors_couverture", motif=OutOfCoverageReason.DATASET_NON_HEBERGE
)


def _hosting(**out: DatasetHosting) -> RouteHosting:
    keys = ("ban", "cadastre", "dvf")
    return RouteHosting({key: out.get(key, HEBERGE) for key in keys})


def _ligne(**partiel) -> dict:
    ligne = {
        "id_mutation": "2025-12345",
        "date_mutation": date(2025, 6, 1),
        "nature_mutation": "Vente",
        "valeur_fonciere": Decimal("245000.00"),
        "nb_locaux_maison": 1,
        "nb_locaux_appartement": 0,
        "nb_locaux_dependance": 1,
        "nb_locaux_commercial": 0,
        "surface_bati_totale": 90,
        "prix_m2": Decimal("2722"),
    }
    ligne.update(partiel)
    return ligne


# --- recency window, anchored on the DVF period --------------------------------


def test_the_window_is_24_months_back_from_the_last_dvf_date():
    assert ventes.seuil_recence(FIN_DVF) == date(2023, 11, 20)


def test_the_window_of_a_29_february_ends_on_the_28th():
    assert ventes.seuil_recence(date(2024, 2, 29)) == date(2022, 2, 28)


def test_the_window_never_reads_the_system_clock(monkeypatch):
    class Horloge:
        @staticmethod
        def today():  # pragma: no cover - must never be called
            raise AssertionError("system clock read")

    monkeypatch.setattr(ventes, "date", Horloge, raising=False)
    assert ventes.seuil_recence(FIN_DVF) == FIN_DVF.replace(year=2023)


# --- minimisation ---------------------------------------------------------------

SEUIL = date(2023, 11, 20)


@pytest.mark.parametrize(
    ("jour", "isolee", "motif"),
    [
        (date(2025, 6, 1), True, "vente_isolee_recente"),
        # The boundary is inclusive: exactly 24 months back is recent.
        (SEUIL, True, "vente_isolee_recente"),
        (date(2023, 11, 19), True, None),
        (date(2025, 6, 1), False, None),
        # Isolation unknown (no parcel link, predicate in outage): no exact price.
        (date(2025, 6, 1), None, "isolement_indetermine"),
        (date(2019, 3, 15), None, None),
    ],
)
def test_minimiser_withholds_a_recent_isolated_or_undetermined_sale(
    jour, isolee, motif
):
    resultat = ventes.minimiser(jour, isolee, SEUIL)
    assert (resultat.motif if resultat else None) == motif


def test_minimiser_without_a_known_dvf_end_withholds_an_undetermined_sale():
    assert ventes.minimiser(date(2025, 6, 1), True, None) == MinimisationVente(
        motif="isolement_indetermine"
    )
    assert ventes.minimiser(date(2025, 6, 1), False, None) is None


def test_a_withheld_card_carries_no_price_nor_any_price_derivative():
    carte = ventes.carte_vente(
        _ligne(), MinimisationVente(motif="vente_isolee_recente")
    )
    assert carte.prix is None and carte.prix_m2 is None
    assert carte.prix_affiche is False
    assert carte.minimisation.motif == "vente_isolee_recente"
    servi = json.dumps(carte.model_dump(mode="json"))
    assert "245000" not in servi and "2722" not in servi
    # Not a price: the surface and the composition stay served.
    assert carte.surface_batie == 90
    assert carte.composition.maisons == 1 and carte.composition.dependances == 1


def test_a_shown_card_carries_the_amount_and_the_honest_price_per_m2():
    carte = ventes.carte_vente(_ligne(), None)
    assert (carte.prix, carte.prix_m2, carte.prix_affiche) == (245000.0, 2722, True)
    assert carte.minimisation is None
    assert carte.date == date(2025, 6, 1) and carte.nature == "Vente"


def test_a_card_never_serves_the_mutation_identifier_nor_a_dwelling():
    servi = ventes.carte_vente(_ligne(), None).model_dump(mode="json")
    assert "2025-12345" not in json.dumps(servi)
    assert set(CarteVente.model_fields) == {
        "date",
        "nature",
        "composition",
        "surface_batie",
        "prix",
        "prix_m2",
        "prix_affiche",
        "minimisation",
        "distance_m",
        "hors_stats_vefa",
    }
    assert not any("logement" in name for name in VentesParcelle.model_fields)


def test_a_vefa_card_is_marked_out_of_statistics_with_its_price_rule():
    """Plan 04.1-15 (D-12): a VEFA is listed, marked, its price rule unchanged;
    a row without the column (or a sale) is not marked."""
    vefa = ventes.carte_vente(
        _ligne(
            nature_mutation="Vente en l'état futur d'achèvement", hors_stats_vefa=True
        ),
        None,
    )
    assert vefa.hors_stats_vefa is True and vefa.prix == 245000.0
    assert (
        ventes.carte_vente(_ligne(hors_stats_vefa=False), None).hors_stats_vefa is False
    )
    assert ventes.carte_vente(_ligne(), None).hors_stats_vefa is False


def test_composition_counts_every_kind_of_local():
    carte = ventes.carte_vente(
        _ligne(
            nb_locaux_maison=0,
            nb_locaux_appartement=2,
            nb_locaux_dependance=1,
            nb_locaux_commercial=1,
        ),
        None,
    )
    assert carte.composition.model_dump() == {
        "maisons": 0,
        "appartements": 2,
        "dependances": 1,
        "locaux_activite": 1,
    }


def test_an_amount_without_price_per_m2_is_not_invented():
    carte = ventes.carte_vente(_ligne(prix_m2=None, valeur_fonciere=None), None)
    assert (carte.prix, carte.prix_m2, carte.prix_affiche) == (None, None, True)


# --- pages and generation --------------------------------------------------------


@pytest.mark.parametrize(("total", "pages"), [(0, 1), (1, 1), (10, 1), (23, 3)])
def test_pages_of_ten(total, pages):
    assert ventes.nombre_pages(total) == pages


def test_the_generation_token_names_both_dvf_relations():
    jeton = ventes.jeton_generation_dvf(101, 202)
    assert len(jeton) == 16 and int(jeton, 16) >= 0
    assert jeton == ventes.jeton_generation_dvf(101, 202)
    assert jeton != ventes.jeton_generation_dvf(101, 203)
    assert jeton != ventes.jeton_generation_dvf(102, 202)


# --- coverage, before any read ------------------------------------------------------


class _SansLecture:
    """A session that must not be used."""

    def __getattr__(self, name):  # pragma: no cover - fails the test
        raise AssertionError(f"session.{name} used")


@pytest.mark.parametrize("cle", ["ban", "dvf"])
async def test_a_source_not_hosted_is_out_of_coverage_without_reading(cle):
    resultat = await ventes.lire_ventes_parcelle(
        _SansLecture(), "85191_0420_00012", 1, None, _hosting(**{cle: NON_HEBERGE})
    )
    assert resultat.etat == "out_of_coverage"
    assert resultat.motif == "dataset_non_heberge"
    assert resultat.ventes == [] and resultat.total is None
    assert resultat.source.cle == "dvf"


# --- registry -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field_id", "unite", "etat_retenu"),
    [
        ("adresse.dvf.vente.prix", "euro", "suppressed"),
        ("adresse.dvf.vente.prix_m2", "euro_per_m2", "suppressed"),
        ("adresse.dvf.vente.surface_batie", "m2", "not_reported"),
    ],
)
def test_sale_fields_are_attached_to_the_parcel_of_the_point(
    field_id, unite, etat_retenu
):
    from app.common.semantic_contract import get_field

    contract = get_field(field_id)
    assert contract.rattachement == "parcelle_du_point"
    assert contract.unit == unite
    assert etat_retenu in contract.allowed_states
    assert "logement" in (contract.description or "") or "local" in (
        contract.description or ""
    )


# --- Task 2: similar sales (D-114) ------------------------------------------------


@pytest.mark.parametrize(
    ("surface", "tolerance", "bornes"),
    [
        (90, 20, (72, 108)),
        (90, 10, (81, 99)),
        (90, 30, (63, 117)),
        # Rounded to the square metre, half up: exactly the bounds filtered on.
        (33, 10, (30, 36)),
        (25, 30, (18, 33)),
        (None, 20, (None, None)),
    ],
)
def test_surface_bounds_are_computed_by_the_api(surface, tolerance, bornes):
    criteres = ventes.CriteresComparables(
        type="maison", surface=surface, tolerance=tolerance
    )
    assert criteres.bornes_surface() == bornes


def test_default_criteria_are_the_ui_spec_defaults():
    criteres = ventes.CriteresComparables(type="maison")
    assert (criteres.tolerance, criteres.rayon, criteres.periode) == (20, 500, 5)
    assert criteres.surface is None


@pytest.mark.parametrize(
    "invalide",
    [
        {"type": "bureau"},
        {"type": "maison", "tolerance": 50},
        {"type": "maison", "rayon": 5000},
        {"type": "maison", "periode": 10},
        {"type": "maison", "surface": 5},
        {"type": "maison", "surface": 1001},
    ],
)
def test_criteria_outside_the_closed_enumerations_are_refused(invalide):
    with pytest.raises(ValueError):
        ventes.CriteresComparables(**invalide)


@pytest.mark.parametrize(
    ("annees", "fin", "periode"),
    [(5, date(2025, 11, 20), (2021, 2025)), (1, date(2025, 11, 20), (2025, 2025))],
)
def test_the_period_counts_calendar_years_back_from_the_last_dvf_date(
    annees, fin, periode
):
    criteres = ventes.CriteresComparables(type="appartement", periode=annees)
    appliques = ventes.criteres_appliques(criteres, fin)
    assert (appliques.periode.debut, appliques.periode.fin) == periode
    assert appliques.periode.annees == annees


def test_applied_criteria_say_the_commune_or_the_radius():
    commune = ventes.criteres_appliques(
        ventes.CriteresComparables(type="maison", rayon=None), FIN_DVF
    )
    rayon = ventes.criteres_appliques(
        ventes.CriteresComparables(type="maison", surface=90, rayon=1000), FIN_DVF
    )
    assert (commune.perimetre, commune.rayon_m) == ("commune", None)
    assert (rayon.perimetre, rayon.rayon_m) == ("rayon", 1000)
    assert (rayon.surface, rayon.surface_min, rayon.surface_max) == (90, 72, 108)
    unknown = ventes.criteres_appliques(ventes.CriteresComparables(type="maison"), None)
    assert (unknown.periode.debut, unknown.periode.fin) == (None, None)


@pytest.mark.parametrize("cle", ["ban", "dvf"])
async def test_similar_sales_out_of_coverage_without_reading(cle):
    resultat = await ventes.lire_ventes_ressemblantes(
        _SansLecture(),
        "85191_0420_00012",
        ventes.CriteresComparables(type="maison"),
        None,
        _hosting(**{cle: NON_HEBERGE}),
    )
    assert resultat.etat == "out_of_coverage"
    assert resultat.effectif is None and resultat.ventes == []
    assert resultat.rattachement == "rayon_du_point"
    assert resultat.criteres_appliques.type == "maison"


def test_the_similar_sales_field_is_registered():
    from app.common.semantic_contract import get_field

    contract = get_field("adresse.dvf.ventes_ressemblantes.effectif")
    assert contract.rattachement == "rayon_du_point"
    assert contract.zero_semantics == "measured_zero"


class _Savepoints:
    """A session whose only use is the savepoint of a block."""

    def begin_nested(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def savepoint():
            yield

        return savepoint()


async def test_similar_sales_of_an_empty_dvf_table_are_never_a_zero(monkeypatch):
    """API IN-05: no DVF date known is a missing list, never « 0 vente »."""
    from app.adresse import blocs
    from app.adresse.relations import PointBan

    async def sans_garde(session, datasets):
        return None

    async def point(session, ban_id):
        return True, PointBan(lon=-1.43, lat=46.67)

    async def generation(session):
        return "0123456789abcdef"

    async def fenetre(session):
        return ventes._Fenetre(fin=None)

    monkeypatch.setattr(blocs, "guard_publication", sans_garde)
    monkeypatch.setattr(ventes, "_point", point)
    monkeypatch.setattr(ventes, "_generation", generation)
    monkeypatch.setattr(ventes, "_fenetre", fenetre)

    resultat = await ventes.lire_ventes_ressemblantes(
        _Savepoints(),
        "85191_0420_00012",
        ventes.CriteresComparables(type="maison", rayon=1000),
        None,
        _hosting(),
    )

    assert resultat.etat == "not_available"
    assert resultat.effectif is None and resultat.ventes == []


# --- Sales declared at the address (C37) ----------------------------------------


@pytest.mark.parametrize(
    ("voie", "formes"),
    [
        ("AVENUE DES CHATAIGNIERS", ["AVENUE DES CHATAIGNIERS", "AV DES CHATAIGNIERS"]),
        ("IMPASSE DU MOULIN", ["IMPASSE DU MOULIN", "IMP DU MOULIN"]),
        ("CHEMIN DE LA LANDE", ["CHEMIN DE LA LANDE", "CHE DE LA LANDE"]),
        # A street without a known type keeps its one form.
        ("RUE DE L EGLISE", ["RUE DE L EGLISE"]),
        ("LA GRANDE ROCHE", ["LA GRANDE ROCHE"]),
        # Only the leading type is abbreviated; a type alone is the name.
        ("LE CHEMIN", ["LE CHEMIN"]),
        ("AVENUE", ["AVENUE"]),
        ("", []),
        (None, []),
    ],
)
def test_a_ban_street_is_compared_under_its_full_and_abbreviated_forms(
    voie, formes
) -> None:
    assert ventes.formes_de_voie(voie) == formes


@pytest.mark.parametrize(
    ("numero", "attendu"),
    [("2", "2"), ("02", "2"), (" 12 ", "12"), ("0", None), ("2A", None), ("", None)],
)
def test_the_ban_number_as_dvf_writes_it(numero, attendu) -> None:
    assert ventes.numero_dvf(numero) == attendu
    assert ventes.numero_dvf(None) is None


def _ban(**partiel) -> dict:
    row = {
        "numero": "2",
        "rep": None,
        "voie_normalisee": "AVENUE DES CHATAIGNIERS",
        "code_commune": "85193",
    }
    row.update(partiel)
    return row


def test_the_key_of_an_address_without_suffix() -> None:
    cle = ventes.cle_adresse_dvf([_ban(), _ban()])
    assert cle == ventes.CleAdresseDvf(
        motif=None,
        commune="85193",
        numero="2",
        voies=("AVENUE DES CHATAIGNIERS", "AV DES CHATAIGNIERS"),
    )


@pytest.mark.parametrize(
    ("rows", "motif"),
    [
        # Bis/ter (C37, « Au 2 seulement »): the imported DVF keeps no suffix
        # (C44: the source has one, `adresse_suffixe`, kept from 4.1).
        ([_ban(rep="bis")], "suffixe_non_distingue"),
        ([_ban(rep="B")], "suffixe_non_distingue"),
        ([_ban(numero=None)], "sans_numero"),
        ([_ban(numero="2A")], "sans_numero"),
        # IN-02 (261010-wpt): 0 is never written by DVF (ltrim of « 0 » is
        # empty): not a comparable number, never a sale-less address.
        ([_ban(numero="0")], "sans_numero"),
        ([_ban(numero="000")], "sans_numero"),
        ([_ban(voie_normalisee=None)], "adresse_indeterminee"),
        ([_ban(code_commune=None)], "adresse_indeterminee"),
        # Rows of one key that disagree are never resolved by picking one.
        ([_ban(), _ban(numero="4")], "adresse_indeterminee"),
        ([_ban(), _ban(rep="bis")], "adresse_indeterminee"),
        ([_ban(), _ban(code_commune="85194")], "adresse_indeterminee"),
    ],
)
def test_an_address_not_compared_with_dvf_says_why(rows, motif) -> None:
    cle = ventes.cle_adresse_dvf(rows)
    assert (cle.motif, cle.numero, cle.voies) == (motif, None, ())


# --- Rule « numéro et suffixe » (plan 04.1-14, C44), served on request -------------


def test_the_measured_suffix_table_is_coded_as_measured() -> None:
    """mesures/dvf-suffixe/correspondance.json (2026-10-19): DVF writes one
    capital letter; BAN writes bis/ter/qua/quater or a letter, in any case."""
    table = ventes.CORRESPONDANCE_SUFFIXE_BAN_DVF
    assert {rep: table[rep] for rep in ("bis", "ter", "quater", "qua")} == {
        "bis": "B",
        "ter": "T",
        "quater": "Q",
        "qua": "Q",
    }
    assert {rep: code for rep, code in table.items() if len(rep) == 1} == {
        chr(c): chr(c).upper() for c in range(ord("a"), ord("z") + 1)
    }
    assert len(table) == 30


@pytest.mark.parametrize(
    ("rep", "code"),
    [
        (None, ""),
        ("  ", ""),
        ("bis", "B"),
        ("Bis", "B"),
        ("BIS ", "B"),
        ("b", "B"),
        ("B", "B"),
        ("ter", "T"),
        ("t", "T"),
        ("qua", "Q"),
        ("Quater", "Q"),
        ("c", "C"),
        # No established correspondence: never guessed.
        ("quinquies", None),
        ("qui", None),
        ("bisa", None),
        ("bis a", None),
        ("logement1", None),
        ("1", None),
    ],
)
def test_the_dvf_suffix_of_a_ban_rep(rep, code) -> None:
    assert ventes.suffixe_dvf(rep) == code


def test_the_suffix_key_of_an_address_without_suffix_wants_no_suffix() -> None:
    cle = ventes.cle_adresse_dvf([_ban()], "numero_et_suffixe")
    assert cle == ventes.CleAdresseDvf(
        motif=None,
        commune="85193",
        numero="2",
        voies=("AVENUE DES CHATAIGNIERS", "AV DES CHATAIGNIERS"),
        suffixe="",
    )


@pytest.mark.parametrize(("rep", "code"), [("bis", "B"), ("Ter", "T"), ("c", "C")])
def test_the_suffix_key_of_a_bis_address(rep, code) -> None:
    cle = ventes.cle_adresse_dvf([_ban(rep=rep), _ban(rep=rep)], "numero_et_suffixe")
    assert (cle.motif, cle.numero, cle.suffixe) == (None, "2", code)


@pytest.mark.parametrize(
    ("rows", "motif"),
    [
        ([_ban(rep="quinquies")], "suffixe_sans_correspondance"),
        ([_ban(rep="logement1")], "suffixe_sans_correspondance"),
        ([_ban(rep="bis", numero="2A")], "sans_numero"),
        ([_ban(numero="0")], "sans_numero"),
        ([_ban(), _ban(rep="bis")], "adresse_indeterminee"),
        ([_ban(rep="bis"), _ban(rep="Bis")], "adresse_indeterminee"),
        ([_ban(rep="bis", voie_normalisee=None)], "adresse_indeterminee"),
    ],
)
def test_a_suffix_key_not_compared_with_dvf_says_why(rows, motif) -> None:
    cle = ventes.cle_adresse_dvf(rows, "numero_et_suffixe")
    assert (cle.motif, cle.numero, cle.suffixe) == (motif, None, None)


def test_the_default_rule_never_compares_a_suffix() -> None:
    assert ventes.cle_adresse_dvf([_ban()]).suffixe is None
    assert ventes.cle_adresse_dvf([_ban(rep="quinquies")]).motif == (
        "suffixe_non_distingue"
    )


@pytest.mark.parametrize(
    ("voisines", "motif"),
    [
        ([], None),
        ([_ban(rep="ter"), _ban(rep="a")], None),
        # « 2 bis » and « 2 b » of one street both read DVF « 2 B »: neither
        # takes it, never resolved by picking one.
        ([_ban(rep="b")], "suffixe_sans_correspondance"),
        ([_ban(rep="bis")], "suffixe_sans_correspondance"),
        ([_ban(rep="logement1"), _ban(rep="B")], "suffixe_sans_correspondance"),
        # Codex review I1: the neighbours are keyed as the attachment keys
        # them: « 02 b » reads DVF « 2 », « AV DES CHATAIGNIERS » is a form of
        # « AVENUE DES CHATAIGNIERS ».
        ([_ban(numero="02", rep="b")], "suffixe_sans_correspondance"),
        (
            [_ban(rep="b", voie_normalisee="AV DES CHATAIGNIERS")],
            "suffixe_sans_correspondance",
        ),
        # Another number, street or commune, or a number DVF cannot read.
        ([_ban(numero="3", rep="b")], None),
        ([_ban(numero="2a", rep="b")], None),
        ([_ban(rep="b", voie_normalisee="AVENUE DES TILLEULS")], None),
        ([_ban(rep="b", code_commune="85194")], None),
    ],
)
def test_a_dvf_suffix_read_by_two_ban_addresses_is_attached_to_neither(
    voisines, motif
) -> None:
    cle = ventes.cle_adresse_dvf([_ban(rep="bis")], "numero_et_suffixe")
    assert ventes.sans_ambiguite(cle, voisines).motif == motif


async def test_declared_sales_out_of_coverage_read_nothing() -> None:
    for absent in ("ban", "dvf"):
        resultat = await ventes.lire_ventes_adresse(
            None, "85193_0901_00002", 1, None, _hosting(**{absent: NON_HEBERGE})
        )
        assert (resultat.etat, resultat.motif) == (
            "out_of_coverage",
            "dataset_non_heberge",
        )
        assert resultat.rattachement == "adresse_declaree_vente"
        assert resultat.total is None and resultat.ventes == []


# --- Phase 04.4, D-03: a mutation of unknown position is a statistic only -------


def _instructions_servies() -> list[str]:
    paires = [*ventes._REQUETES_DECLAREES.values(), *ventes._REQUETES.values()]
    return [str(ventes._PAGE_PARCELLE_SQL), *(str(s) for p in paires for s in p)]


def test_every_list_of_the_address_page_excludes_the_mutations_of_unknown_position():
    instructions = _instructions_servies()

    # The parcel page, four declared and four similar (count, page) pairs.
    assert len(instructions) == 1 + 2 * 4 + 2 * 4
    for instruction in instructions:
        assert POSITION_CONNUE_SQL in instruction


def test_the_isolation_counts_the_positioned_mutations_only():
    """R*112 A-3 LPF: a mutation of unknown position never makes a recent sale
    « not isolated », so it never gets a price published."""
    instruction = str(ventes._ISOLEMENT_SQL)
    interieur = instruction.split("CROSS JOIN LATERAL", 1)[1]

    assert "mutations_grain" in interieur
    assert POSITION_CONNUE_SQL in interieur
