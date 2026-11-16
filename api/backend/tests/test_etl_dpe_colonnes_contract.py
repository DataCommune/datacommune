"""Unit tests: etl_dpe Phase 58 -- 8 raw ADEME address columns + regime_dpe.

Phase 58 (Rattachement des maisons, bug Reddit n. 3), D-02/D-16/D-14: locks
the 4-list positional contract (`API_SELECT` / `EXPECTED_COLUMNS` /
`COLUMN_MAPPING` / `OUTPUT_COLUMNS`) the same way R4-01 locked its own 3
columns, and guards against the exact regression 58-RESEARCH.md Pitfall 5
warns about -- a mid-list insertion that silently shifts a prior append
(here, R4-01's `id_rnb`/`provenance_id_rnb`/`score_ban` at [25:28]) while the
COPY still succeeds with wrong data.

Pattern: test_etl_dpe_rnb_columns.py (itself following
test_etl_dpe_logement_columns.py) -- this is the 3rd link of the same chain,
not a new one.

The pipeline (`DpePipeline.stage`/`swap`) is wired to these 8 columns by
plan 58-04: `TestPipelineCableColonnesBrutes` asserts GREEN now -- the
`xfail(strict=True)` markers that guarded the gap until this plan have been
removed.
"""

import csv
import inspect
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from app.etl_dpe import config
from app.etl_dpe.pipeline import DpePipeline
from tests.test_etl_dpe_sources import transform_fixture


@pytest.fixture(autouse=True)
def _dpe_scope(bind_departements):
    # DPE reads its departements from the bound instance scope (44/85,
    # within the migration 091 lock), for direct calls and runners alike.
    bind_departements(("44", "85"), DpePipeline)


COLONNES_BRUTES = (
    "adresse_brut",
    "adresse_complete_brut",
    "nom_commune_brut",
    "code_postal_brut",
    "adresse_ban",
    "numero_voie_ban",
    "nom_rue_ban",
    "statut_geocodage",
)
COLONNE_REGIME = "regime_dpe"


class TestConfigColonnesBrutes:
    """The 8 Phase 58 raw columns are present in the 3 ADEME-facing lists,
    regime_dpe (pipeline-computed, D-14) is absent from all 3, and the
    OUTPUT_COLUMNS positional contract -- including R4-01's untouched
    offsets -- holds."""

    @pytest.mark.parametrize("nom", COLONNES_BRUTES)
    def test_colonne_dans_api_select(self, nom):
        assert nom in config.API_SELECT.split(",")

    @pytest.mark.parametrize("nom", COLONNES_BRUTES)
    def test_colonne_dans_expected_columns(self, nom):
        assert nom in config.EXPECTED_COLUMNS

    @pytest.mark.parametrize("nom", COLONNES_BRUTES)
    def test_colonne_dans_column_mapping(self, nom):
        assert nom in config.COLUMN_MAPPING

    def test_regime_dpe_absent_des_listes_ademe(self):
        """regime_dpe is computed by the pipeline (58-04), never requested
        from or mapped from the ADEME API."""
        assert COLONNE_REGIME not in config.API_SELECT.split(",")
        assert COLONNE_REGIME not in config.EXPECTED_COLUMNS
        assert COLONNE_REGIME not in config.COLUMN_MAPPING

    def test_colonnes_appendees_a_la_fin_de_output_columns(self):
        """OUTPUT_COLUMNS is a positional contract with transform() -- the 9
        new entries (8 raw columns + regime_dpe) must be the LAST 9, in this
        order."""
        assert config.OUTPUT_COLUMNS[28:37] == [*COLONNES_BRUTES, COLONNE_REGIME]

    def test_output_columns_positions_r4_01_inchangees(self):
        """The regression this test exists to catch: a Phase 58 column
        inserted before id_rnb would silently shift R4-01's offsets while
        the COPY still succeeds (Pitfall 5)."""
        assert config.OUTPUT_COLUMNS[25:28] == [
            "id_rnb",
            "provenance_id_rnb",
            "score_ban",
        ]


