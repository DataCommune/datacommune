"""Unit tests for etl_copropriete RNIC mapping -- D-20 (Phase 48).

Covers the parcel derivation (derive_id_parcelle_1), the OUTPUT_COLUMNS /
staging-flat coherence and a non-regression proof that the existing read
path (app/copropriete/queries.py::copropriete_par_codes) is untouched.
"""

import inspect
import typing

import pytest

pytestmark = pytest.mark.unit

from app.etl_copropriete.config import OUTPUT_COLUMNS
from app.etl_copropriete.pipeline import (
    CoproprietePipeline,
    derive_id_parcelle,
    derive_id_parcelle_1,
    normalize_mandat_en_cours,
    parse_date_fin_mandat,
)


class TestDeriveIdParcelle:
    """derive_id_parcelle_1(row) -- 14 chars exact or "" (never guessed)."""

    def test_reference_cadastrale_normalisee(self):
        row = {"reference_cadastrale_1": "44109000EN0179"}
        assert derive_id_parcelle_1(row) == "44109000EN0179"

    def test_reference_cadastrale_avec_espaces_et_tirets(self):
        row = {"reference_cadastrale_1": "44109-000-EN-0179"}
        assert derive_id_parcelle_1(row) == "44109000EN0179"

    def test_reference_cadastrale_minuscule_normalisee_majuscule(self):
        row = {"reference_cadastrale_1": "44109000en0179"}
        assert derive_id_parcelle_1(row) == "44109000EN0179"

    def test_recomposition_depuis_les_parties(self):
        row = {
            "reference_cadastrale_1": "",
            "code_insee_parcelle_1": "44109",
            "prefixe_parcelle_1": "0",
            "section_parcelle_1": "EN",
            "numero_parcelle_1": "179",
        }
        assert derive_id_parcelle_1(row) == "44109000EN0179"

    def test_recomposition_sans_reference_cadastrale_absente(self):
        row = {
            "code_insee_parcelle_1": "85047",
            "prefixe_parcelle_1": "",
            "section_parcelle_1": "AB",
            "numero_parcelle_1": "42",
        }
        assert derive_id_parcelle_1(row) == "85047000AB0042"

    def test_ni_reference_ni_recomposition_retourne_vide(self):
        row = {
            "reference_cadastrale_1": "",
            "code_insee_parcelle_1": "44109",
            "prefixe_parcelle_1": "",
            "section_parcelle_1": "",
            "numero_parcelle_1": "",
        }
        assert derive_id_parcelle_1(row) == ""

    def test_reference_cadastrale_tronquee_retourne_vide_jamais_devinee(self):
        row = {"reference_cadastrale_1": "44109000EN01"}
        assert derive_id_parcelle_1(row) == ""

    def test_ligne_vide_retourne_vide(self):
        assert derive_id_parcelle_1({}) == ""

    def test_sentinelle_non_connu_traitee_comme_vide(self):
        row = {
            "reference_cadastrale_1": "non connu",
            "code_insee_parcelle_1": "44109",
            "prefixe_parcelle_1": "000",
            "section_parcelle_1": "EN",
            "numero_parcelle_1": "0179",
        }
        assert derive_id_parcelle_1(row) == "44109000EN0179"

    def test_sentinelle_non_connu_sur_partie_requise_retourne_vide(self):
        row = {
            "reference_cadastrale_1": "",
            "code_insee_parcelle_1": "44109",
            "prefixe_parcelle_1": "000",
            "section_parcelle_1": "non connu",
            "numero_parcelle_1": "0179",
        }
        assert derive_id_parcelle_1(row) == ""

    def test_prefixe_absent_recompose_avec_zfill_000(self):
        row = {
            "reference_cadastrale_1": "",
            "code_insee_parcelle_1": "44109",
            "prefixe_parcelle_1": "",
            "section_parcelle_1": "EN",
            "numero_parcelle_1": "0179",
        }
        assert derive_id_parcelle_1(row) == "44109000EN0179"


