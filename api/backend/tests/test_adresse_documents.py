"""Declared DPE documents of an address, without a database (04-03).

The list is chronological and cut by ten in SQL, the card serves the source
number as is (never an opaque reference), and every calendar validity comes
from :func:`app.dpe.lifecycle.validity_at` (D-109, D-111, DPE-01).
"""

from dataclasses import replace
from datetime import date

import pytest
from pydantic import BaseModel

from app.adresse import documents, relations, schemas
from app.common.semantic_contract import get_field
from app.dpe import queries
from app.dpe.concordance import FAITS_DOCUMENTS_SQL
from app.dpe.public_projection import AdresseBruteDocument, IdentiteBan
from app.dpe.queries import DpeDocumentAdresse

pytestmark = pytest.mark.unit

AS_OF = date(2026, 10, 2)


def _doc(
    numero: str,
    etabli: date | None,
    fin: date | None = None,
    *,
    document_id: int = 1,
    classe: str | None = "C",
    ges: str | None = "B",
    remplacement: str = "no_successor_known",
    graphe: tuple[str, ...] = (),
    dates: tuple[str, ...] = (),
) -> DpeDocumentAdresse:
    if fin is None and etabli is not None:
        fin = date(etabli.year + 10, etabli.month, etabli.day)
    return DpeDocumentAdresse(
        document_id=document_id,
        numero_dpe=numero,
        type_batiment="maison",
        surface_habitable=82.5,
        etiquette_dpe=classe,
        etiquette_ges=ges,
        date_etablissement_dpe=etabli,
        date_fin_validite_dpe=fin,
        lifecycle_facts={
            "replacement_state": remplacement,
            "graph_issues": list(graphe),
            "date_issues": list(dates),
        },
    )


# --- slicing by ten -----------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "pages"), [(0, 1), (1, 1), (10, 1), (11, 2), (20, 2), (23, 3)]
)
def test_pages_are_cut_by_ten(total, pages):
    assert documents.PAR_PAGE == 10
    assert documents.nombre_pages(total) == pages


@pytest.mark.parametrize(("page", "offset"), [(1, 0), (2, 10), (3, 20)])
def test_offset_of_a_page(page, offset):
    assert documents.decalage(page) == offset


class _Capture:
    """Fake session recording the one statement a reader sends."""

    def __init__(self) -> None:
        self.sql = ""
        self.params: dict = {}

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        self.params = dict(params or {})

        class _Result:
            def mappings(self):
                class _Rows:
                    def all(self):
                        return []

                return _Rows()

        return _Result()


async def test_chronological_reader_orders_before_limit_and_offset():
    session = _Capture()

    rows = await queries.dpe_documents_adresse_chronologiques(
        session, ban_id="85191_0420_00012", department="85", limit=10, offset=10
    )

    assert rows == []
    sql = " ".join(session.sql.split())
    order = sql.index(
        "ORDER BY d.date_etablissement_dpe DESC NULLS LAST, d.numero_dpe, d.id"
    )
    assert order < sql.index("LIMIT :limit OFFSET :offset")
    # Exact source key and partition, retired documents excluded (N-11).
    assert "d.identifiant_ban = :ban_id" in sql
    assert "d.code_departement = :department" in sql
    assert "NOT d.is_tombstone" in sql
    assert session.params == {
        "ban_id": "85191_0420_00012",
        "department": "85",
        "limit": 10,
        "offset": 10,
    }


# --- the source number, never an opaque reference ----------------------------


def test_card_serves_the_source_number_as_is():
    doc = _doc("2385E0123456A", date(2024, 3, 1))
    statut = documents.statut_document(doc, AS_OF)

    carte = documents.carte_document(doc, statut, badges_selection=())

    assert carte.numero == "2385E0123456A"
    dumped = carte.model_dump_json()
    assert "document_ref" not in dumped and "context_ref" not in dumped


def _properties(model: type[BaseModel]) -> set[str]:
    names: set[str] = set()
    stack: list = [model.model_json_schema()]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if isinstance(node.get("properties"), dict):
                names.update(node["properties"])
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return names


def test_no_address_document_model_carries_an_opaque_reference():
    for model in (schemas.DocumentDpeCarte, schemas.DocumentsDpeAdresse):
        assert not _properties(model) & {"document_ref", "context_ref", "id"}


