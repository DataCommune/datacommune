"""SQL regressions for the DPE document measurement on disposable PostGIS.

Run only through ``scripts/run_contract_integration.ps1``.  The module owns an
isolated child database and never reads ``DATABASE_URL`` or production data.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from scripts import mesure_dpe_documents as mesure


@pytest.fixture()
def measurement_conn(isolated_pg_dsn: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(isolated_pg_dsn, row_factory=dict_row, connect_timeout=5)
    conn.execute(
        """
        CREATE TEMP TABLE diagnostics_dpe (
            code_departement text NOT NULL, regime_dpe text, type_batiment text,
            is_tombstone boolean DEFAULT false, position geometry(Point, 4326),
            numero_dpe text NOT NULL, identifiant_ban text,
            date_etablissement_dpe date, date_fin_validite_dpe date,
            lifecycle_facts jsonb DEFAULT '{}'::jsonb, adresse_brut text,
            adresse_complete_brut text, complement_adresse_logement text,
            code_postal_brut text, nom_commune_brut text,
            id_rnb text, numero_dpe_remplace text, etiquette_dpe text,
            surface_habitable numeric, provenance_id_rnb text
        );
        CREATE TEMP TABLE adresses (
            id bigint, code_departement text, code_commune text, numero text,
            rep text, nom_voie text, code_postal text, ban_id text,
            position geometry(Point, 4326)
        );
        CREATE TEMP TABLE parcelles (
            id bigint, id_parcelle text, code_departement text,
            geometrie geometry(Polygon, 4326)
        );
        CREATE TEMP TABLE batiments (
            id bigint, code_departement text, usage_1 text,
            geometrie geometry(Polygon, 4326)
        );
        """
    )
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _address(
    conn: psycopg.Connection,
    *,
    dep: str = "44",
    commune: str = "44109",
    ban_id: str = "44109_a001_00012",
    numero: str = "12",
    rep: str | None = None,
    voie: str = "Rue du Port",
    row_id: int = 1,
) -> None:
    conn.execute(
        """INSERT INTO adresses
        (id, code_departement, code_commune, numero, rep, nom_voie,
         code_postal, ban_id, position)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s,
                ST_SetSRID(ST_Point(-1.55, 47.21), 4326))""",
        (row_id, dep, commune, numero, rep, voie, dep + "000", ban_id),
    )


def _document(
    conn: psycopg.Connection,
    numero_dpe: str,
    *,
    dep: str = "44",
    ban_id: str = "44109_a001_00012",
    adresse: str | None = "12 rue du Port",
    complete: str | None = None,
    regime: str = "existant",
    building: str = "maison",
    established: date | None = date(2024, 1, 1),
) -> None:
    conn.execute(
        """INSERT INTO diagnostics_dpe
        (code_departement, regime_dpe, type_batiment, position, numero_dpe,
         identifiant_ban, date_etablissement_dpe, adresse_brut,
         adresse_complete_brut)
        VALUES (%s, %s, %s, ST_SetSRID(ST_Point(-1.55, 47.21), 4326),
                %s, %s, %s, %s, %s)""",
        (dep, regime, building, numero_dpe, ban_id, established, adresse, complete),
    )


def _facts(conn: psycopg.Connection, sql: str, params: dict[str, object]) -> list[dict]:
    return list(
        conn.execute(
            sql,
            {
                "date_import": date(2026, 1, 1),
                "date_mesure": date(2026, 1, 2),
                **params,
            },
        ).fetchall()
    )


@pytest.mark.parametrize(
    ("raw_address", "expected_concordance", "expected_iv"),
    [
        ("12 rue du Port", "concordant", 1),
        ("12 bis rue du Port", "ecart", 0),
        ("12 rue du Portail", "ecart", 0),
        ("12 bis rue du Portail", "ecart", 0),
        ("12-14 rue du Port", "non_evaluable", 0),
    ],
)
def test_concordance_exige_numero_suffixe_et_voie_complete(
    measurement_conn: psycopg.Connection,
    raw_address: str,
    expected_concordance: str,
    expected_iv: int,
) -> None:
    _address(measurement_conn, rep=None, voie="Rue du Port")
    measurement_conn.execute(
        """INSERT INTO parcelles VALUES
        (1, 'P1', '44', ST_MakeEnvelope(-1.56, 47.20, -1.54, 47.22, 4326));
        INSERT INTO batiments VALUES
        (1, '44', 'Résidentiel',
         ST_MakeEnvelope(-1.551, 47.209, -1.549, 47.211, 4326));"""
    )
    _document(measurement_conn, "DOC-A", adresse=raw_address)
    row = _facts(
        measurement_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, {"departements": ["44"]}
    )[0]
    assert row["concordance_adresse"] == expected_concordance
    assert (
        mesure.agreger_maisons_d110([row])["44"]["iv_cumul_adresse_concordante"]
        == expected_iv
    )


@pytest.mark.parametrize(
    ("raw_address", "complete_address"),
    [(None, "12 rue du Port appartement 4"), ("12 rue du Port appartement 4", None)],
)
def test_complement_est_null_safe_quand_un_seul_champ_brut_est_renseigne(
    measurement_conn: psycopg.Connection,
    raw_address: str | None,
    complete_address: str | None,
) -> None:
    _address(measurement_conn)
    _document(
        measurement_conn,
        "DOC-B",
        adresse=raw_address,
        complete=complete_address,
    )
    row = _facts(
        measurement_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, {"departements": ["44"]}
    )[0]
    assert row["complement_brut"] is True


def test_capture_partielle_choisit_un_representant_capture_et_garde_unicite_globale(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn)
    _document(measurement_conn, "A-HORS-CAPTURE")
    _document(measurement_conn, "B-DANS-CAPTURE")
    rows = _facts(
        measurement_conn,
        mesure.FAITS_DOCUMENTS_CAPTURE_SQL,
        {"numeros": ["B-DANS-CAPTURE"]},
    )
    assert len(rows) == 1
    assert rows[0]["classe_docs_cle"] == "2-5"
    assert rows[0]["premier_de_cle"] is True


def test_representants_par_regime_comptent_une_meme_cle_dans_chaque_regime(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn)
    _document(measurement_conn, "A-EXISTANT", regime="existant")
    _document(measurement_conn, "B-NEUF", regime="neuf")
    rows = _facts(
        measurement_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, {"departements": ["44"]}
    )
    assert sum(row["premier_de_cle"] for row in rows) == 1
    assert sum(row["premier_de_cle_regime"] for row in rows) == 2
    par_cle = mesure.agreger_documents_par_cle(rows)["44"]
    assert par_cle["adresses_avec_dpe"] == 1
    assert par_cle["par_regime"]["existant"]["adresses_avec_dpe"] == 1
    assert par_cle["par_regime"]["neuf"]["adresses_avec_dpe"] == 1
    assert par_cle["par_regime"]["existant"]["histogramme"]["1"] == 1
    assert par_cle["par_regime"]["neuf"]["histogramme"]["1"] == 1


def test_parts_i_ii_sur_adresses_viennent_de_representants_sql_distincts(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn, ban_id="44109_a001_00012", row_id=1)
    _address(
        measurement_conn,
        ban_id="44109_a001_00014",
        numero="14",
        row_id=2,
    )
    measurement_conn.execute(
        """INSERT INTO parcelles VALUES
        (1, 'P1', '44', ST_MakeEnvelope(-1.56, 47.20, -1.54, 47.22, 4326));
        INSERT INTO batiments VALUES
        (1, '44', 'Résidentiel',
         ST_MakeEnvelope(-1.551, 47.209, -1.549, 47.211, 4326));"""
    )
    _document(measurement_conn, "A-APPARTEMENT", building="appartement")
    _document(measurement_conn, "B-MAISON-PARTAGEE")
    _document(
        measurement_conn,
        "C-MAISON-UNIQUE",
        ban_id="44109_a001_00014",
        adresse="14 rue du Port",
    )
    rows = _facts(
        measurement_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, {"departements": ["44"]}
    )
    d110 = mesure.agreger_maisons_d110(rows)["44"]
    assert d110["adresses_avec_dpe"] == 2
    assert d110["i_adresses_distinctes"] == 1
    assert d110["ii_adresses_distinctes"] == 2
    assert d110["parts"]["i_sur_adresses"] == 0.5
    assert d110["parts"]["ii_sur_adresses"] == 1.0


def test_faits_restent_bornes_au_departement_et_ne_multiplient_pas_les_doublons_ban(
    measurement_conn: psycopg.Connection,
) -> None:
    shared = "shared_a001_00012"
    _address(measurement_conn, dep="44", ban_id=shared, row_id=1)
    _address(measurement_conn, dep="44", ban_id=shared, row_id=2)
    _address(measurement_conn, dep="85", commune="85191", ban_id=shared, row_id=3)
    _document(measurement_conn, "DOC-44", dep="44", ban_id=shared)
    _document(measurement_conn, "DOC-85", dep="85", ban_id=shared)
    rows = _facts(
        measurement_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, {"departements": ["44"]}
    )
    assert sum(row["effectif"] for row in rows) == 1
    assert {row["code_departement"] for row in rows} == {"44"}
    assert rows[0]["classe_docs_cle"] == "1"


def test_oracle_sql_detecte_mutations_de_filtre_et_de_jointure_departementale(
    measurement_conn: psycopg.Connection,
) -> None:
    shared = "shared_a001_00012"
    _address(measurement_conn, dep="44", ban_id=shared, row_id=1)
    _address(measurement_conn, dep="85", commune="85191", ban_id=shared, row_id=2)
    _document(measurement_conn, "DOC-44", dep="44", ban_id=shared)
    _document(measurement_conn, "DOC-85", dep="85", ban_id=shared)

    def assert_oracle(rows: list[dict]) -> None:
        assert sum(row["effectif"] for row in rows) == 1
        assert {row["code_departement"] for row in rows} == {"44"}
        assert rows[0]["classe_docs_cle"] == "1"

    params = {"departements": ["44"]}
    assert_oracle(_facts(measurement_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, params))
    filtre_mute = mesure.FAITS_DOCUMENTS_BASE_SQL.replace(
        "d.code_departement = ANY(%(departements)s)", "TRUE", 1
    )
    with pytest.raises(AssertionError):
        assert_oracle(_facts(measurement_conn, filtre_mute, params))
    jointure_mute = mesure.FAITS_DOCUMENTS_BASE_SQL.replace(
        "AND d.code_departement = k.code_departement", "", 1
    )
    with pytest.raises(AssertionError):
        assert_oracle(_facts(measurement_conn, jointure_mute, params))


def test_requete_ban_puis_classement_dedoublonnent_un_candidat_identique(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn, rep="bis", voie="Rue de l'Église", row_id=1)
    _address(measurement_conn, rep="bis", voie="Rue de l'Église", row_id=2)
    rows = list(
        measurement_conn.execute(
            mesure.ADRESSES_COMMUNES_SQL,
            {"departement": "44", "communes": ["44109"]},
        ).fetchall()
    )
    index = mesure.indexer_adresses(rows, {("44109", "rue de l eglise")})
    classe = mesure.classer_document(
        {
            "identifiant_ban": "44109",
            "adresse_brut": "12 bis rue de l eglise",
            "code_postal_brut": "44000",
        },
        index,
    )
    assert classe["verdict"] == "correspondance_unique"


def test_suffixe_contradictoire_est_detecte_depuis_les_requetes_sql(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(
        measurement_conn,
        ban_id="44109_a001_00012_bis",
        rep="bis",
        voie="Rue de l'Église",
    )
    _document(
        measurement_conn,
        "DOC-SUFFIXE",
        ban_id="44109_a001_00012_ter",
        adresse="12 bis rue de l'Église",
    )
    documents = list(
        measurement_conn.execute(
            mesure.DOCUMENTS_ORPHELINS_SQL, {"departements": ["44"]}
        ).fetchall()
    )
    adresses = list(
        measurement_conn.execute(
            mesure.ADRESSES_COMMUNES_SQL,
            {"departement": "44", "communes": ["44109"]},
        ).fetchall()
    )
    index = mesure.indexer_adresses(adresses, {("44109", "rue de l eglise")})
    classe = mesure.classer_document(documents[0], index)
    assert classe["verdict"] == "correspondance_unique"
    assert classe["contradiction_suffixe"] is True
    resultat, cles = mesure.agreger_recuperables([("44", classe)])
    assert resultat["44"]["numerotee"]["unique_sans_contradiction"] == 0
    assert cles["44:numerotee"] == set()


def test_formes_sql_ventile_documents_et_cles_liees_sans_multiplier_total_ban(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn)
    _document(measurement_conn, "0044A0000001B", regime="existant")
    _document(measurement_conn, "0044A0000002B", regime="neuf")
    params = {"departements": ["44"]}
    numeros = list(
        measurement_conn.execute(mesure.FORMES_NUMERO_DPE_SQL, params).fetchall()
    )
    cles = list(measurement_conn.execute(mesure.FORMES_CLES_BAN_SQL, params).fetchall())
    assert {row["regime"] for row in numeros} == {"existant", "neuf"}
    assert {
        row["regime"]
        for row in cles
        if row["origine"] == "adresses" and row["regime"] != "tous"
    } == {"existant", "neuf"}
    formes = mesure.agreger_formes(numeros, cles)
    assert formes["numero_dpe"]["44"]["total"] == 2
    assert formes["numero_dpe"]["44"]["par_regime"]["neuf"]["total"] == 1
    assert formes["cles_ban"]["adresses"]["44"]["total"] == 1


def test_d109_distingue_sans_date_d_une_egalite_reelle(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn)
    _document(measurement_conn, "DOC-NULL-1", established=None)
    _document(measurement_conn, "DOC-NULL-2", established=None)
    rows = list(
        measurement_conn.execute(mesure.D109_SQL, {"departements": ["44"]}).fetchall()
    )
    assert len(rows) == 1
    assert rows[0]["code_departement"] == "44"
    assert rows[0]["plusieurs"] is True
    assert rows[0]["recent_unique"] is False
    assert rows[0]["type_recent"] == "sans_date"
    assert rows[0]["effectif"] == 1
    aggregated = mesure.agreger_d109(rows)["44"]
    assert aggregated.get("plus_recent_a_egalite_de_date", 0) == 0


def test_d109_compte_une_vraie_egalite_de_dates(
    measurement_conn: psycopg.Connection,
) -> None:
    _address(measurement_conn)
    _document(measurement_conn, "DOC-EGAL-1", established=date(2024, 2, 1))
    _document(measurement_conn, "DOC-EGAL-2", established=date(2024, 2, 1))
    rows = list(
        measurement_conn.execute(mesure.D109_SQL, {"departements": ["44"]}).fetchall()
    )
    assert len(rows) == 1
    assert rows[0]["type_recent"] == "maison"
    assert rows[0]["recent_unique"] is False
    aggregated = mesure.agreger_d109(rows)["44"]
    assert aggregated["plus_recent_a_egalite_de_date"] == 1


# --- Identity audit (plan 04-14) -------------------------------------------------
# Real production column types, so psycopg returns what the work base run reads
# (Decimal with its scale, char(1), date, integer, jsonb, boolean).


@pytest.fixture()
def identity_conn(isolated_pg_dsn: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(isolated_pg_dsn, row_factory=dict_row, connect_timeout=5)
    conn.execute(
        """
        CREATE TEMP TABLE diagnostics_dpe (
            id bigint, numero_dpe text NOT NULL,
            code_departement varchar(3) NOT NULL, date_etablissement_dpe date,
            annee_construction integer, type_batiment varchar,
            surface_habitable numeric(8, 2), etiquette_dpe character(1),
            etiquette_ges character(1), type_installation_chauffage varchar,
            conso_5_usages_ep numeric(10, 2), emission_ges_5_usages numeric(10, 2),
            code_postal varchar, code_insee varchar, nom_commune text,
            longitude double precision, latitude double precision,
            position geometry(Point, 4326), periode_construction varchar,
            cout_total_5_usages numeric(12, 2),
            type_energie_principale_chauffage varchar,
            qualite_isolation_enveloppe varchar, numero_etage_appartement integer,
            complement_adresse_logement text, identifiant_ban varchar,
            numero_dpe_remplace text, date_fin_validite_dpe date, id_rnb varchar,
            provenance_id_rnb text, score_ban numeric, adresse_brut text,
            adresse_complete_brut text, nom_commune_brut text,
            code_postal_brut text, adresse_ban text, numero_voie_ban text,
            nom_rue_ban text, statut_geocodage text, regime_dpe text,
            source_observations jsonb, lifecycle_facts jsonb,
            is_tombstone boolean DEFAULT false,
            -- Migration 100_dpe_source_columns (plan 04-15).
            version_dpe text, date_derniere_modification_dpe text,
            date_reception_dpe text, date_visite_diagnostiqueur text,
            conso_5_usages_par_m2_ep numeric, emission_ges_5_usages_par_m2 numeric,
            numero_dpe_immeuble_associe text,
            numero_immatriculation_copropriete text
        );
        CREATE TEMP TABLE adresses (
            id bigint, code_departement text, code_commune text, numero text,
            rep text, nom_voie text, code_postal text, ban_id text,
            position geometry(Point, 4326)
        );
        CREATE TEMP TABLE parcelles (
            id bigint, id_parcelle text, code_departement text, commune text,
            updated date, geometrie geometry(Polygon, 4326)
        );
        CREATE TEMP TABLE batiments (
            id bigint, code_departement text, usage_1 text,
            geometrie geometry(Polygon, 4326)
        );
        CREATE TEMP TABLE rnb_liens (
            id bigint, id_rnb varchar, cle_ban varchar, code_departement varchar,
            statut varchar, parcelles text[], millesime date
        );
        CREATE TEMP TABLE etl_runs (
            id bigint, dataset_name varchar, started_at timestamptz,
            completed_at timestamptz, status varchar, total_rows_loaded bigint
        );
        """
    )
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


_OBSERVATION_G1 = (
    '[{"source_slug": "dpe03existant", "import_id": "g1",'
    ' "source_version": "v1", "observed_at": "2026-09-25T20:30:05Z"}]'
)


def _dpe_identite(
    conn: psycopg.Connection,
    numero: str,
    *,
    dep: str = "44",
    surface: str | None = "80.56",
    rnb: str | None = None,
    provenance: str | None = None,
    ban_id: str = "44109_a001_00012",
    observations: str = _OBSERVATION_G1,
) -> None:
    conn.execute(
        """INSERT INTO diagnostics_dpe
        (numero_dpe, code_departement, date_etablissement_dpe, annee_construction,
         type_batiment, surface_habitable, etiquette_dpe, identifiant_ban, id_rnb,
         provenance_id_rnb, adresse_brut, position, regime_dpe,
         source_observations, lifecycle_facts)
        VALUES (%s, %s, DATE '2024-01-02', 1975, 'maison', %s, 'D', %s, %s, %s,
                '12 rue du Port', ST_SetSRID(ST_Point(-1.55, 47.21), 4326),
                'existant', %s::jsonb, '{"date_issues": []}'::jsonb)""",
        (numero, dep, surface, ban_id, rnb, provenance, observations),
    )


def test_projection_stockee_relue_avec_ses_types_et_classee(
    identity_conn: psycopg.Connection,
) -> None:
    _dpe_identite(identity_conn, "DOC-A", rnb="RNB1", provenance="Logiciel")
    # Plan 04-15: source columns stored as published (text, exact numeric).
    identity_conn.execute(
        "UPDATE diagnostics_dpe SET version_dpe = '2.4', "
        "conso_5_usages_par_m2_ep = 226.10 WHERE numero_dpe = 'DOC-A'"
    )
    rows = list(
        identity_conn.execute(
            mesure.LIGNES_DPE_DEPARTEMENT_SQL, {"departement": "44"}
        ).fetchall()
    )
    assert len(rows) == 1
    stockee = rows[0]
    assert stockee["a_position"] is True
    assert stockee["date_issues"] == []
    source = dict.fromkeys(mesure.CHAMPS_SELECTIONNES, "")
    source.update(
        numero_dpe="DOC-A",
        date_etablissement_dpe="2024-01-02",
        annee_construction="1975",
        type_batiment="maison",
        surface_habitable_logement="80.555",
        etiquette_dpe="D",
        code_departement_ban="44",
        identifiant_ban="44109_a001_00012",
        id_rnb="RNB1",
        provenance_id_rnb="Logiciel",
        adresse_brut="12 rue du Port",
        coordonnee_cartographique_x_ban="355000",
        coordonnee_cartographique_y_ban="6690000",
        version_dpe="2.4",
        conso_5_usages_par_m2_ep="226.10",
    )
    bloc = mesure.comparer_generation(
        {"dpe03existant": [source]}, {r["numero_dpe"]: r for r in rows}
    )
    champs = bloc["par_source"]["dpe03existant"]["champs"]
    assert champs["surface_habitable_logement"] == {"arrondi": 1}
    assert champs["etiquette_dpe"] == {"identique": 1}
    assert champs["annee_construction"] == {"identique": 1}
    assert champs["date_etablissement_dpe"] == {"identique": 1}
    assert champs["coordonnee_cartographique_x_ban"] == {"presence_conforme": 1}
    assert champs["adresse_complete_brut"] == {"vides": 1}
    assert champs["id_rnb"] == {"identique": 1}
    assert champs["version_dpe"] == {"identique": 1}
    assert champs["conso_5_usages_par_m2_ep"] == {"identique": 1}
    assert champs["numero_dpe_immeuble_associe"] == {"vides": 1}
    assert bloc["retrouves_en_base"] == 1
    assert bloc["en_base_hors_archive"] == 0


def test_generations_comptent_observations_et_documents(
    identity_conn: psycopg.Connection,
) -> None:
    _dpe_identite(identity_conn, "DOC-A")
    _dpe_identite(identity_conn, "DOC-SANS", observations="[]")
    _dpe_identite(
        identity_conn,
        "DOC-DEUX",
        observations='[{"source_slug": "dpe03existant", "import_id": "g1"},'
        ' {"source_slug": "dpe03existant", "import_id": "g1"}]',
    )
    _dpe_identite(identity_conn, "DOC-85", dep="85")
    rows = list(
        identity_conn.execute(
            mesure.GENERATIONS_DPE_SQL, {"departements": ["44"]}
        ).fetchall()
    )
    sans = [r for r in rows if r["import_id"] is None]
    assert [(r["effectif"], r["documents"]) for r in sans] == [(1, 1)]
    g1 = [r for r in rows if r["import_id"] == "g1"]
    assert sum(r["effectif"] for r in g1) == 3
    assert sum(r["documents"] for r in g1) == 2
    assert {r["code_departement"] for r in rows} == {"44"}


def test_lignee_rnb_mesure_l_accord_avec_rnb_liens(
    identity_conn: psycopg.Connection,
) -> None:
    _address(identity_conn)
    _dpe_identite(identity_conn, "DOC-REPRISE", rnb="RNB1", provenance="Reprise RNB")
    _dpe_identite(identity_conn, "DOC-LOGICIEL", rnb="RNB9", provenance="Logiciel")
    _dpe_identite(identity_conn, "DOC-SANS")
    identity_conn.execute(
        """INSERT INTO rnb_liens VALUES
        (1, 'RNB1', '44109_a001_00012', '44', 'constructed', ARRAY['P1'],
         DATE '2026-09-20'),
        (2, 'RNB9', '44109_a001_00099', '44', 'constructed', ARRAY['P2'],
         DATE '2026-09-20')"""
    )
    rows = list(
        identity_conn.execute(
            mesure.RNB_LIGNEE_SQL, {"departements": ["44"]}
        ).fetchall()
    )
    rnb = mesure.agreger_rnb_lignee(rows)["departements"]["44"]
    reprise = rnb["Reprise RNB"]["id_rnb_lie_a_la_cle_ban"]
    assert (reprise["numerateur"], reprise["denominateur"]) == (1, 1)
    logiciel = rnb["Logiciel"]
    assert logiciel["id_rnb_lie_a_la_cle_ban"]["numerateur"] == 0
    assert logiciel["id_rnb_lie_a_la_cle_ban"]["denominateur"] == 1
    assert logiciel["id_rnb_connu_de_rnb_liens"]["numerateur"] == 1
    assert rnb["(null)"]["id_rnb_renseigne"]["numerateur"] == 0
    assert rnb["(null)"]["documents"] == 1


def test_adresses_parcelle_rnb_point_distinct_et_liens_construits(
    identity_conn: psycopg.Connection,
) -> None:
    _address(identity_conn, ban_id="k-in", row_id=1)
    _address(identity_conn, ban_id="k-in", row_id=2)  # duplicate BAN row
    identity_conn.execute(
        """INSERT INTO adresses VALUES
        (3, '44', '44109', '1', NULL, 'Rue', '44000', 'k-out',
         ST_SetSRID(ST_Point(-1.70, 47.30), 4326)),
        (4, '44', '44109', '2', NULL, 'Rue', '44000', 'k-nopos', NULL);
        INSERT INTO parcelles VALUES
        (1, 'P1', '44', '44109', DATE '2026-06-05',
         ST_MakeEnvelope(-1.56, 47.20, -1.54, 47.22, 4326)),
        (2, 'P2', '44', '44109', DATE '2026-06-05',
         ST_MakeEnvelope(-1.60, 47.20, -1.58, 47.22, 4326));
        INSERT INTO rnb_liens VALUES
        (1, 'R1', 'k-in', '44', 'constructed', ARRAY['P1'], DATE '2026-09-20'),
        (2, 'R2', 'k-in', '44', 'demolished', ARRAY['P2'], DATE '2026-09-20'),
        (3, 'R3', 'k-out', '44', 'constructed', ARRAY['P2', 'P9'],
         DATE '2026-09-20')"""
    )
    rows = {
        r["ban_id"]: r
        for r in identity_conn.execute(
            mesure.ADRESSES_PARCELLE_RNB_SQL, {"departement": "44"}
        ).fetchall()
    }
    assert set(rows) == {"k-in", "k-out", "k-nopos"}
    assert rows["k-in"]["point_dans_parcelle"] is True
    assert rows["k-in"]["n_batiments_rnb"] == 1
    assert rows["k-in"]["n_parcelles_rnb"] == 1
    assert rows["k-in"]["point_dans_parcelles_rnb"] is True
    assert rows["k-out"]["point_dans_parcelle"] is False
    assert rows["k-out"]["n_parcelles_rnb"] == 2
    assert rows["k-out"]["n_parcelles_rnb_locales"] == 1
    assert rows["k-out"]["point_dans_parcelles_rnb"] is False
    assert rows["k-nopos"]["a_position"] is False
    assert rows["k-nopos"]["point_dans_parcelle"] is False
    parcelles = list(
        identity_conn.execute(
            mesure.PARCELLES_DEPARTEMENT_SQL, {"departement": "44"}
        ).fetchall()
    )
    bloc = mesure.agreger_adresses_parcelle(
        "44",
        rows.values(),
        {"k-in": (["P1"], "entrée"), "k-out": (["P9"], "parcelle")},
        {p["id_parcelle"] for p in parcelles},
        {p["commune"] for p in parcelles},
        None,
        None,
    )
    assert bloc["point_hors_parcelle"]["numerateur"] == 1
    assert bloc["point_hors_parcelle"]["denominateur"] == 2
    assert bloc["sans_position"] == 1
    assert (
        bloc["hors_parcelle_par_cause"]["parcelle_citee_absente_du_cadastre_local"] == 1
    )
    assert bloc["source_archivee_pour_les_causes_cadastre"]["etat"] == "non_mesure"
    millesimes = list(
        identity_conn.execute(
            mesure.MILLESIMES_SQL, {"departements": ["44"]}
        ).fetchall()
    )
    assert {(m["origine"], m["effectif"]) for m in millesimes} == {
        ("parcelles", 2),
        ("rnb_liens", 3),
    }


def test_variantes_usage_null_changent_seulement_le_compte_hors_annexes(
    identity_conn: psycopg.Connection,
) -> None:
    _address(identity_conn)
    identity_conn.execute(
        """INSERT INTO parcelles VALUES
        (1, 'P1', '44', '44109', NULL,
         ST_MakeEnvelope(-1.56, 47.20, -1.54, 47.22, 4326));
        INSERT INTO batiments VALUES
        (1, '44', 'Résidentiel',
         ST_MakeEnvelope(-1.551, 47.209, -1.549, 47.211, 4326)),
        (2, '44', NULL, ST_MakeEnvelope(-1.545, 47.205, -1.544, 47.206, 4326)),
        (3, '44', 'Annexe', ST_MakeEnvelope(-1.548, 47.214, -1.547, 47.215, 4326))"""
    )
    _document(identity_conn, "DOC-A")
    params = {"departements": ["44"]}
    decidee = _facts(identity_conn, mesure.FAITS_DOCUMENTS_BASE_SQL, params)
    attendu = {
        "usage_null_exclu_du_compte": "1",
        "aucun_constat_si_usage_null": "2+",
        "aucun_constat_si_usage_null_ou_indifferencie": "2+",
    }
    # decided rule: the NULL usage counts as a building, so two non-annex ones
    assert decidee[0]["batiments_hors_annexe"] == "2+"
    for nom, sql in mesure.VARIANTES_USAGE.items():
        rows = _facts(identity_conn, sql, params)
        assert rows[0]["batiments_hors_annexe"] == attendu[nom], nom
        for cle in ("concordance_adresse", "batiments_parcelle", "classe_docs_cle"):
            assert rows[0][cle] == decidee[0][cle], (nom, cle)
    usages = list(identity_conn.execute(mesure.USAGES_BATIMENTS_SQL, params).fetchall())
    assert {(u["usage"], u["effectif"]) for u in usages} == {
        ("(null)", 1),
        ("Annexe", 1),
        ("Résidentiel", 1),
    }


def test_runs_etl_relus_dans_l_ordre(identity_conn: psycopg.Connection) -> None:
    identity_conn.execute(
        """INSERT INTO etl_runs VALUES
        (29, 'dpe', now(), now(), 'success', 545030),
        (30, 'vacance', now(), now(), 'success', 1),
        (32, 'ban', now(), NULL, 'running', NULL)"""
    )
    runs = mesure._runs(identity_conn)
    assert [(r["dataset"], r["id"], r["statut"]) for r in runs] == [
        ("dpe", 29, "success"),
        ("ban", 32, "running"),
    ]