class TestParcellesSecondaires:
    """A registration declares up to three parcels: the second and the third
    are derived by the same rule as the first, never dropped (KI-20)."""

    def test_reference_de_rang_2_et_3(self):
        row = {
            "reference_cadastrale_1": "44109000EN0179",
            "reference_cadastrale_2": "44109-000-EN-0180",
            "reference_cadastrale_3": "44109000en0181",
        }
        assert derive_id_parcelle(row, 2) == "44109000EN0180"
        assert derive_id_parcelle(row, 3) == "44109000EN0181"

    def test_recomposition_du_rang_2_depuis_ses_parties(self):
        row = {
            "reference_cadastrale_2": "",
            "code_insee_parcelle_2": "85047",
            "prefixe_parcelle_2": "",
            "section_parcelle_2": "AB",
            "numero_parcelle_2": "43",
        }
        assert derive_id_parcelle(row, 2) == "85047000AB0043"

    def test_un_rang_absent_reste_vide_et_ne_reprend_jamais_le_rang_1(self):
        row = {"reference_cadastrale_1": "44109000EN0179"}
        assert derive_id_parcelle(row, 2) == ""
        assert derive_id_parcelle(row, 3) == ""

    def test_transform_garde_les_trois_parcelles_et_leur_nombre(
        self, tmp_path, monkeypatch
    ):
        import pandas as pd

        monkeypatch.setattr(
            "app.etl_copropriete.pipeline.get_download_dir", lambda name: tmp_path
        )
        header = [
            *TestTransformCleInsee.SOURCE_HEADER,
            "reference_cadastrale_2",
            "reference_cadastrale_3",
            "nombre_parcelles",
        ]
        lignes = [
            {
                "numero_immatriculation": "AA1-000-010",
                "code_insee_parcelle_1": "85047",
                "reference_cadastrale_1": "85047000AB0042",
                "reference_cadastrale_2": "85047000AB0043",
                "reference_cadastrale_3": "85047000AB0044",
                "nombre_parcelles": "4",
            },
            {
                "numero_immatriculation": "AA1-000-011",
                "code_insee_parcelle_1": "85047",
                "reference_cadastrale_1": "85047000AB0050",
                "nombre_parcelles": "1",
            },
        ]
        source = tmp_path / "rnic.csv"
        pd.DataFrame(lignes, columns=header).fillna("").to_csv(source, index=False)
        out = pd.read_csv(CoproprietePipeline().transform(source), dtype=str).fillna("")
        colonnes = ["id_parcelle_1", "id_parcelle_2", "id_parcelle_3"]
        assert list(out.loc[0, colonnes]) == [
            "85047000AB0042",
            "85047000AB0043",
            "85047000AB0044",
        ]
        assert out.loc[0, "nb_parcelles"] == "4"
        assert list(out.loc[1, colonnes[1:]]) == ["", ""]

    def test_une_cellule_vide_lue_nan_ne_fabrique_jamais_un_identifiant(self):
        # pandas hands an empty cell as NaN: str() made « nan » of it and the
        # recomposition wrote 14 characters of it (« 00NANNANNA0NAN »).
        nan = float("nan")
        row = {
            f"{nom}_{rang}": nan
            for rang in (1, 2, 3)
            for nom in (
                "reference_cadastrale",
                "code_insee_parcelle",
                "prefixe_parcelle",
                "section_parcelle",
                "numero_parcelle",
            )
        }
        assert [derive_id_parcelle(row, rang) for rang in (1, 2, 3)] == ["", "", ""]

    def test_transform_sans_aucune_parcelle_laisse_les_trois_rangs_vides(
        self, tmp_path, monkeypatch
    ):
        helper = TestTransformCleInsee()
        out = helper._transform(
            tmp_path, monkeypatch, [{"numero_immatriculation": "AA1-000-012"}]
        )
        assert list(
            out.loc[0, ["id_parcelle_1", "id_parcelle_2", "id_parcelle_3"]]
        ) == ["", "", ""]

    def test_la_garde_de_schema_surveille_les_rangs_2_et_3(self):
        from app.etl_copropriete.config import SOURCE_COLUMNS

        for col in (
            "reference_cadastrale_2",
            "numero_parcelle_2",
            "reference_cadastrale_3",
            "numero_parcelle_3",
            "nombre_parcelles",
        ):
            assert col in SOURCE_COLUMNS

    def test_staging_et_bascule_portent_les_trois_colonnes(self):
        for methode in (CoproprietePipeline.stage, CoproprietePipeline.swap):
            source = inspect.getsource(methode)
            for col in ("id_parcelle_2", "id_parcelle_3", "nb_parcelles"):
                assert col in source