def test_unknown_or_malformed_class_stays_unknown():
    carte = documents.carte_document(
        _doc("2385E0123456B", date(2024, 3, 1), classe=" x", ges=None),
        documents.statut_document(_doc("2385E0123456B", date(2024, 3, 1)), AS_OF),
        badges_selection=(),
    )
    assert carte.etiquette_dpe is None and carte.etiquette_ges is None


# --- validity: lifecycle.py only ---------------------------------------------


def test_calendar_validity_is_read_from_lifecycle(monkeypatch):
    calls: list[tuple] = []

    def spy(established, end, as_of, *, date_issues=()):
        calls.append((established, end, as_of, tuple(date_issues)))
        return "expired"

    monkeypatch.setattr(documents, "validity_at", spy)

    statut = documents.statut_document(_doc("N1", date(2024, 3, 1)), AS_OF)

    assert calls == [(date(2024, 3, 1), date(2034, 3, 1), AS_OF, ())]
    assert statut.validite == "expired"
    assert not statut.eligible_valide


def test_most_recent_valid_document_is_selected():
    docs = [
        _doc("ANCIEN", date(2020, 5, 4), document_id=1),
        _doc("VALIDE-2022", date(2022, 6, 1), document_id=2),
        _doc("VALIDE-2024", date(2024, 3, 1), document_id=3, classe="B"),
    ]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert selection.etat == "unique"
    assert selection.document is not None
    assert selection.document.numero == "VALIDE-2024"
    assert selection.document.etiquette_dpe == "B"
    assert selection.date == date(2024, 3, 1)
    assert selection.motif == "document_valide"


# --- Task 2: global selection D-109 / D-111 -----------------------------------


def _numeros(selection) -> list[str]:
    return [doc.numero for doc in selection.documents_ex_aequo]


def test_a_valid_document_wins_over_a_more_recent_expired_one():
    docs = [
        _doc("EXPIRE", date(2025, 6, 1), date(2026, 1, 1), document_id=1),
        _doc("VALIDE", date(2023, 2, 1), document_id=2),
        _doc("ANCIEN", date(2021, 3, 1), document_id=3),
    ]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert selection.etat == "unique"
    assert selection.document.numero == "VALIDE"
    assert selection.motif == "document_valide"


def test_a_valid_document_wins_over_a_more_recent_replaced_one():
    docs = [
        _doc("REMPLACE", date(2025, 6, 1), remplacement="replaced"),
        _doc("AMBIGU", date(2025, 5, 1), remplacement="ambiguous"),
        _doc("NON-VERIFIE", date(2025, 4, 1), remplacement="not_checked"),
        _doc("FOURCHE", date(2025, 3, 1), graphe=("fork",)),
        _doc("VALIDE", date(2023, 2, 1)),
    ]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert (selection.etat, selection.document.numero) == ("unique", "VALIDE")


def test_former_method_only_gives_the_latest_never_called_valid():
    docs = [
        _doc("ANCIEN-2019", date(2019, 1, 10)),
        _doc("ANCIEN-2021", date(2021, 6, 30)),
    ]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert selection.etat == "unique"
    assert selection.document.numero == "ANCIEN-2021"
    assert selection.motif == "ancienne_methode"


def test_expired_only_gives_the_latest_with_its_status():
    docs = [_doc("EXPIRE", date(2022, 1, 1), date(2024, 1, 1))]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert (selection.etat, selection.motif) == ("unique", "expire")


def test_uncertain_replacement_only_is_not_a_valid_document():
    docs = [_doc("AMBIGU", date(2024, 1, 1), remplacement="ambiguous")]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert (selection.etat, selection.motif) == ("unique", "remplacement_incertain")


def test_missing_end_date_is_an_unknown_validity():
    doc = replace(_doc("SANS-FIN", date(2024, 1, 1)), date_fin_validite_dpe=None)

    selection = documents.choisir_plus_recent([doc], AS_OF)

    assert (selection.etat, selection.motif) == ("unique", "validite_inconnue")


