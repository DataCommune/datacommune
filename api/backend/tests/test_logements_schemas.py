"""Unit tests for app/logements/schemas.py -- response contract (D-12/D-16/D-17)."""

from datetime import date

import pytest

pytestmark = pytest.mark.unit

from pydantic import ValidationError

from app.dpe.schemas import (
    DpeAttribution,
    DpeBuildingContext,
    DpeInventoryDistribution,
    DpeInventoryPage,
)
from app.etl_dvf.qualification import CLASSES_MUTATION, RAISONS_PRIX_M2
from app.logements.schemas import (
    BlocDetection,
    Denominateur,
    ResultatLogements,
    SignalDetection,
    VenteImmeuble,
)
from app.resolve.schemas import RESOLUTION_RAISONS

# Models that must still carry no monetary field. `VenteImmeuble` was removed
# from this list by Phase 57 Plan 13 (D-17) -- deliberately, not by accident:
# see TestNoPriceField and TestPrixToujoursEtiquete below. `LogementItem` left
# it in phase 01-04 of the reboot for a different reason: the card it described
# was removed entirely, so there is no longer a model to keep price-free.
_MODELES = [
    SignalDetection,
    BlocDetection,
    Denominateur,
    ResultatLogements,
]

_INTERDITS = ("valeur_fonciere", "prix", "montant", "cout")


def _bloc_detection() -> BlocDetection:
    return BlocDetection(
        signaux={
            "copro_rnc": SignalDetection(positif=True, presence="value", compte=1),
            "dpe_appart": SignalDetection(
                positif=False,
                presence="absent",
                absence_reason="not_reported",
                compte=0,
            ),
            "dvf_appart": SignalDetection(
                positif=False,
                presence="absent",
                absence_reason="not_reported",
                compte=0,
            ),
        },
        signaux_positifs=1,
        mode="indice",
    )


def _attribution_batiment() -> DpeAttribution:
    """The Phase 51 decision this fiche publishes: a building aggregate.

    ``distribution_dpe`` mirrors it verbatim (Plan 51-03), so the two must be
    built together -- the model refuses a distribution the decision does not
    carry.
    """
    return DpeAttribution(
        status="absent",
        reason="not_reported",
        building_context=DpeBuildingContext(
            count=5, distribution={"C": 1, "D": 3, "E": 1}
        ),
    )


def _resultat(**overrides) -> ResultatLogements:
    base = {
        "id_parcelle": "85047000AB0042",
        "detection": _bloc_detection(),
        "denominateur": Denominateur(
            logements_connus=0, lots_declares=None, non_distinguables=0
        ),
        "distribution_dpe": {"C": 1, "D": 3, "E": 1},
        "ventes_immeuble": [],
        "dpe_inventory": DpeInventoryPage(
            state="absent",
            context_ref="opaque-context",
            total=0,
            distribution=DpeInventoryDistribution(),
            page_size=20,
            has_more=False,
            read_state="complete",
        ),
        "dpe_attribution": _attribution_batiment(),
        "diagnostics_charges": 5,
        "rattachement": {"exact": 0, "elargi_batiment": 5, "spatial": 0},
        "tronque": False,
    }
    base.update(overrides)
    return ResultatLogements(**base)


class TestNoPriceField:
    """D-16 narrowed by D-17: no amount on a logement CARD -- gate still live.

    This gate was posed in Phase 48 against a precise regression: a per-LINE
    DVF price, i.e. the whole mutation's ``valeur_fonciere`` displayed beside
    a single local's surface. Phase 57 removed that cause by reconstructing
    the mutation, and D-17 revises the interdiction accordingly --
    :class:`VenteImmeuble` is therefore OUT of ``_MODELES``, by decision and
    with a replacement, never by accident.

    What takes over for that one model is
    :class:`TestPrixToujoursEtiquete`, which proves the stronger property:
    an amount is unconstructible WITHOUT the class label that says what was
    sold, and a price per m2 is unconstructible on anything but a mono-local
    mutation. Everything else this module publishes -- a logement card, a
    detection signal, a denominator, the response body itself -- still
    carries no monetary field at all, which is what the loop below keeps
    enforcing.
    """

    def test_schema_has_no_price_field(self):
        """D-16/D-17: no model but VenteImmeuble exposes a price-looking field."""
        for modele in _MODELES:
            for nom_champ in modele.model_fields:
                bas = nom_champ.lower()
                assert not any(interdit in bas for interdit in _INTERDITS), (
                    f"{modele.__name__}.{nom_champ} looks like a price field"
                )

    def test_le_gate_couvre_toujours_les_quatre_modeles_sans_prix(self):
        """Each departure from the list is named, so none can be silent."""
        noms = [modele.__name__ for modele in _MODELES]
        assert noms == [
            "SignalDetection",
            "BlocDetection",
            "Denominateur",
            "ResultatLogements",
        ]
        assert VenteImmeuble not in _MODELES
        assert "logements" not in ResultatLogements.model_fields

    def test_le_gate_bloque_bien_un_champ_de_prix_ajoute_a_un_modele(self):
        """D-40: prove the gate blocks -- its presence is not its proof.

        A published model that grew a price field must make this test red.
        Simulated on a throwaway subclass so the real contract is untouched.
        Anchored on `Denominateur` since phase 01-04: the former anchor was
        `LogementItem`, and a removed model cannot vouch for anything.
        """

        class DenominateurAvecPrix(Denominateur):
            valeur_fonciere: float | None = None

        champs = DenominateurAvecPrix.model_fields
        assert any(
            any(interdit in nom.lower() for interdit in _INTERDITS) for nom in champs
        ), "the forbidden-word list must catch a price field added to a model"


