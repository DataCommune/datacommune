"""Unit tests: street-number discrimination read from the BAN key (T4).

The D-13 narrowing used to be attempted on ``complement_adresse_logement``,
which carries floor/door/building ONLY: ``diagnostics_dpe`` holds no postal
address field at all, so that filter could never separate two street numbers
of the same parcel (verified in prod and in base, 2026-08-04 -- passing
``adresse=zzz introuvable`` returned the exact same rows as passing nothing).

The number is read instead from the BAN interoperability key
(``INSEE_FANTOIR_NUMERO``), the only address-bearing field a DPE row carries.
Doctrine (Ianis, 2026-08-04): fail-CLOSED on rows that carry a readable
number, fail-OPEN and traced on rows that carry none.
"""

import pytest

pytestmark = pytest.mark.unit

from app.logements.service import filtre_par_numero_voie, numero_voie_ban

# Resolved key of 1 rue de Guerande (parcel 44109000HN0129, witness case).
CLE_N1 = "44109_3912_00001"
# Same parcel, OTHER building: 3 rue de Guerande. Publishing its DPE on the
# fiche of n°1 is exactly the regression fixed by PR #47.
CLE_N3 = "44109_3912_00003"
# Orphan key (finding R3-04, 8,7 % dept 44 / 20 % dept 85): same address,
# DIFFERENT voie code -- the number is what still matches.
CLE_N1_ORPHELINE = "44109_9999_00001"


def _row(numero_dpe: str, identifiant_ban: str | None) -> dict:
    return {"numero_dpe": numero_dpe, "identifiant_ban": identifiant_ban}


class TestNumeroVoieBan:
    @pytest.mark.parametrize(
        ("cle", "attendu"),
        [
            (CLE_N1, "1"),
            ("44184_5330_00040", "40"),
            # 4 segments: the suffix is part of the number, "1 bis" is NOT "1".
            ("44109_3912_00001_bis", "1_bis"),
            ("44109_3912_00001_ter", "1_ter"),
            ("97402_1234_00007", "7"),
            # 2 segments = voie-level key: no number encoded, never invented.
            ("44109_4552", None),
            ("44109", None),
            ("ban_1", None),
            # Non-numeric number slot: unreadable, never guessed.
            ("44109_3912_000A1", None),
            ("", None),
            (None, None),
        ],
    )
    def test_extraction(self, cle, attendu):
        assert numero_voie_ban(cle) == attendu


class TestFiltreParNumeroVoie:
    def test_exclut_le_numero_voisin_de_la_meme_parcelle(self):
        """T4: two street numbers on one parcel -- only the requested one stays."""
        rows = [
            _row("dpe-n1-a", CLE_N1),
            _row("dpe-n3-a", CLE_N3),
            _row("dpe-n3-b", CLE_N3),
        ]

        gardes, exclus, sans_numero = filtre_par_numero_voie(rows, [CLE_N1])

        assert [r["numero_dpe"] for r in gardes] == ["dpe-n1-a"]
        assert exclus == 2
        assert sans_numero == 0

    def test_garde_les_cles_orphelines_du_meme_numero(self):
        """R3-04: an orphan key differs by VOIE code, not by number -- kept."""
        rows = [_row("dpe-orphelin", CLE_N1_ORPHELINE)]

        gardes, exclus, _ = filtre_par_numero_voie(rows, [CLE_N1])

        assert [r["numero_dpe"] for r in gardes] == ["dpe-orphelin"]
        assert exclus == 0

    def test_union_des_numeros_preserve_l_immeuble_d_angle(self):
        """R4-02: corner building = two entrances, two numbers, one DPE set.

        The reference is the UNION of the resolved key and the other BAN keys
        of the SAME RNB building, so widening and discrimination cannot
        cancel each other out (witness 44184000VO0054).
        """
        rows = [
            _row("dpe-entree-a", "44184_5330_00040"),
            _row("dpe-entree-b", "44184_7777_00012"),
            _row("dpe-voisin", "44184_5330_00044"),
        ]

        gardes, exclus, _ = filtre_par_numero_voie(
            rows, ["44184_5330_00040", "44184_7777_00012"]
        )

        assert [r["numero_dpe"] for r in gardes] == ["dpe-entree-a", "dpe-entree-b"]
        assert exclus == 1

    def test_fail_open_trace_sur_les_lignes_sans_cle(self):
        """A keyless row cannot be discriminated: kept, and COUNTED (T5)."""
        rows = [_row("dpe-sans-cle", None), _row("dpe-cle-voie", "44109_4552")]

        gardes, exclus, sans_numero = filtre_par_numero_voie(rows, [CLE_N1])

        assert len(gardes) == 2
        assert exclus == 0
        assert sans_numero == 2

    def test_sans_reference_lisible_aucun_filtrage(self):
        """Voie-level resolved key: nothing to discriminate on -> fail-open."""
        rows = [_row("dpe-n1", CLE_N1), _row("dpe-n3", CLE_N3)]

        gardes, exclus, sans_numero = filtre_par_numero_voie(rows, ["44109_4552"])

        assert len(gardes) == 2
        assert (exclus, sans_numero) == (0, 0)

    def test_sans_cle_de_reference_aucun_filtrage(self):
        """No ban_id at all (pure spatial path): the filter never bites."""
        rows = [_row("dpe-n1", CLE_N1), _row("dpe-n3", CLE_N3)]

        gardes, exclus, sans_numero = filtre_par_numero_voie(rows, [])

        assert len(gardes) == 2
        assert (exclus, sans_numero) == (0, 0)

    def test_le_suffixe_bis_est_une_adresse_distincte(self):
        """1 bis is not 1: excluded unless the RNB widening names its key."""
        rows = [_row("dpe-bis", "44109_3912_00001_bis")]

        gardes, exclus, _ = filtre_par_numero_voie(rows, [CLE_N1])
        assert gardes == []
        assert exclus == 1

        gardes, exclus, _ = filtre_par_numero_voie(
            rows, [CLE_N1, "44109_3912_00001_bis"]
        )
        assert len(gardes) == 1
        assert exclus == 0

    def test_preserve_l_ordre_et_les_lignes(self):
        """Pure filter: kept rows are the SAME objects, in the same order."""
        rows = [_row("a", CLE_N1), _row("b", CLE_N3), _row("c", CLE_N1)]

        gardes, _, _ = filtre_par_numero_voie(rows, [CLE_N1])

        assert gardes == [rows[0], rows[2]]
        assert gardes[0] is rows[0]