@pytest.mark.parametrize(
    ("doc", "badge"),
    [
        (_doc("FUTUR", date(2027, 1, 1)), "etablissement_futur"),
        (_doc("INVERSE", date(2024, 1, 1), date(2023, 1, 1)), "dates_incoherentes"),
        (
            _doc("DATE-INVALIDE", None, date(2030, 1, 1), dates=("invalid_end_date",)),
            "dates_incoherentes",
        ),
    ],
)
def test_future_or_inconsistent_dates_are_explicit_and_never_selected(doc, badge):
    statut = documents.statut_document(doc, AS_OF)
    carte = documents.carte_document(doc, statut, badges_selection=())

    assert badge in carte.badges
    assert not statut.eligible_valide
    selection = documents.choisir_plus_recent([doc], AS_OF)
    assert (selection.etat, selection.motif) == ("indeterminee", "aucun_document_date")
    assert selection.document is None and selection.date is None


def test_no_dated_candidate_is_indeterminate():
    selection = documents.choisir_plus_recent([_doc("SANS-DATE", None, None)], AS_OF)

    assert (selection.etat, selection.motif) == ("indeterminee", "aucun_document_date")


def test_a_dated_document_wins_over_a_future_one_when_none_is_valid():
    docs = [_doc("FUTUR", date(2027, 1, 1)), _doc("ANCIEN", date(2020, 1, 1))]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert (selection.etat, selection.document.numero) == ("unique", "ANCIEN")
    assert selection.motif == "ancienne_methode"


def test_no_document_is_indeterminate():
    selection = documents.choisir_plus_recent([], AS_OF)

    assert (selection.etat, selection.motif) == ("indeterminee", "aucun_document")


def test_an_incomplete_population_is_never_selected():
    selection = documents.choisir_plus_recent(
        [_doc("VALIDE", date(2024, 1, 1))], AS_OF, complete=False
    )

    assert (selection.etat, selection.motif) == (
        "indeterminee",
        "population_incomplete",
    )


def test_a_tie_at_the_latest_date_chooses_no_document():
    docs = [
        _doc("B-VALIDE", date(2024, 3, 1), classe="D", document_id=2),
        _doc("A-VALIDE", date(2024, 3, 1), classe=None, document_id=1),
        _doc("PLUS-ANCIEN", date(2023, 1, 1), document_id=3),
    ]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert selection.etat == "egalite"
    assert selection.document is None
    assert selection.date == date(2024, 3, 1)
    assert _numeros(selection) == ["A-VALIDE", "B-VALIDE"]
    # A missing class stays unknown, never borrowed from the other document.
    assert [doc.etiquette_dpe for doc in selection.documents_ex_aequo] == [None, "D"]
    assert selection.motif == "document_valide"
    badges = documents.badges_de_selection(selection)
    assert badges == {
        "A-VALIDE": "meme_date_plus_recente",
        "B-VALIDE": "meme_date_plus_recente",
    }
    assert "plus_recent" not in badges.values()


def test_a_tie_among_non_valid_documents_says_so():
    docs = [_doc("A", date(2020, 1, 1)), _doc("B", date(2020, 1, 1))]

    selection = documents.choisir_plus_recent(docs, AS_OF)

    assert (selection.etat, selection.motif) == ("egalite", "aucun_document_valide")
    assert _numeros(selection) == ["A", "B"]


def test_the_page_tie_breaker_never_breaks_a_selection_tie():
    """Order of the input (the page order) never changes the selection."""
    docs = [
        _doc("Z", date(2024, 3, 1), document_id=1),
        _doc("A", date(2024, 3, 1), document_id=9),
    ]

    assert documents.choisir_plus_recent(docs, AS_OF) == documents.choisir_plus_recent(
        list(reversed(docs)), AS_OF
    )


def test_the_unique_selection_badges_only_its_document():
    selection = documents.choisir_plus_recent([_doc("SEUL", date(2024, 3, 1))], AS_OF)

    assert documents.badges_de_selection(selection) == {"SEUL": "plus_recent"}


# --- 04-19: declared address of a card (P-ADR-01, LIEU-03, N-12) ---------------

GLYCINES = IdentiteBan(nom_voie="Rue des Glycines", nom_commune="La Roche-sur-Yon")
FORBIDDEN_RAW_PROPERTIES = {
    "adresse_brut",
    "adresse_complete_brut",
    "complement_adresse_logement",
    "nom_commune_brut",
    "code_postal_brut",
}


