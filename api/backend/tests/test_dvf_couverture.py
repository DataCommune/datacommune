"""Wave 6 matrix of the pure DVF coverage-threshold policy (Phase 57, D-23/D-24).

ONE function is proven here, without any database or network access:
``couverture_suffisante`` -- the closed decision of whether an effectif
clears the `n.d.` threshold arbitrated in `57-VERDICT-COUVERTURE.md` (C-01
commune, C-02 zone), the same threshold `v_commune_dvf` interpolates from
this module rather than hardcoding its own number.

A dedicated test proves the module docstring cites its own evidence trail
(`C-01`, `C-02`, `57-MESURE-DVF`, the verdict date) -- the traceability from
constant to measured number is part of the contract, not decorative prose.
"""

import pytest

pytestmark = pytest.mark.unit

from app.dvf import couverture
from app.dvf.couverture import (
    CLASSES_STATISTIQUES,
    SEUIL_ND_COMMUNE,
    SEUIL_ND_ZONE,
    couverture_suffisante,
)


def test_seuils_valent_exactement_les_nombres_retenus_au_verdict():
    """C-01 et C-02 (57-VERDICT-COUVERTURE.md) retiennent tous deux 5."""
    assert SEUIL_ND_COMMUNE == 5
    assert SEUIL_ND_ZONE == 5


def test_commune_strictement_sous_le_seuil_est_insuffisante():
    assert couverture_suffisante(4, portee="commune") is False


def test_commune_au_seuil_pile_est_suffisante():
    assert couverture_suffisante(5, portee="commune") is True


def test_commune_au_dessus_du_seuil_est_suffisante():
    assert couverture_suffisante(42, portee="commune") is True


def test_zone_applique_le_meme_seuil_que_commune():
    assert couverture_suffisante(4, portee="zone") is False
    assert couverture_suffisante(5, portee="zone") is True


def test_effectif_nul_est_toujours_insuffisant():
    """Un effectif nul retourne False -- jamais une exception, jamais un True."""
    assert couverture_suffisante(0, portee="commune") is False
    assert couverture_suffisante(0, portee="zone") is False


def test_portee_par_defaut_est_commune():
    assert couverture_suffisante(5) is True
    assert couverture_suffisante(4) is False


def test_portee_inconnue_leve_valueerror():
    """Vocabulaire ferme (D-23) -- pas de repli silencieux sur une portee inconnue."""
    with pytest.raises(ValueError, match="portee inconnue"):
        couverture_suffisante(10, portee="departement")


def test_classes_statistiques_ne_contient_que_le_residentiel_mono_local():
    assert CLASSES_STATISTIQUES == ("residentiel_maison", "residentiel_appartement")


def test_docstring_cite_sa_preuve():
    """Le lien seuil -> chiffre mesure fait partie du contrat (D-23 traçabilite)."""
    docstring = couverture.__doc__ or ""
    assert "C-01" in docstring
    assert "C-02" in docstring
    assert "57-MESURE-DVF" in docstring
    assert "2026-08-24" in docstring
