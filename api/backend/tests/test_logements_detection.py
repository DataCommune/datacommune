"""Unit tests for logements/detection.py -- signals, verdict, denominator."""

import pytest

pytestmark = pytest.mark.unit

from app.logements.detection import (
    MODE_BIEN,
    MODE_IMMEUBLE,
    MODE_INDICE,
    calcule_signaux,
    construit_denominateur,
    determine_mode,
    preuve_identites_distinctes,
)
from app.resolve.schemas import RESOLUTION_RAISONS


class TestSignaux:
    """D1/D2/D3 signal thresholds (D-01/D-03) -- no BDNB (D4 excluded from MVP)."""

    def test_signal_d1_copro_presente(self):
        signaux = calcule_signaux(True, 0, 0)
        assert signaux["copro_rnc"]["positif"] is True

    def test_signal_d2_seuil_deux_dpe(self):
        assert calcule_signaux(False, 1, 0)["dpe_appart"]["positif"] is False
        assert calcule_signaux(False, 2, 0)["dpe_appart"]["positif"] is True

    def test_signal_d2_compte_les_non_distinguables(self):
        assert calcule_signaux.__doc__ is not None
        assert "non_distinguables" in calcule_signaux.__doc__
        signaux = calcule_signaux(False, 0 + 3, 0)
        assert signaux["dpe_appart"]["positif"] is True

    def test_signal_d3_seuil_une_vente(self):
        assert calcule_signaux(False, 0, 0)["dvf_appart"]["positif"] is False
        assert calcule_signaux(False, 0, 1)["dvf_appart"]["positif"] is True

    def test_aucun_signal_bdnb(self):
        signaux = calcule_signaux(True, 2, 1)
        assert set(signaux.keys()) == {"copro_rnc", "dpe_appart", "dvf_appart"}

    def test_signaux_utilisent_le_vocabulaire_resolve(self):
        signaux = calcule_signaux(False, 0, 0)
        for signal in signaux.values():
            assert signal["presence"] in {"value", "absent", "error"}
            if signal["absence_reason"] is not None:
                assert signal["absence_reason"] in RESOLUTION_RAISONS


class TestMode:
    """Verdict = simple count of >=2 positive signals, no numeric coherence (D-02)."""

    def test_mode_immeuble_a_deux_signaux_quelconques(self):
        assert determine_mode(calcule_signaux(True, 0, 1)) == MODE_IMMEUBLE
        assert determine_mode(calcule_signaux(False, 2, 1)) == MODE_IMMEUBLE
        assert determine_mode(calcule_signaux(True, 2, 0)) == MODE_IMMEUBLE

    def test_mode_indice_a_un_signal(self):
        assert determine_mode(calcule_signaux(True, 0, 0)) == MODE_INDICE

    def test_mode_bien_a_zero_signal(self):
        assert determine_mode(calcule_signaux(False, 0, 0)) == MODE_BIEN


def _carte(etage=None, batiment=None, porte=None) -> dict:
    """Minimal enriched card, as produced by service.dedup_logements."""
    return {"etage": etage, "batiment": batiment, "porte": porte}


class TestPreuveIdentites:
    """ADR-0006 promotion guard: distinct identities + porte/batiment token.

    Hard constraint (D-02): a maison individuelle re-surveyed twice must
    NEVER be presented as an immeuble -- including the mislabelled-maison
    counter-case demonstrated by the 2026-08-04 review (T3).
    """

    def test_guerande_deux_identites_dont_une_porte(self):
        """Reference case 1 rue de Guerande: 2 distinct identities, one porte."""
        cartes = [_carte(etage=2, porte="droite"), _carte(etage=4)]
        assert preuve_identites_distinctes(cartes) is True

    def test_maison_multi_etages_sans_porte_refusee(self):
        """Review counter-case: 'Etage 1'/'Etage 2', no porte/bat -> refused."""
        cartes = [_carte(etage=1), _carte(etage=2)]
        assert preuve_identites_distinctes(cartes) is False

    def test_remesure_meme_identite_refusee(self):
        """Surface-split re-measure: 2 cards, ONE identity -> refused."""
        cartes = [_carte(etage=1), _carte(etage=1)]
        assert preuve_identites_distinctes(cartes) is False

    def test_batiment_vaut_comme_token(self):
        cartes = [_carte(etage=1, batiment="A"), _carte(etage=2, batiment="B")]
        assert preuve_identites_distinctes(cartes) is True

    def test_moins_de_deux_cartes_refusees(self):
        assert preuve_identites_distinctes([]) is False
        assert preuve_identites_distinctes([_carte(etage=2, porte="12")]) is False

    def test_promotion_depuis_un_seul_signal(self):
        """1 positive signal + identity proof -> immeuble (ADR-0006)."""
        signaux = calcule_signaux(False, 2, 0)
        assert determine_mode(signaux, preuve_identites=True) == MODE_IMMEUBLE
        assert determine_mode(signaux, preuve_identites=False) == MODE_INDICE

    def test_defaut_sans_preuve_inchange(self):
        """D-02 nominal untouched: the default argument changes nothing."""
        assert determine_mode(calcule_signaux(True, 0, 0)) == MODE_INDICE
        assert determine_mode(calcule_signaux(True, 2, 0)) == MODE_IMMEUBLE


class TestDenominateur:
    """Honest denominator: lots stays None, non_distinguables never estimated."""

    def test_denominateur_forme_complete(self):
        denom = construit_denominateur(11, 24, 3)
        assert denom == {
            "logements_connus": 11,
            "lots_declares": 24,
            "non_distinguables": 3,
        }

    def test_denominateur_lots_inconnus(self):
        denom = construit_denominateur(11, None, 0)
        assert denom["lots_declares"] is None

    def test_denominateur_zero_non_distinguables(self):
        denom = construit_denominateur(11, 24, 0)
        assert denom["non_distinguables"] == 0