def _faits(concordance: str, brut: str | None, complement: str | None = None):
    return documents.FaitsAdresseDocument(
        concordance=concordance,
        brute=AdresseBruteDocument(
            adresse_brut=brut,
            complements={"complement_adresse_logement": complement},
        ),
    )


def test_a_strictly_concordant_document_has_no_line_four():
    faits = _faits("concordant", "12 rue des Glycines")

    assert documents.adresse_declaree_de_carte(faits, GLYCINES) is None


def test_a_divergent_address_established_without_complement_is_published():
    faits = _faits("ecart", "14 rue des Glycines")

    adresse = documents.adresse_declaree_de_carte(faits, GLYCINES)

    assert adresse == schemas.AdresseDeclaree(
        etat="publiee", texte="14 rue des Glycines"
    )


def test_a_non_evaluable_concordance_still_goes_through_the_projection():
    faits = _faits("non_evaluable", "12-14 rue des Glycines")

    adresse = documents.adresse_declaree_de_carte(faits, GLYCINES)

    assert adresse.etat == "masquee"
    assert adresse.motif == "non_verifiable_sans_complement"


def test_facts_not_read_mask_the_field_rather_than_publish_it():
    adresse = documents.adresse_declaree_de_carte(None, GLYCINES)

    assert adresse.etat == "masquee" and adresse.texte is None


def test_the_card_carries_the_projection_and_never_a_raw_column():
    doc = _doc("2385E0123456C", date(2024, 3, 1))
    faits = _faits("ecart", "14 rue des Glycines", complement="porte SENTINELLE")

    carte = documents.carte_document(
        doc,
        documents.statut_document(doc, AS_OF),
        badges_selection=(),
        adresse_declaree=documents.adresse_declaree_de_carte(faits, GLYCINES),
    )

    assert carte.adresse_declaree.motif == "complement_present"
    assert "SENTINELLE" not in carte.model_dump_json()
    for model in (schemas.DocumentDpeCarte, schemas.DocumentsDpeAdresse):
        assert not _properties(model) & FORBIDDEN_RAW_PROPERTIES


def test_identity_of_contradictory_ban_rows_is_unknown():
    class Row:
        def __init__(self, voie, code_postal="85000"):
            self.nom_voie, self.nom_commune = voie, "La Roche-sur-Yon"
            self.code_postal = code_postal

    assert documents.identite_ban([Row("Rue A"), Row("Rue A")]).nom_voie == "Rue A"
    assert documents.identite_ban([Row("Rue A"), Row("Rue B")]).nom_voie is None
    # The BAN postcode the projection compares a typed postcode with (n° 25).
    assert documents.identite_ban([Row("Rue A")]).code_postal == "85000"
    assert (
        documents.identite_ban([Row("Rue A"), Row("Rue A", "85001")]).code_postal
        is None
    )


def test_the_facts_query_reads_the_page_documents_of_one_partition():
    sql = " ".join(FAITS_DOCUMENTS_SQL.split())

    assert "WHERE d.id = ANY(:ids) AND d.code_departement = :department" in sql
    # The BAN row of the key is the first one by id, as in the script.
    assert "ORDER BY ad.id LIMIT 1" in sql


def test_the_declared_address_has_its_contract_entry():
    contract = get_field("dpe.document.adresse_declaree")

    assert contract.rattachement == "adresse_saisie_diagnostiqueur"
    assert contract.value_type == "string"


# --- 04-19 Task 2: D-110 finding, served only when every criterion holds -------

UNIQUE_DU_POINT = schemas.RelationParcelle(
    etat="unique",
    id_parcelle="85191000AH0001",
    nombre_candidats=1,
    rattachement="parcelle_du_point",
)


def _faits_d110(**valeurs):
    base = {
        "type_batiment": "maison",
        "documents_presents": 1,
        "documents_de_la_cle": 1,
        "concordance": "concordant",
        "relation": UNIQUE_DU_POINT,
        "batiments": relations.BatimentsParcelle(total=2, hors_annexe=1),
    }
    return documents.FaitsD110(**{**base, **valeurs})