class TestPerimetreSource:
    """Both measured sources are active; derived slugs remain unsupported."""

    def test_etl_dpe_sources_deux_sources_mesurees(self):
        assert config.ETL_DPE_SOURCES == ("dpe03existant", "dpe02neuf")

    def test_api_base_url_dpe02neuf(self):
        """dpe02neuf (the CORRECT slug, P-1) is nameable without being
        activated."""
        assert "dpe02neuf" in config.api_base_url("dpe02neuf")

    def test_slug_errone_absent_hors_commentaire(self):
        """The wrong slug (P-1) must not appear in any non-comment line.
        Built by concatenation on purpose: this test must not itself
        inscribe the literal the gate tracks."""
        slug_errone = "dpe0" + "3" + "neuf"
        source = inspect.getsource(config)
        lignes_actives = [
            ligne for ligne in source.splitlines() if not ligne.strip().startswith("#")
        ]
        assert slug_errone not in "\n".join(lignes_actives)


class TestPipelineCableColonnesBrutes:
    """stage() and swap() wire the 8 raw columns end to end -- câblé par le
    plan 58-04, vert."""

    @pytest.mark.parametrize("nom", COLONNES_BRUTES)
    def test_colonne_dans_stage(self, nom):
        source = inspect.getsource(DpePipeline.stage)
        assert nom in source, f"{nom} missing from stage()"

    @pytest.mark.parametrize("nom", COLONNES_BRUTES)
    def test_colonne_dans_swap(self, nom):
        source = inspect.getsource(DpePipeline.swap)
        assert nom in source, f"{nom} missing from swap()"

    def test_swap_insert_et_select_meme_nombre_de_colonnes(self):
        """The INSERT column list and the SELECT expression list of swap()
        must stay aligned POSITIONALLY -- prove it by counting rather than
        by visual re-reading (a silent count mismatch is exactly Pitfall 5)."""
        source = inspect.getsource(DpePipeline.swap)
        insert_start = source.index("INSERT INTO _dpe_incoming")
        insert_cols_start = source.index("(", insert_start)
        insert_cols_end = source.index(")", insert_cols_start)
        insert_cols = source[insert_cols_start + 1 : insert_cols_end]
        n_insert = len([c for c in insert_cols.split(",") if c.strip()])

        select_start = source.index("SELECT", insert_cols_end)
        from_start = source.index("FROM (", select_start)
        select_body = source[select_start + len("SELECT") : from_start]
        # Strip SQL comment lines (-- ...) before counting top-level commas:
        # comments may themselves contain commas (prose), which would
        # inflate the count independently of the actual expression list.
        lignes_sans_commentaire = [
            ligne
            for ligne in select_body.splitlines()
            if not ligne.strip().startswith("--")
        ]
        select_body_nu = "\n".join(lignes_sans_commentaire)
        # Expressions can span multiple lines (LEAST(...)::numeric(8, 2));
        # count top-level commas by tracking parenthesis depth.
        depth = 0
        n_select = 1
        for char in select_body_nu:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                n_select += 1

        assert n_insert == n_select, (
            f"INSERT declares {n_insert} columns but SELECT yields "
            f"{n_select} expressions -- positional mismatch (Pitfall 5)"
        )


def test_swap_ne_caste_jamais_code_postal_brut_en_integer():
    """Pitfall 8: code_postal_brut is TEXT end to end. A cast to ::integer
    would silently drop the leading zero of postal codes below 10000. This
    test must be GREEN now (nothing to cast yet) and stay GREEN after 58-04
    wires swap() -- unlike the pipeline-wiring class above, this is not an
    xfail."""
    source = inspect.getsource(DpePipeline.swap)
    assert "code_postal_brut, '')::integer" not in source
    assert "code_postal_brut::integer" not in source