class TestPrixToujoursEtiquete:
    """D-17/D-40: on VenteImmeuble, an amount cannot exist without its label.

    This is the guarantee that REPLACES the Phase 48 interdiction for this
    model (see :class:`TestNoPriceField`). Each rule below is proven by a
    negative test: the incoherent payload raises, it is not merely
    undocumented.
    """

    def test_un_prix_de_mutation_etiquete_est_constructible(self):
        """The nominal row D-17 exists to allow: a price beside its class."""
        vente = VenteImmeuble(
            date_mutation=date(2024, 3, 1),
            type_local=None,
            surface_reelle_bati=310,
            nombre_pieces_principales=14,
            valeur_fonciere=980000.0,
            classe_mutation="ensemble_residentiel",
            mono_local=False,
            hors_stats_vefa=False,
            prix_m2=None,
            prix_m2_raison="mutation_multi_locaux",
            nb_locaux_residentiel=5,
            nb_locaux_commercial=0,
        )
        assert vente.valeur_fonciere == 980000.0
        assert vente.classe_mutation == "ensemble_residentiel"
        assert vente.prix_m2 is None, "an ensemble sale has no honest price per m2"

    def test_un_prix_au_m2_exige_une_mutation_mono_local(self):
        """A mono-local sale is the only row where a price per m2 is true."""
        vente = VenteImmeuble(
            valeur_fonciere=250000.0,
            classe_mutation="residentiel_appartement",
            mono_local=True,
            prix_m2=4200.0,
            nb_locaux_residentiel=1,
        )
        assert vente.prix_m2 == 4200.0
        assert vente.prix_m2_raison is None

    def test_un_montant_sans_etiquette_est_inconstructible(self):
        """D-17: valeur_fonciere without classe_mutation is the forbidden payload."""
        with pytest.raises(ValidationError):
            VenteImmeuble(valeur_fonciere=250000)

    def test_une_etiquette_hors_vocabulaire_est_inconstructible(self):
        """The label belongs to the closed CLASSES_MUTATION vocabulary."""
        with pytest.raises(ValidationError):
            VenteImmeuble(valeur_fonciere=250000, classe_mutation="vente_immeuble")

    def test_un_prix_au_m2_sur_une_vente_d_ensemble_est_inconstructible(self):
        """Dividing a global price by several dwellings' surface stays impossible."""
        with pytest.raises(ValidationError):
            VenteImmeuble(
                prix_m2=4200,
                mono_local=False,
                classe_mutation="ensemble_residentiel",
            )

    def test_un_prix_au_m2_sans_drapeau_mono_local_est_inconstructible(self):
        """`None` is not `True`: an unknown mono-local flag proves nothing."""
        with pytest.raises(ValidationError):
            VenteImmeuble(
                prix_m2=4200,
                classe_mutation="residentiel_appartement",
                valeur_fonciere=250000,
            )

    def test_une_raison_hors_vocabulaire_est_inconstructible(self):
        """prix_m2_raison belongs to the closed RAISONS_PRIX_M2 vocabulary."""
        with pytest.raises(ValidationError):
            VenteImmeuble(
                valeur_fonciere=250000,
                classe_mutation="mixte",
                mono_local=False,
                prix_m2_raison="pas_envie",
            )

    def test_une_ligne_avec_montant_exige_prix_m2_ou_sa_raison(self):
        """Same coherence as ResultatDvf: exactly one of the two, never neither."""
        with pytest.raises(ValidationError):
            VenteImmeuble(
                valeur_fonciere=250000,
                classe_mutation="mixte",
                mono_local=False,
            )
        with pytest.raises(ValidationError):
            VenteImmeuble(
                valeur_fonciere=250000,
                classe_mutation="residentiel_appartement",
                mono_local=True,
                prix_m2=4200,
                prix_m2_raison="surface_absente",
            )

    def test_une_ligne_sans_montant_reste_constructible_sans_raison(self):
        """A contextual-only row (fixture, pre-D-17 caller) is still valid.

        This is why the iff above is gated on ``valeur_fonciere``: with every
        field optional, a row that projects no amount at all legitimately
        carries neither a price per m2 nor a reason for its absence.
        """
        vente = VenteImmeuble(
            date_mutation=date(2024, 3, 1),
            type_local="Appartement",
            surface_reelle_bati=65,
            nombre_pieces_principales=3,
        )
        assert vente.valeur_fonciere is None
        assert vente.prix_m2 is None and vente.prix_m2_raison is None

    def test_le_vocabulaire_est_celui_de_l_etl_jamais_un_double(self):
        """No second taxonomy: the model reads the ETL's closed vocabularies."""
        assert "ensemble_residentiel" in CLASSES_MUTATION
        assert "mutation_multi_locaux" in RAISONS_PRIX_M2


