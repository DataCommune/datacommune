"""Unit tests: carrier width ceiling on the RNB building widening (R4-02).

Recalibrated 2026-08-06 (internal note INSTRUMENTATION-PLAFOND-GEOMETRIQUE-
2026-08-06): the 2026-08-05 ceiling measured the widened SET (2 streets /
4 keys) and withheld 29 legitimate diagnostics from a real corner building
with 3 entrances on 3 streets (angle-44-000006) -- corner buildings and
multi-entrance bis/ter buildings are exactly what the widening exists for.
The geometric replacements considered (disjoint carriers, multi-parcel
footprints) discriminate nothing: what separates the adjudicated pair is the
width of the CARRIER itself -- 3 keys for the true corner building, 16 for
the gallery artefact that filled an empty fiche with 22 borrowed diagnostics.

The ceiling is still deliberately blunt: one carrier past the width threshold
and NOTHING is widened. These tests pin both sides of it -- the corner
building widens whatever its street count, the artefact does not -- and the
exact boundary.
"""

import pytest

pytestmark = pytest.mark.unit

from app.logements.service import (
    PLAFOND_LARGEUR_PORTEUR,
    applique_plafond_largeur,
)

# Corner building, 2 rue des Essais / 12 rue Felibien: the canonical R4-02 case.
CLE_FELIBIEN = "44109_3236_00012"


class TestPlafondLargeur:
    def test_immeuble_d_angle_legitime_elargit_toujours(self):
        """NON-REGRESSION: a 2-key carrier on 2 streets -- R4-02 must work.

        This is the case the rule exists for. A ceiling that silences it would
        have traded one bug for another.
        """
        plafond = applique_plafond_largeur((CLE_FELIBIEN,), largeur_porteur=2)

        assert plafond.depasse is False
        assert plafond.cles == (CLE_FELIBIEN,)
        assert plafond.largeur_porteur == 2

    def test_trois_voies_elargissent_desormais(self):
        """The 2026-08-06 recalibration case (angle-44-000006).

        A real corner building with 3 entrances on 3 STREETS, carrier of 3
        keys: the previous ceiling cancelled its widening on the street count
        (29 legitimate diagnostics withheld, proven false positive). Streets
        are not counted any more -- 3 entrances on 3 streets is what a corner
        building IS.
        """
        elargies = ("44109_3236_00012", "44109_5000_00007")

        plafond = applique_plafond_largeur(elargies, largeur_porteur=3)

        assert plafond.depasse is False
        assert plafond.cles == elargies

    def test_largeur_neuf_est_exactement_le_plafond(self):
        """Widest sane carrier of the corpus (bis_ter-44-000014): 9 keys pass.

        The count is per carrier, so 8 widened keys under one 9-key carrier
        stay a legitimate multi-entrance building.
        """
        elargies = tuple(f"44109_zz01_{n:05d}" for n in range(2, 10))

        plafond = applique_plafond_largeur(elargies, largeur_porteur=9)

        assert plafond.depasse is False
        assert plafond.cles == elargies

    def test_largeur_dix_n_elargit_pas(self):
        """One key past the ceiling: cancelled ENTIRELY, never partially."""
        elargies = tuple(f"44109_zz01_{n:05d}" for n in range(2, 11))

        plafond = applique_plafond_largeur(elargies, largeur_porteur=10)

        assert plafond.depasse is True
        assert plafond.cles == ()
        assert plafond.largeur_porteur == 10

    def test_le_cas_reel_16_cles_sur_4_rues_n_elargit_rien(self):
        """The artefact that motivated the ceiling (corpus 2026-08-05).

        ONE RNB building carrying 16 BAN keys spread over 4 streets, numbers 1
        to 22. It filled a fiche that is legitimately empty with 22 diagnostics
        taken from the neighbouring streets. Not an immeuble: an aggregation
        artefact -- and 16 is the carrier width, exactly what the recalibrated
        ceiling measures.
        """
        elargies = tuple(
            f"44109_{5000 + (i + 1) % 4:04d}_{(i + 1) * 3 // 2 + 1:05d}"
            for i in range(15)
        )

        plafond = applique_plafond_largeur(elargies, largeur_porteur=16)

        assert plafond.depasse is True
        assert plafond.cles == ()

    def test_aucun_elargissement_reste_un_non_depassement(self):
        """An empty widening is not a blocked one: the log must tell them apart.

        Covers both the mono-key carrier (largeur 1) and the no-carrier case
        (largeur 0, no row returned at all).
        """
        for largeur in (0, 1):
            plafond = applique_plafond_largeur((), largeur_porteur=largeur)

            assert plafond.depasse is False
            assert plafond.cles == ()

    def test_le_seuil_est_celui_calibre_le_2026_08_06(self):
        """Locked: the calibration is a measurement, not a tunable knob.

        9 separates the two adjudicated cases (3 vs 16), clears every sane
        widened fiche of the corpus (carriers <= 4) and the widest plausible
        multi-entrance building measured (9); carriers above 9 keys are
        0,06 % of the 1,43 M-building population. Changing it silently would
        invalidate the instrumentation quoted in applique_plafond_largeur.
        """
        assert PLAFOND_LARGEUR_PORTEUR == 9