class TestTransformColonnesBrutes:
    """transform() carries the 8 raw columns + regime_dpe positionally,
    empty-safe, and survives a CSV round-trip on a MULTILINE adresse_brut
    (Pitfall 7). Pattern: TestTransformRnb (test_etl_dpe_rnb_columns.py)."""

    def _write_fixture(
        self, tmp_path: Path, *, avec_brutes: bool, adresse_multiligne: bool = False
    ) -> Path:
        """One valid row (Lambert coords, dept 44) with/without the 8 new
        fields, optionally with a literal `\\n` inside adresse_brut."""
        row = dict.fromkeys(config.EXPECTED_COLUMNS, "")
        row.update(
            {
                "numero_dpe": "2444E0000002X",
                "date_etablissement_dpe": "2024-01-15",
                "type_batiment": "maison",
                "code_departement_ban": "44",
                "code_insee_ban": "44109",
                "coordonnee_cartographique_x_ban": "354780.58",
                "coordonnee_cartographique_y_ban": "6690061.58",
            }
        )
        if avec_brutes:
            adresse_brut = (
                "3 RUE\nDES LILAS" if adresse_multiligne else "3 RUE DES LILAS"
            )
            row.update(
                {
                    "adresse_brut": adresse_brut,
                    "adresse_complete_brut": f"{adresse_brut} 44100 NANTES",
                    "nom_commune_brut": "NANTES",
                    "code_postal_brut": "44100",
                    "adresse_ban": "3 Rue des Lilas",
                    "numero_voie_ban": "3",
                    "nom_rue_ban": "Rue des Lilas",
                    "statut_geocodage": "ok",
                }
            )
        suffix = "avec" if avec_brutes else "sans"
        path = tmp_path / f"dpe_test_brutes_{suffix}.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=config.EXPECTED_COLUMNS)
            writer.writeheader()
            writer.writerow(row)
        return path

    def _transform(
        self, source: Path, transform_source: str = "dpe03existant"
    ) -> list[dict]:
        out_path = transform_fixture(source, transform_source)
        with open(out_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == config.OUTPUT_COLUMNS
            return list(reader)

    def test_transform_ecrit_les_champs_aux_bonnes_positions(self, tmp_path):
        rows = self._transform(self._write_fixture(tmp_path, avec_brutes=True))

        assert len(rows) == 1
        assert rows[0]["adresse_brut"] == "3 RUE DES LILAS"
        assert rows[0]["adresse_complete_brut"] == "3 RUE DES LILAS 44100 NANTES"
        assert rows[0]["nom_commune_brut"] == "NANTES"
        assert rows[0]["code_postal_brut"] == "44100"
        assert rows[0]["adresse_ban"] == "3 Rue des Lilas"
        assert rows[0]["numero_voie_ban"] == "3"
        assert rows[0]["nom_rue_ban"] == "Rue des Lilas"
        assert rows[0]["statut_geocodage"] == "ok"

    def test_transform_sans_les_champs_produit_des_vides(self, tmp_path):
        """Missing/empty source values stay empty strings (NULLIF at swap)."""
        rows = self._transform(self._write_fixture(tmp_path, avec_brutes=False))

        assert len(rows) == 1
        for nom in COLONNES_BRUTES:
            assert rows[0][nom] == ""

    def test_transform_regime_dpe_existant(self, tmp_path):
        """The validated existing-build source yields its exact regime."""
        rows = self._transform(
            self._write_fixture(tmp_path, avec_brutes=True),
            transform_source="dpe03existant",
        )
        assert rows[0]["regime_dpe"] == "existant"

    def test_transform_regime_dpe_neuf(self, tmp_path):
        """The validated new-build source yields its exact regime."""
        rows = self._transform(
            self._write_fixture(tmp_path, avec_brutes=True),
            transform_source="dpe02neuf",
        )
        assert rows[0]["regime_dpe"] == "neuf"

    def test_transform_adresse_brut_multiligne_aller_retour_csv(self, tmp_path):
        """Pitfall 7: a literal `\\n` inside adresse_brut must survive the
        CSV write (transform) + CSV read (this test's own re-read) intact --
        proof that csv.writer's quoting/escaping is not corrupted by any
        line-based post-processing between transform() and the read here."""
        rows = self._transform(
            self._write_fixture(tmp_path, avec_brutes=True, adresse_multiligne=True)
        )

        assert len(rows) == 1
        assert rows[0]["adresse_brut"] == "3 RUE\nDES LILAS"


LIFECYCLE_COLUMNS = [
    "source_observations",
    "source_state",
    "source_snapshot_at",
    "is_tombstone",
    "lifecycle_facts",
    "revision_evidence",
]


def test_lifecycle_columns_append_after_existing_contract():
    # Fixed offsets: plan 04-15 appends its columns after this block.
    assert config.OUTPUT_COLUMNS[37:43] == LIFECYCLE_COLUMNS
    assert config.OUTPUT_COLUMNS[36] == "regime_dpe"


def test_transform_emits_current_typed_facts_and_invalid_date_flags(
    monkeypatch, tmp_path
):
    from tests.test_etl_dpe_sources import install_source_api

    install_source_api(
        monkeypatch,
        tmp_path,
        row_override={
            "date_etablissement_dpe": "2024-02-30",
            "date_fin_validite_dpe": None,
        },
    )
    pipeline = DpePipeline()
    paths = pipeline.download()
    with pipeline.transform(paths[0]).open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    row = rows[0]
    assert row["date_etablissement_dpe"] == ""
    assert row["date_fin_validite_dpe"] == ""
    assert row["source_state"] == "present"
    assert row["is_tombstone"] == "false"
    assert json.loads(row["lifecycle_facts"]) == {
        "replacement_state": "not_checked",
        "graph_issues": [],
        "date_issues": ["invalid_establishment_date"],
    }
    observations = json.loads(row["source_observations"])
    assert observations[0]["import_id"] == pipeline.import_manifest.import_id
    assert json.loads(row["revision_evidence"])["status"] == "unverified"
    pipeline.close()


def test_presence_is_ready_before_transform_and_restored_for_resume(
    monkeypatch, tmp_path
):
    from tests.test_etl_dpe_sources import install_source_api

    install_source_api(
        monkeypatch,
        tmp_path,
        row_override={
            "coordonnee_cartographique_x_ban": None,
        },
    )
    pipeline = DpePipeline()
    pipeline.download()
    number = next(pipeline.import_manifest.files[0].source_rows())["numero_dpe"]
    assert number in pipeline._presence_registry
    # Plan 02-16: counted as kept without geometry (four pairs, one row each).
    assert pipeline.stage_counters() == {"documents_sans_geometrie": 4}
    with pipeline.transform(Path(pipeline.import_manifest.files[0].path)).open(
        encoding="utf-8", newline=""
    ) as stream:
        kept = list(csv.DictReader(stream))
    assert [row["numero_dpe"] for row in kept] == [number]
    assert kept[0]["longitude"] == kept[0]["latitude"] == ""
    manifest = pipeline.import_manifest
    pipeline.close()
    resumed = DpePipeline()
    resumed._import_id = manifest.import_id
    resumed._import_manifest = manifest
    resumed.download()
    assert number in resumed._presence_registry
    assert resumed.stage_counters() == {"documents_sans_geometrie": 4}
    resumed.close()


def test_stage_refuses_forged_lifecycle_json_before_connect(monkeypatch, tmp_path):
    from tests.test_etl_dpe_sources import install_source_api

    install_source_api(monkeypatch, tmp_path)
    pipeline = DpePipeline()
    path = pipeline.transform(pipeline.download()[0])
    with path.open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    rows[0]["source_observations"] = "{}"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=config.OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    monkeypatch.setattr(
        "app.etl_dpe.pipeline.psycopg.connect",
        lambda *a, **k: pytest.fail("COPY must not start"),
    )
    with pytest.raises(ValueError, match="lifecycle"):
        pipeline.stage(path)
    assert pipeline._presence_registry is None


@pytest.mark.parametrize(
    ("x", "y", "invalid"),
    [
        (None, 10, True),
        ("", 10, True),
        ("bad", 10, True),
        ("NaN", 10, True),
        ("inf", 10, True),
        (0, 0, True),
        (0, 10, False),
        (10, 0, False),
        (354780.58, 6690061.58, False),
    ],
)
def test_geometry_absence_keeps_existing_exclusive_rules(x, y, invalid):
    assert (
        DpePipeline._invalid_geometry(
            {
                "coordonnee_cartographique_x_ban": x,
                "coordonnee_cartographique_y_ban": y,
            }
        )
        is invalid
    )


# --- Plan 04-15 (owner decisions C1 to C3, 04-DECISIONS-COLONNES-DPE.md) -----
# C1: four fields requested and archived but dropped before staging (the only
# import loss 04-14 proved). C2: ADEME per-m2 values of landmarks 1 and 2.
# C3: identifiers stored, never served. C4/C5 deliberately not requested.
COLONNES_C1 = (
    "version_dpe",
    "date_derniere_modification_dpe",
    "date_reception_dpe",
    "date_visite_diagnostiqueur",
)
COLONNES_C2 = ("conso_5_usages_par_m2_ep", "emission_ges_5_usages_par_m2")
COLONNES_C3 = ("numero_dpe_immeuble_associe", "numero_immatriculation_copropriete")
COLONNES_0415 = (*COLONNES_C1, *COLONNES_C2, *COLONNES_C3)
# Source lexemes as data-fair serves them (JSON numbers decoded as Decimal
# keep their exact digits; dates are ISO strings).
VALEURS_0415 = {
    "version_dpe": "2.4",
    "date_derniere_modification_dpe": "2024-05-02",
    "date_reception_dpe": "2024-04-30",
    "date_visite_diagnostiqueur": "2024-04-12",
    "conso_5_usages_par_m2_ep": "226.10",
    "emission_ges_5_usages_par_m2": "43.5",
    "numero_dpe_immeuble_associe": "2444E0000099Z",
    "numero_immatriculation_copropriete": "AB1234567",
}


def test_0415_columns_are_requested_once_and_appended_at_the_end():
    selected = config.API_SELECT.split(",")
    assert selected[-4:] == [*COLONNES_C2, *COLONNES_C3]
    assert config.EXPECTED_COLUMNS[-4:] == [*COLONNES_C2, *COLONNES_C3]
    for nom in COLONNES_0415:
        assert selected.count(nom) == config.EXPECTED_COLUMNS.count(nom) == 1
        assert config.COLUMN_MAPPING[nom] == nom
    for nom in ("numero_rpls_logement", "complement_adresse_batiment"):
        assert nom not in selected and nom not in config.COLUMN_MAPPING
    assert len(selected) == len(config.EXPECTED_COLUMNS) == 44


def test_0415_columns_follow_the_lifecycle_block_of_output_columns():
    # Positional contract with transform(): appended, never inserted.
    assert config.OUTPUT_COLUMNS[36] == "regime_dpe"
    assert config.OUTPUT_COLUMNS[37:43] == LIFECYCLE_COLUMNS
    assert config.OUTPUT_COLUMNS[43:] == list(COLONNES_0415)


def test_0415_declared_source_types_match_the_measured_ademe_schema():
    # Types read in the ADEME metadata of 2026-10-06 (04-14 evidence).
    for nom in ("version_dpe", *COLONNES_C2):
        assert config.SOURCE_FIELD_TYPES[nom] == "number"
    for nom in COLONNES_C3:
        assert config.SOURCE_FIELD_TYPES[nom] == "string"
        assert nom not in config.SOURCE_FIELD_FORMATS


def test_0415_transform_keeps_the_source_values_instead_of_dropping_them(
    monkeypatch, tmp_path
):
    """Red before 04-15: C1 values reach the archive and are dropped by
    transform(); C2/C3 values are not even requested from the source."""
    from tests.test_etl_dpe_sources import install_source_api

    install_source_api(monkeypatch, tmp_path, row_override=dict(VALEURS_0415))
    pipeline = DpePipeline()
    try:
        paths = pipeline.download()
        source = next(pipeline.import_manifest.files[0].source_rows())
        archived = {nom: source.get(nom) for nom in COLONNES_0415}
        with pipeline.transform(paths[0]).open(encoding="utf-8", newline="") as f:
            [row] = list(csv.DictReader(f))
    finally:
        pipeline.close()
    assert {nom: str(valeur) for nom, valeur in archived.items()} == VALEURS_0415
    assert {nom: row.get(nom) for nom in COLONNES_0415} == VALEURS_0415


def test_0415_missing_source_values_stay_empty_not_zero(monkeypatch, tmp_path):
    from tests.test_etl_dpe_sources import install_source_api

    install_source_api(monkeypatch, tmp_path, row_override=dict.fromkeys(COLONNES_0415))
    pipeline = DpePipeline()
    try:
        paths = pipeline.download()
        with pipeline.transform(paths[0]).open(encoding="utf-8", newline="") as f:
            [row] = list(csv.DictReader(f))
    finally:
        pipeline.close()
    assert {nom: row[nom] for nom in COLONNES_0415} == dict.fromkeys(COLONNES_0415, "")


def test_0415_swap_stores_source_text_and_unbounded_numbers():
    """C1 and C3 stay the published text (no cast that could fail or lose a
    value); C2 is an exact numeric, neither bounded nor rounded."""
    source = " ".join(inspect.getsource(DpePipeline.swap).split())
    for nom in (*COLONNES_C1, *COLONNES_C3):
        assert f"NULLIF({nom}, '')" in source
        assert f"{nom}, '')::" not in source
    for nom in COLONNES_C2:
        assert f"NULLIF({nom}, '')::numeric," in source
        assert f"{nom}::numeric, " not in source
    stage = inspect.getsource(DpePipeline.stage)
    for nom in COLONNES_0415:
        assert f"{nom} TEXT" in stage