def test_the_finding_is_served_with_its_criteria_and_sources():
    constat = documents.constat_d110(_faits_d110())

    assert constat == schemas.ConstatD110(
        criteres=[
            "maison",
            "document_unique_cle",
            "concordance_stricte",
            "batiment_unique_parcelle_du_point",
            "annexes_exclues_usage_inconnu_compte",
        ],
        sources=[
            documents.source_valeur("dpe"),
            documents.source_valeur("ban"),
            documents.source_valeur("cadastre"),
            documents.source_valeur("batiments"),
        ],
    )


@pytest.mark.parametrize(
    "valeurs",
    [
        {"type_batiment": "appartement"},
        {"type_batiment": None},
        {"documents_presents": 2, "documents_de_la_cle": 2},
        {"documents_de_la_cle": 2},  # one present, one retired
        {"documents_de_la_cle": None},  # count not read
        {"documents_presents": 0, "documents_de_la_cle": 1},  # only one retired
        {"concordance": "ecart"},
        {"concordance": "non_evaluable"},
        {"concordance": None},
        {"relation": None},
        {
            "relation": schemas.RelationParcelle(
                etat="ambigue", nombre_candidats=2, motif="parcelles_multiples"
            )
        },
        {
            "relation": schemas.RelationParcelle(
                etat="point_hors_parcelle", motif="point_hors_parcelle"
            )
        },
        {"relation": schemas.RelationParcelle(etat="indisponible")},
        {"relation": schemas.RelationParcelle(etat="hors_couverture")},
        {
            "relation": schemas.RelationParcelle(
                etat="unique", id_parcelle="85191000AH0001", nombre_candidats=1
            )
        },  # a unique parcel without « parcelle_du_point » is not the rule
        {"batiments": None},  # not hosted, outage or parcel not read
        {"batiments": relations.BatimentsParcelle(total=2, hors_annexe=2)},
        {"batiments": relations.BatimentsParcelle(total=1, hors_annexe=0)},
    ],
)
def test_any_false_or_unknown_criterion_serves_no_finding(valeurs):
    assert documents.constat_d110(_faits_d110(**valeurs)) is None


async def test_buildings_are_counted_on_the_parcel_without_limit():
    session = _Capture()

    compte = await relations.batiments_sur_parcelle(session, "85191000AH0001")

    sql = " ".join(session.sql.split())
    assert "IS DISTINCT FROM 'Annexe'" in sql
    assert "ST_Covers(p.geometrie, ST_PointOnSurface(b.geometrie))" in sql
    assert "LIMIT" not in sql.upper()
    assert session.params == {"id_parcelle": "85191000AH0001", "department": "85"}
    # No parcel row: the count is unknown, never a zero.
    assert compte is None


# --- API WR-04: an undetermined identity carries no D-110 finding --------------


def _ligne_ban(**champs):
    from types import SimpleNamespace

    base = {
        "lon": -1.43,
        "lat": 46.67,
        "numero": "12",
        "rep": None,
        "nom_voie": "Rue des Lilas",
        "code_postal": "85000",
        "code_commune": "85191",
        "nom_commune": "La Roche-sur-Yon",
    }
    return SimpleNamespace(**{**base, **champs})


@pytest.mark.parametrize(
    ("lignes", "attendu"),
    [
        ([_ligne_ban()], True),
        ([_ligne_ban(), _ligne_ban()], True),
        ([_ligne_ban(), _ligne_ban(numero="120")], False),
        ([_ligne_ban(), _ligne_ban(rep="bis")], False),
        ([_ligne_ban(), _ligne_ban(code_postal="85001")], False),
        ([], False),
    ],
    ids=["one", "coherent_pair", "number", "suffix", "postcode", "none"],
)
def test_identity_coherence_compares_every_identity_field(lignes, attendu):
    assert documents.identite_coherente(lignes) is attendu


async def test_no_finding_is_read_for_an_incoherent_identity():
    constat = await documents.lire_constat_d110(
        None,
        ban_id="85191_0420_00012",
        departement="85",
        total=1,
        documents=[],
        faits_adresse={},
        point=relations.PointBan(lon=-1.43, lat=46.67),
        hosting=None,
        identite_coherente=False,
    )

    assert constat is None