class TestDetectionStatesUseResolveVocabulary:
    def test_detection_states_use_resolve_vocabulary(self):
        """presence in {value,absent,error}; absence_reason in RESOLUTION_RAISONS."""
        valide = SignalDetection(
            positif=False,
            presence="absent",
            absence_reason="not_reported",
            compte=0,
        )
        assert valide.presence in {"value", "absent", "error"}
        assert valide.absence_reason in RESOLUTION_RAISONS

        with pytest.raises(ValidationError):
            SignalDetection(positif=False, presence="inconnu", compte=0)  # type: ignore[arg-type]

        with pytest.raises(ValidationError):
            SignalDetection(
                positif=False,
                presence="absent",
                absence_reason="raison_inventee",
                compte=0,
            )


class TestModeLiteral:
    def test_mode_literal(self):
        for valeur in ("immeuble", "indice", "bien"):
            bloc = _bloc_detection()
            bloc.mode = valeur
            assert bloc.mode == valeur

        with pytest.raises(ValidationError):
            BlocDetection(signaux={}, signaux_positifs=0, mode="autre")  # type: ignore[arg-type]


class TestLotsDeclaresAcceptsNone:
    def test_lots_declares_accepts_none(self):
        denom = Denominateur(
            logements_connus=11, lots_declares=None, non_distinguables=3
        )
        assert denom.lots_declares is None
        dump = denom.model_dump(mode="json")
        assert dump["lots_declares"] is None


class TestDiagnosticsChargesPresent:
    def test_diagnostics_charges_present(self):
        assert "diagnostics_charges" in ResultatLogements.model_fields
        resultat = _resultat()
        assert resultat.diagnostics_charges == 5
        # It counts DOCUMENTS read, and there is no card list to confuse it
        # with any more (phase 01-04): the field itself is gone.
        assert "logements" not in ResultatLogements.model_fields


class TestDistributionDpePorteSurLesNonDistinguables:
    def test_distribution_dpe_porte_sur_les_non_distinguables(self):
        """n3: the distribution describes the diagnostics the fiche cannot
        attribute -- the building aggregate -- never the cards it publishes."""
        resultat = _resultat()
        assert resultat.distribution_dpe == {"C": 1, "D": 3, "E": 1}
        total = sum(resultat.distribution_dpe.values())
        assert total == 5
        assert total != resultat.denominateur.logements_connus


class TestContratLogementsV2:
    def test_inventory_is_required_and_uses_shared_closed_model(self):
        field = ResultatLogements.model_fields["dpe_inventory"]
        assert field.is_required()
        assert field.annotation is DpeInventoryPage

    @pytest.mark.parametrize(
        "mutate",
        [
            # Since phase 01-04 there is no `logements` field at all, so this
            # case is caught by `extra="forbid"` rather than by the validator.
            # Kept deliberately: a reintroduced card list must stay rejected.
            lambda body: body.update(
                logements=[{"numero_dpe": "legacy-card", "perime": False}]
            ),
            lambda body: body["detection"].update(preuve_identites=True),
            lambda body: body["denominateur"].update(logements_connus=1),
            lambda body: body["denominateur"].update(non_distinguables=1),
            lambda body: body["dpe_attribution"].update(
                status="exact",
                reason=None,
                diagnostic={
                    "numero_dpe": "legacy-document",
                    "date_etablissement_dpe": None,
                    "type_batiment": None,
                    "surface_habitable": None,
                    "etiquette_dpe": None,
                    "etiquette_ges": None,
                },
            ),
        ],
    )
    def test_document_counts_cannot_be_promoted_to_dwelling_identity(self, mutate):
        body = _resultat().model_dump(mode="python")
        mutate(body)
        with pytest.raises(ValidationError):
            ResultatLogements.model_validate(body)

    def test_attribution_must_match_inventory_state(self):
        body = _resultat().model_dump(mode="python")
        body["dpe_inventory"] = {
            "state": "ready",
            "context_ref": "opaque-context",
            "total": 1,
            "distribution": {"D": 1},
            "page_size": 20,
            "has_more": False,
            "read_state": "complete",
        }
        body["rattachement"]["exact"] = 1
        body["diagnostics_charges"] = 6

        with pytest.raises(ValidationError):
            ResultatLogements.model_validate(body)

    def test_apartment_document_count_cannot_exceed_inventory_total(self):
        body = _resultat().model_dump(mode="python")
        body["detection"]["signaux"]["dpe_appart"] = {
            "positif": False,
            "presence": "value",
            "absence_reason": None,
            "compte": 1,
        }

        with pytest.raises(ValidationError):
            ResultatLogements.model_validate(body)

    def test_response_rejects_unlisted_fields(self):
        body = _resultat().model_dump(mode="python")
        body["raw_dpe_row"] = {"numero_dpe": "must-not-leak"}

        with pytest.raises(ValidationError):
            ResultatLogements.model_validate(body)
