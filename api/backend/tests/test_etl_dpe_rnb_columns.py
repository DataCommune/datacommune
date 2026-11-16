"""Unit tests: etl_dpe R4-01 -- id_rnb / provenance_id_rnb / score_ban.

Plan A'' (RESEARCH-L1-RATTACHEMENT-DPE-2026-07-31 §1.4): the 3 RNB/BAN
confidence columns are requested, validated, mapped, appended at the END of
the positional OUTPUT_COLUMNS contract, and wired end to end through
transform(), stage() and swap(). Pattern: test_etl_dpe_logement_columns.py.
"""

import csv
import inspect
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


COLONNES_RNB = ("id_rnb", "provenance_id_rnb", "score_ban")


class TestNouvellesColonnesRnb:
    """The 3 R4-01 columns are present in the 4 coupled lists."""

    @pytest.mark.parametrize("nom", COLONNES_RNB)
    def test_colonne_dans_api_select(self, nom):
        assert nom in config.API_SELECT.split(",")

    @pytest.mark.parametrize("nom", COLONNES_RNB)
    def test_colonne_dans_expected_columns(self, nom):
        assert nom in config.EXPECTED_COLUMNS

    @pytest.mark.parametrize("nom", COLONNES_RNB)
    def test_colonne_dans_column_mapping(self, nom):
        assert nom in config.COLUMN_MAPPING

    def test_colonnes_a_positions_25_28_inchangees(self):
        """OUTPUT_COLUMNS is a positional contract with transform() -- the 3
        R4-01 columns were appended LAST when this test was written; Phase 58
        (D-02/D-16) appended 9 more after them (58-RESEARCH.md Pitfall 5), so
        the invariant this test locks is now their fixed offset [25:28], not
        "last three". Re-verified independently by
        test_etl_dpe_colonnes_contract.py::test_output_columns_positions_r4_01_inchangees."""
        assert config.OUTPUT_COLUMNS[25:28] == list(COLONNES_RNB)

    def test_output_columns_25_premieres_inchangees(self):
        """Phase 48's 25 entries keep their offsets (positional contract)."""
        assert config.OUTPUT_COLUMNS[20:25] == [
            "numero_etage_appartement",
            "complement_adresse_logement",
            "identifiant_ban",
            "numero_dpe_remplace",
            "date_fin_validite_dpe",
        ]


class TestPipelineCableRnb:
    """stage() and swap() wire the 3 new columns end to end."""

    @pytest.mark.parametrize("nom", COLONNES_RNB)
    def test_colonne_dans_stage(self, nom):
        source = inspect.getsource(DpePipeline.stage)
        assert nom in source, f"{nom} missing from stage()"

    @pytest.mark.parametrize("nom", COLONNES_RNB)
    def test_colonne_dans_swap(self, nom):
        source = inspect.getsource(DpePipeline.swap)
        assert nom in source, f"{nom} missing from swap()"

    def test_swap_cast_score_ban_numeric(self):
        """score_ban is an ADEME float shipped as text -- NULLIF + ::numeric,
        never a raw text insert (Pitfall 3, PLAN-RESTES §6 R4-01)."""
        source = inspect.getsource(DpePipeline.swap)
        assert "NULLIF(score_ban, '')::numeric" in source


class TestTransformRnb:
    """transform() carries the 3 fields positionally, empty-safe."""

    def _write_fixture(self, tmp_path: Path, *, avec_rnb: bool) -> Path:
        """One valid row (Lambert coords, dept 44) with/without the 3 fields."""
        row = dict.fromkeys(config.EXPECTED_COLUMNS, "")
        row.update(
            {
                "numero_dpe": "2444E0000001X",
                "date_etablissement_dpe": "2024-01-15",
                "type_batiment": "appartement",
                "code_departement_ban": "44",
                "code_insee_ban": "44109",
                "coordonnee_cartographique_x_ban": "354780.58",
                "coordonnee_cartographique_y_ban": "6690061.58",
                "identifiant_ban": "44109_zz01_00002",
            }
        )
        if avec_rnb:
            row.update(
                {
                    "id_rnb": "42P8B8HVDY1D",
                    "provenance_id_rnb": "Reprise RNB",
                    "score_ban": "0.98",
                }
            )
        suffix = "avec" if avec_rnb else "sans"
        path = tmp_path / f"dpe_test_rnb_{suffix}.csv"
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=config.EXPECTED_COLUMNS)
            writer.writeheader()
            writer.writerow(row)
        return path

    def _transform(self, source: Path) -> list[dict]:
        out_path = transform_fixture(source)
        with open(out_path, encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == config.OUTPUT_COLUMNS
            return list(reader)

    def test_transform_ecrit_les_3_champs_aux_bonnes_positions(self, tmp_path):
        rows = self._transform(self._write_fixture(tmp_path, avec_rnb=True))

        assert len(rows) == 1
        assert rows[0]["id_rnb"] == "42P8B8HVDY1D"
        assert rows[0]["provenance_id_rnb"] == "Reprise RNB"
        assert rows[0]["score_ban"] == "0.98"
        # Positional contract: values at fixed offset [25:28] of the raw CSV
        # line -- these 3 were the last 3 when this test was written; Phase
        # 58 (D-02/D-16) appended 9 more after them (58-RESEARCH.md
        # Pitfall 5), so the invariant is now their fixed offset, not "last
        # three". Same rewrite as test_colonnes_a_positions_25_28_inchangees.
        with open(
            transform_fixture(self._write_fixture(tmp_path, avec_rnb=True)),
            encoding="utf-8",
        ) as f:
            ligne = list(csv.reader(f))[1]
        assert ligne[25:28] == ["42P8B8HVDY1D", "Reprise RNB", "0.98"]

    def test_transform_sans_les_3_champs_produit_des_vides(self, tmp_path):
        """Missing/empty source values stay empty strings (NULLIF at swap)."""
        rows = self._transform(self._write_fixture(tmp_path, avec_rnb=False))

        assert len(rows) == 1
        assert rows[0]["id_rnb"] == ""
        assert rows[0]["provenance_id_rnb"] == ""
        assert rows[0]["score_ban"] == ""