class TestListesCoherentes:
    """OUTPUT_COLUMNS / staging flat contract (positional with COPY)."""

    def test_output_columns_compte_23(self):
        assert len(OUTPUT_COLUMNS) == 23

    def test_output_columns_finit_par_les_parcelles_secondaires(self):
        # Positional with the flat staging table + COPY: new columns go last.
        assert OUTPUT_COLUMNS[-5:] == [
            "mandat_en_cours",
            "date_fin_dernier_mandat",
            "id_parcelle_2",
            "id_parcelle_3",
            "nb_parcelles",
        ]

    def test_output_columns_contient_les_5_colonnes_neuves(self):
        for col in (
            "code_insee",
            "id_parcelle_1",
            "longitude",
            "latitude",
            "nb_lots_habitation_strict",
        ):
            assert col in OUTPUT_COLUMNS

    def test_staging_flat_declare_les_5_colonnes_neuves(self):
        source = inspect.getsource(CoproprietePipeline.stage)
        for col in (
            "code_insee",
            "id_parcelle_1",
            "longitude",
            "latitude",
            "nb_lots_habitation_strict",
            "mandat_en_cours",
            "date_fin_dernier_mandat",
        ):
            assert col in source


class TestTransformCleInsee:
    """D-20 root-cause fix: the INSEE key comes from code_insee_parcelle_1.

    Measured at the source (2026-07-27 prod run): code_officiel_commune
    carries the POSTAL code in ~89% of RNIC rows. Mapping it to
    code_insee/code_commune reproduces the bug on every reload.
    """

    SOURCE_HEADER: typing.ClassVar[list[str]] = [
        "numero_immatriculation",
        "nom_usage_copropriete",
        "adresse_reference",
        "code_officiel_commune",
        "code_postal_adresse",
        "nombre_total_lots",
        "nombre_lots_habitation_bureaux_commerces",
        "nombre_lots_habitation",
        "nombre_lots_stationnement",
        "raison_sociale_representant_legal",
        "type_syndic",
        "periode_construction",
        "date_reglement_copropriete",
        "longitude",
        "latitude",
        "reference_cadastrale_1",
        "code_insee_parcelle_1",
        "prefixe_parcelle_1",
        "section_parcelle_1",
        "numero_parcelle_1",
        "mandat_en_cours",
        "date_fin_dernier_mandat",
    ]

    def _transform(self, tmp_path, monkeypatch, rows: list[dict]):
        import pandas as pd

        monkeypatch.setattr(
            "app.etl_copropriete.pipeline.get_download_dir",
            lambda dataset_name: tmp_path,
        )
        source = tmp_path / "rnic.csv"
        frame = pd.DataFrame(rows, columns=self.SOURCE_HEADER).fillna("")
        frame.to_csv(source, index=False, encoding="utf-8")

        output_path = CoproprietePipeline().transform(source)
        return pd.read_csv(output_path, dtype=str).fillna("")

    def _ligne_challans(self) -> dict:
        # Real-world shape of the bug: code_officiel_commune holds the
        # postal code (85300), the true INSEE code (85047, Challans) only
        # exists in code_insee_parcelle_1.
        return {
            "numero_immatriculation": "AA1-000-001",
            "code_officiel_commune": "85300",
            "code_postal_adresse": "85300",
            "reference_cadastrale_1": "85047000AB0042",
            "code_insee_parcelle_1": "85047",
            "section_parcelle_1": "AB",
            "numero_parcelle_1": "42",
        }

    def test_code_insee_vient_de_la_parcelle_pas_du_code_postal(
        self, tmp_path, monkeypatch
    ):
        out = self._transform(tmp_path, monkeypatch, [self._ligne_challans()])
        assert out.loc[0, "code_insee"] == "85047"

    def test_code_commune_recoit_la_meme_valeur_insee(self, tmp_path, monkeypatch):
        out = self._transform(tmp_path, monkeypatch, [self._ligne_challans()])
        assert out.loc[0, "code_commune"] == "85047"

    def test_code_insee_zfill_5(self, tmp_path, monkeypatch):
        ligne = self._ligne_challans()
        ligne["code_insee_parcelle_1"] = "1001"
        out = self._transform(tmp_path, monkeypatch, [ligne])
        assert out.loc[0, "code_insee"] == "01001"

    def test_parcelle_absente_ne_devine_jamais_depuis_le_code_postal(
        self, tmp_path, monkeypatch
    ):
        ligne = self._ligne_challans()
        ligne["reference_cadastrale_1"] = ""
        ligne["code_insee_parcelle_1"] = ""
        out = self._transform(tmp_path, monkeypatch, [ligne])
        assert out.loc[0, "code_insee"] == ""
        assert out.loc[0, "code_commune"] == ""

    def test_sentinelle_non_connu_traitee_comme_absente(self, tmp_path, monkeypatch):
        ligne = self._ligne_challans()
        ligne["reference_cadastrale_1"] = ""
        ligne["code_insee_parcelle_1"] = "non connu"
        out = self._transform(tmp_path, monkeypatch, [ligne])
        assert out.loc[0, "code_insee"] == ""


