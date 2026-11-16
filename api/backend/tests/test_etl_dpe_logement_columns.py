"""Unit tests: etl_dpe/config.py 4-list coherence for the 5 new logement columns.

Phase 48 Wave 0 (CONTEXT, non-negociable): without numero_etage_appartement,
complement_adresse_logement, identifiant_ban, numero_dpe_remplace and
date_fin_validite_dpe, the logement list is unconstructible. This test proves
the 4 coupled lists (API_SELECT, EXPECTED_COLUMNS, COLUMN_MAPPING,
OUTPUT_COLUMNS) stay coherent end to end, and that the shared read-path
constant DPE_SELECT_COLUMNS (locked by test_dpe_columns_shared.py) is never
touched by this extension.
"""

import inspect

import pytest

pytestmark = pytest.mark.unit

from app.dpe.columns import DPE_SELECT_COLUMNS
from app.etl_dpe import config
from app.etl_dpe.pipeline import DpePipeline

NOUVELLES_COLONNES_ADEME = (
    "numero_etage_appartement",
    "complement_adresse_logement",
    "identifiant_ban",
    "numero_dpe_remplace",
    "date_fin_validite_dpe",
)


class TestListesCoherentes:
    """API_SELECT / EXPECTED_COLUMNS / COLUMN_MAPPING stay consistent."""

    def test_output_columns_has_37_entries(self):
        # 25 (Phase 48) + 3 RNB/BAN confidence columns (R4-01, Plan A'')
        # + 9 raw ADEME address columns + regime_dpe (Phase 58, D-02/D-16/D-14)
        # + 6 lifecycle columns + 8 source columns of plan 04-15 (C1 to C3).
        assert len(config.OUTPUT_COLUMNS) == 51

    def test_api_select_and_expected_columns_same_set(self):
        api_select_names = config.API_SELECT.split(",")
        # 25 + 3 (R4-01) + 8 raw ADEME columns (Phase 58) -- regime_dpe is
        # pipeline-computed, never requested/mapped, so it is NOT in this set.
        # + 4 lifecycle evidence fields + 4 fields of plan 04-15 (C2, C3).
        assert len(api_select_names) == len(config.EXPECTED_COLUMNS) == 44
        assert set(api_select_names) == set(config.EXPECTED_COLUMNS)

    def test_column_mapping_keys_and_values_are_referenced(self):
        for key in config.COLUMN_MAPPING:
            assert key in config.EXPECTED_COLUMNS, f"{key} not in EXPECTED_COLUMNS"
        for value in config.COLUMN_MAPPING.values():
            assert value in config.OUTPUT_COLUMNS, f"{value} not in OUTPUT_COLUMNS"

    def test_output_columns_first_20_unchanged(self):
        """OUTPUT_COLUMNS order is a positional contract with transform() --
        the 5 new columns must be appended, never inserted, so the 20
        existing values keep their offset."""
        premiers_20 = [
            "numero_dpe",
            "code_departement",
            "date_etablissement_dpe",
            "annee_construction",
            "periode_construction",
            "type_batiment",
            "surface_habitable",
            "etiquette_dpe",
            "etiquette_ges",
            "type_installation_chauffage",
            "type_energie_principale_chauffage",
            "conso_5_usages_ep",
            "emission_ges_5_usages",
            "cout_total_5_usages",
            "qualite_isolation_enveloppe",
            "code_postal",
            "code_insee",
            "nom_commune",
            "latitude",
            "longitude",
        ]
        assert config.OUTPUT_COLUMNS[:20] == premiers_20


class TestNouvellesColonnes:
    """The 5 new ADEME logement columns are requested, validated and mapped."""

    @pytest.mark.parametrize("nom", NOUVELLES_COLONNES_ADEME)
    def test_colonne_dans_api_select(self, nom):
        assert nom in config.API_SELECT.split(",")

    @pytest.mark.parametrize("nom", NOUVELLES_COLONNES_ADEME)
    def test_colonne_dans_expected_columns(self, nom):
        assert nom in config.EXPECTED_COLUMNS

    @pytest.mark.parametrize("nom", NOUVELLES_COLONNES_ADEME)
    def test_colonne_dans_column_mapping(self, nom):
        assert nom in config.COLUMN_MAPPING

    @pytest.mark.parametrize("nom", NOUVELLES_COLONNES_ADEME)
    def test_colonne_dans_output_columns(self, nom):
        assert nom in config.OUTPUT_COLUMNS


class TestNonRegressionColonnesPartagees:
    """Guard rails: shared read-path constant and excluded field."""

    def test_complement_adresse_batiment_absent_des_4_listes(self):
        """Pitfall 2: complement_adresse_batiment is a diagnostician folder
        reference, never a logement discriminant -- must never be ingested."""
        assert "complement_adresse_batiment" not in config.API_SELECT
        assert "complement_adresse_batiment" not in config.EXPECTED_COLUMNS
        assert "complement_adresse_batiment" not in config.COLUMN_MAPPING
        assert "complement_adresse_batiment" not in config.OUTPUT_COLUMNS

    def test_dpe_select_columns_untouched(self):
        """DPE_SELECT_COLUMNS (app/dpe/columns.py) is locked by
        test_dpe_columns_shared.py -- the logements endpoint projects its own
        columns, this shared read-path constant must stay exactly as is."""
        for nom in NOUVELLES_COLONNES_ADEME:
            assert nom not in DPE_SELECT_COLUMNS


class TestPipelineCable:
    """stage() and swap() wire the 5 new columns end to end (Task 3)."""

    @pytest.mark.parametrize("nom", NOUVELLES_COLONNES_ADEME)
    def test_colonne_dans_stage(self, nom):
        source = inspect.getsource(DpePipeline.stage)
        assert nom in source, f"{nom} missing from stage()"

    @pytest.mark.parametrize("nom", NOUVELLES_COLONNES_ADEME)
    def test_colonne_dans_swap(self, nom):
        source = inspect.getsource(DpePipeline.swap)
        assert nom in source, f"{nom} missing from swap()"

    def test_swap_cast_date_fin_validite_dpe(self):
        source = inspect.getsource(DpePipeline.swap)
        assert "NULLIF(date_fin_validite_dpe, '')::date" in source

    def test_swap_cast_numero_etage_appartement(self):
        source = inspect.getsource(DpePipeline.swap)
        assert "NULLIF(numero_etage_appartement, '')::integer" in source