class TestMandat:
    """RNIC mandat columns: ``non_connu`` marker, ISO date, counted bad dates."""

    def _ligne(self, mandat: str, date: str) -> dict:
        return {
            "numero_immatriculation": "AA1-000-002",
            "code_insee_parcelle_1": "85047",
            "reference_cadastrale_1": "85047000AB0042",
            "mandat_en_cours": mandat,
            "date_fin_dernier_mandat": date,
        }

    def _out(self, tmp_path, monkeypatch, mandat, date):
        helper = TestTransformCleInsee()
        return helper._transform(tmp_path, monkeypatch, [self._ligne(mandat, date)])

    def test_valeur_source_normalisee(self, tmp_path, monkeypatch):
        out = self._out(tmp_path, monkeypatch, "  Mandat  en cours ", "2029-09-30")
        assert out.loc[0, "mandat_en_cours"] == "mandat en cours"

    @pytest.mark.parametrize(
        "source",
        ["Pas de mandat en cours", "Mandat expiré sans successeur déclaré"],
    )
    def test_autres_valeurs_vues_a_la_source(self, tmp_path, monkeypatch, source):
        out = self._out(tmp_path, monkeypatch, source, "")
        assert out.loc[0, "mandat_en_cours"] == normalize_mandat_en_cours(source)
        assert out.loc[0, "mandat_en_cours"] == source.lower()

    @pytest.mark.parametrize("source", ["", "   ", "non connu", "Non connu"])
    def test_vide_ou_sentinelle_donne_non_connu(self, tmp_path, monkeypatch, source):
        out = self._out(tmp_path, monkeypatch, source, "")
        assert out.loc[0, "mandat_en_cours"] == "non_connu"

    def test_colonne_absente_donne_quand_meme_non_connu(self, tmp_path, monkeypatch):
        import pandas as pd

        monkeypatch.setattr(
            "app.etl_copropriete.pipeline.get_download_dir", lambda name: tmp_path
        )
        source = tmp_path / "rnic.csv"
        header = [
            c
            for c in TestTransformCleInsee.SOURCE_HEADER
            if c not in ("mandat_en_cours", "date_fin_dernier_mandat")
        ]
        pd.DataFrame([self._ligne("", "")], columns=header).fillna("").to_csv(
            source, index=False
        )
        out = pd.read_csv(CoproprietePipeline().transform(source), dtype=str)
        assert out.loc[0, "mandat_en_cours"] == "non_connu"

    def test_date_iso_conservee(self, tmp_path, monkeypatch):
        out = self._out(tmp_path, monkeypatch, "Mandat en cours", "2029-09-30")
        assert out.loc[0, "date_fin_dernier_mandat"] == "2029-09-30"

    @pytest.mark.parametrize("date", ["", "non connu"])
    def test_date_vide_donne_null(self, tmp_path, monkeypatch, date):
        out = self._out(tmp_path, monkeypatch, "Mandat en cours", date)
        assert out.loc[0, "date_fin_dernier_mandat"] == ""

    @pytest.mark.parametrize("date", ["30/09/2029", "2029-13-45", "demain"])
    def test_date_illisible_rare_donne_null_et_est_comptee(
        self, tmp_path, monkeypatch, caplog, date
    ):
        lignes = [self._ligne("Mandat en cours", date)] + [
            {**self._ligne("Mandat en cours", "2029-09-30"),
             "numero_immatriculation": f"AA1-000-{i:03d}"}
            for i in range(3, 153)
        ]  # fmt: skip
        helper = TestTransformCleInsee()
        with caplog.at_level("WARNING", logger="app.etl_copropriete.pipeline"):
            out = helper._transform(tmp_path, monkeypatch, lignes)
        assert (out["date_fin_dernier_mandat"] == "").sum() == 1
        assert "1 unreadable date_fin_dernier_mandat" in caplog.text

    @pytest.mark.parametrize("sentinelle", ["N/A", "NULL", "NaN"])
    def test_une_sentinelle_de_pandas_est_une_date_illisible(
        self, tmp_path, monkeypatch, sentinelle
    ):
        lignes = [
            {
                **self._ligne("Mandat en cours", sentinelle),
                "numero_immatriculation": f"AA1-{i}",
            }
            for i in range(3)
        ]
        helper = TestTransformCleInsee()
        with pytest.raises(ValueError, match="date_fin_dernier_mandat"):
            helper._transform(tmp_path, monkeypatch, lignes)

    def test_dates_illisibles_au_dela_du_seuil_font_echouer(
        self, tmp_path, monkeypatch
    ):
        # A change of format at the source would empty every date silently.
        lignes = [
            {**self._ligne("Mandat en cours", d), "numero_immatriculation": f"AA1-{i}"}
            for i, d in enumerate(("30/09/2029", "2029-09-30", "2029-10-31"))
        ]
        helper = TestTransformCleInsee()
        with pytest.raises(ValueError, match="date_fin_dernier_mandat"):
            helper._transform(tmp_path, monkeypatch, lignes)

    def test_parse_date_fin_mandat(self):
        assert parse_date_fin_mandat("2027-03-31") == "2027-03-31"
        assert parse_date_fin_mandat("") == ""
        assert parse_date_fin_mandat("2027-02-30") is None


class TestSourceColumnsGardeFou:
    """SOURCE_COLUMNS must declare every consumed column, so that
    validate_schema fails BEFORE loading wrong data on a source rename
    (the guard was inert: only numero_immatriculation was declared)."""

    def test_source_columns_declare_les_colonnes_du_col_map(self):
        from app.etl_copropriete.config import COL_MAP, SOURCE_COLUMNS

        manquantes = set(COL_MAP) - set(SOURCE_COLUMNS)
        assert manquantes == set()

    def test_source_columns_declare_les_colonnes_de_parcelle(self):
        from app.etl_copropriete.config import SOURCE_COLUMNS

        for col in (
            "reference_cadastrale_1",
            "code_insee_parcelle_1",
            "prefixe_parcelle_1",
            "section_parcelle_1",
            "numero_parcelle_1",
        ):
            assert col in SOURCE_COLUMNS

    def test_code_officiel_commune_nest_plus_consommee(self):
        # Anti-regression lock on the D-20 root cause: this RNIC column
        # carries the postal code, it must never feed the INSEE key again.
        from app.etl_copropriete.config import COL_MAP

        assert "code_officiel_commune" not in COL_MAP


class TestNonRegressionLecture:
    """The existing read path stays untouched (discipline additive 45/47)."""

    def test_copropriete_par_codes_filtre_toujours_sur_code_commune(self):
        from app.copropriete import queries as copropriete_queries

        source = inspect.getsource(copropriete_queries.copropriete_par_codes)
        assert "cp.code_commune = ANY(:codes)" in source
