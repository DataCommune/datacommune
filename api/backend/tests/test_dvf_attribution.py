"""Tests: DVF attribution decision and its unconstructible impossible states.

Phase 57 plan 12 (D-10/D-11/D-12/D-13/D-16, D-37). Two levels live here:

  1. Contract: :class:`app.dvf.schemas.DvfAttribution` rejects every incoherent
     payload BEFORE serialization -- a positive state without its mutation, an
     ``exacte`` without a local proof, an absence carrying a price, a reason
     outside the closed vocabulary. D-40 demands that a gate proves it blocks:
     each of these is a NEGATIVE test, not the mere presence of a validator.
  2. Policy: :func:`app.dvf.attribution.decide_dvf_attribution` decides what may
     be said about the consulted property, and NEVER produces ``exacte`` in this
     phase -- no local/lot proof source exists yet (D-37).

Offline by construction: no database, no network, no app. Every fixture is a
CONFIGURATION of the real Reddit-reported cases (mixed Pornichet-shaped sale,
VEFA without surface, block sale), never a real address (D-31).
"""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.dvf import attribution
from app.dvf.attribution import decide_dvf_attribution
from app.dvf.schemas import (
    DVF_ATTRIBUTION_RAISONS,
    DVF_ATTRIBUTION_RULE_VERSION,
    DVF_EVIDENCES,
    DVF_PREUVES_LOCALES,
    DVF_TYPES_BIEN_CONSULTES,
    DvfAttribution,
    ResultatDvf,
)

pytestmark = pytest.mark.unit


# --- Synthetic mutation configurations (D-31) --------------------------------


def mutation(**overrides) -> dict:
    """One reconstructed mutation row, appartement mono-local sale by default.

    Same shape as the rows ``app.dvf.queries`` projects from
    ``mutations_grain`` -- the decision reads nothing the endpoints do not
    already publish.
    """
    ligne: dict = {
        "id_mutation": "CASE-DVF-0001",
        "date_mutation": date(2024, 6, 1),
        "nature_mutation": "Vente",
        "valeur_fonciere": 250000.0,
        "type_local": "Appartement",
        "surface_reelle_bati": 62,
        "nombre_pieces_principales": 3,
        "surface_terrain": None,
        "prix_m2": 4032.0,
        "adresse_numero": "1",
        "adresse_nom_voie": "rue synthetique",
        "code_postal": "85300",
        "nom_commune": "Commune-Test",
        "id_parcelle": "85047000AD0123",
        "code_type_local": "2",
        "longitude": -1.000001,
        "latitude": 46.000001,
        "classe_mutation": "residentiel_appartement",
        "prix_m2_raison": None,
        "mutation_mono_local": True,
        "nb_locaux_maison": 0,
        "nb_locaux_appartement": 1,
        "nb_locaux_commercial": 0,
        "nb_lots_source": 1,
        "nb_parcelles": 1,
        "hors_stats_vefa": False,
        "surface_bati_totale": 62,
    }
    ligne.update(overrides)
    return ligne


def mutation_candidate() -> ResultatDvf:
    """The projected model a positive state must carry."""
    return ResultatDvf.model_validate(mutation())


# --- Level 1: the impossible states are unconstructible (Task 2, D-40) -------


def test_compatible_parcelle_sans_mutation_est_inconstructible():
    """A positive state without its candidate is a price with no transaction."""
    with pytest.raises(ValidationError):
        DvfAttribution(status="compatible_parcelle")


def test_exacte_sans_preuve_locale_est_inconstructible():
    """D-37 in the type system: parcel compatibility is NOT a local proof."""
    with pytest.raises(ValidationError):
        DvfAttribution(status="exacte", mutation=mutation_candidate())


def test_exacte_sans_mutation_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(status="exacte", preuve_locale="lot_identifie")


def test_exacte_avec_preuve_locale_et_mutation_est_constructible():
    """The only door to ``exacte`` -- closed today by the absence of a proof
    SOURCE, not by the absence of a state (Phase 58 owns that source)."""
    decision = DvfAttribution(
        status="exacte",
        mutation=mutation_candidate(),
        preuve_locale="lot_identifie",
    )
    assert decision.status == "exacte"
    assert decision.preuve_locale in DVF_PREUVES_LOCALES


def test_compatible_parcelle_avec_preuve_locale_est_inconstructible():
    """A local proof would make it exact; carrying one here is a contradiction."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="compatible_parcelle",
            mutation=mutation_candidate(),
            preuve_locale="lot_identifie",
        )


@pytest.mark.parametrize("statut", ["non_attribuable", "absente", "error"])
def test_absence_portant_une_mutation_est_inconstructible(statut: str):
    """An absence never carries a price: the history lives in datasets.dvf."""
    raison = {
        "non_attribuable": "vente_ensemble",
        "absente": "aucune_mutation",
        "error": "temporary_error",
    }[statut]
    with pytest.raises(ValidationError):
        DvfAttribution(status=statut, reason=raison, mutation=mutation_candidate())


@pytest.mark.parametrize("statut", ["non_attribuable", "absente", "error"])
def test_absence_sans_raison_est_inconstructible(statut: str):
    with pytest.raises(ValidationError):
        DvfAttribution(status=statut)


def test_raison_hors_vocabulaire_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(status="non_attribuable", reason="le voisin a vendu")


def test_raison_inadmissible_pour_le_statut_est_inconstructible():
    """``aucune_mutation`` is an absence, never a non-attributable mutation."""
    with pytest.raises(ValidationError):
        DvfAttribution(status="non_attribuable", reason="aucune_mutation")


def test_etat_positif_avec_raison_est_inconstructible():
    """The payload IS the answer: a positive state explains nothing away."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="compatible_parcelle",
            mutation=mutation_candidate(),
            reason="type_incompatible",
        )


def test_champ_supplementaire_est_inconstructible():
    """``extra="forbid"``: a raw row is never handed to this model by accident."""
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente",
            reason="aucune_mutation",
            prix_certain_du_bien=250000.0,
        )


def test_preuve_locale_hors_vocabulaire_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="exacte",
            mutation=mutation_candidate(),
            preuve_locale="parcelle_exacte",
        )


def test_evidence_hors_vocabulaire_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="compatible_parcelle",
            mutation=mutation_candidate(),
            evidence=["adresse_proche"],
        )


def test_evidence_repetee_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="compatible_parcelle",
            mutation=mutation_candidate(),
            evidence=["mono_local", "mono_local"],
        )


@pytest.mark.parametrize(
    "champ,valeur",
    [
        ("perimetre_ensemble", True),
        ("nb_locaux_residentiel", 2),
        ("nb_locaux_commercial", 1),
        ("nb_lots_source", 3),
    ],
)
def test_absente_portant_un_perimetre_est_inconstructible(champ: str, valeur):
    """``absente`` describes NO mutation: it cannot describe one's perimeter."""
    with pytest.raises(ValidationError):
        DvfAttribution(status="absente", reason="aucune_mutation", **{champ: valeur})


def test_absente_portant_une_preuve_verifiee_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(
            status="absente", reason="aucune_mutation", evidence=["mono_local"]
        )


def test_rule_version_vide_est_inconstructible():
    with pytest.raises(ValidationError):
        DvfAttribution(status="absente", reason="aucune_mutation", rule_version="")


def test_toute_decision_porte_la_version_de_regle():
    decision = DvfAttribution(status="absente", reason="aucune_mutation")
    assert decision.rule_version == DVF_ATTRIBUTION_RULE_VERSION
    assert decision.rule_version, "la version de règle est publiée avec la décision"


def test_la_version_de_regle_est_celle_du_plan_courant():
    """D-47: the VEFA reason split changed a published outcome, so the version
    moved with it. Asserting the LITERAL is the point -- comparing the constant
    to itself would let a silent bump through."""
    assert DVF_ATTRIBUTION_RULE_VERSION == "57-17/D-47"


def test_le_vocabulaire_des_raisons_couvre_les_cas_du_plan():
    """The seven reasons the plan names by hand, plus the ones it delegates."""
    attendues = {
        "aucune_mutation",
        "derniere_vente_mixte",
        "derniere_vente_commerciale",
        "vente_ensemble",
        "type_incompatible",
        "vefa_sans_surface",
        "vente_sur_plan",
        "nature_hors_vente",
        "type_bien_inconnu",
    }
    assert attendues <= set(DVF_ATTRIBUTION_RAISONS)
    assert all(libelle.strip() for libelle in DVF_ATTRIBUTION_RAISONS.values())
    assert "parcelle_exacte" in DVF_EVIDENCES


# --- Level 2: the decision matrix (Task 1, 10 behaviours) --------------------

_COMMERCIALE = dict(
    id_mutation="CASE-DVF-COM",
    date_mutation=date(2025, 3, 1),
    type_local=None,
    code_type_local=None,
    classe_mutation="commerciale",
    prix_m2=None,
    prix_m2_raison="mutation_commerciale",
    nb_locaux_appartement=0,
    nb_locaux_commercial=1,
)

_MIXTE = dict(
    id_mutation="CASE-DVF-MIX",
    date_mutation=date(2025, 3, 1),
    type_local=None,
    code_type_local=None,
    classe_mutation="mixte",
    mutation_mono_local=False,
    prix_m2=None,
    prix_m2_raison="vente_mixte",
    nb_locaux_appartement=1,
    nb_locaux_commercial=1,
    nb_lots_source=None,
)

_ENSEMBLE = dict(
    id_mutation="CASE-DVF-ENS",
    date_mutation=date(2025, 3, 1),
    type_local=None,
    code_type_local=None,
    classe_mutation="ensemble_residentiel",
    mutation_mono_local=False,
    prix_m2=None,
    prix_m2_raison="mutation_multi_locaux",
    nb_locaux_appartement=4,
    nb_locaux_commercial=0,
    nb_lots_source=None,
)

_MAISON = dict(
    id_mutation="CASE-DVF-MAI",
    type_local="Maison",
    code_type_local="1",
    classe_mutation="residentiel_maison",
    nb_locaux_maison=1,
    nb_locaux_appartement=0,
    surface_bati_totale=110,
    surface_reelle_bati=110,
)

_ADJUDICATION = dict(
    id_mutation="CASE-DVF-ADJ",
    nature_mutation="Adjudication",
    prix_m2=None,
    prix_m2_raison="nature_hors_vente",
)

# The whole matrix read as ONE table by the invariant tests below: whatever the
# input, `exacte` is unreachable (D-37) and an absence never carries a price.
_MATRICE: tuple[tuple, ...] = (
    ([mutation(**_COMMERCIALE), mutation()], "appartement"),
    ([mutation(**_MIXTE)], "appartement"),
    ([mutation(**_COMMERCIALE)], "appartement"),
    ([mutation(**_ENSEMBLE)], "appartement"),
    ([mutation(**_MAISON)], "appartement"),
    ([mutation(**_MAISON)], "maison"),
    ([mutation()], "appartement"),
    ([mutation()], "maison"),
    ([mutation(**_ADJUDICATION)], "appartement"),
    ([], "appartement"),
    ([mutation()], None),
    (None, "appartement"),
    ([{"id_mutation": "CASE-DVF-TRONQUE"}], "appartement"),
)


def test_pornichet_une_vente_commerciale_recente_ne_devient_pas_le_kpi():
    """CASE-DVF-ATTR-01 (Pornichet, DVF-04/D-37): the ground-floor shop sold
    last year is the most recent mutation of the parcel. It must never become
    the apartment's price -- and the older compatible flat is a CANDIDATE on
    this parcel, not "the sale of this bien"."""
    decision = decide_dvf_attribution(
        [mutation(**_COMMERCIALE), mutation()],
        type_bien="appartement",
    )

    assert decision.status == "compatible_parcelle", decision.reason
    assert decision.mutation is not None
    assert decision.mutation.id_mutation == "CASE-DVF-0001", (
        "la mutation commerciale plus récente ne doit jamais être retenue"
    )
    assert decision.status != "exacte"
    assert decision.preuve_locale is None
    assert "type_compatible" in decision.evidence


def test_vente_mixte_seule_est_non_attribuable():
    """CASE-DVF-ATTR-02 (DVF-05): a mixed sale is never attributed to a lot."""
    decision = decide_dvf_attribution([mutation(**_MIXTE)], type_bien="appartement")

    assert decision.status == "non_attribuable"
    assert decision.reason == "derniere_vente_mixte"
    assert decision.mutation is None
    assert decision.perimetre_ensemble is True


def test_vente_commerciale_seule_est_non_attribuable():
    """CASE-DVF-ATTR-03: nothing residential to attribute at all."""
    decision = decide_dvf_attribution(
        [mutation(**_COMMERCIALE)], type_bien="appartement"
    )

    assert decision.status == "non_attribuable"
    assert decision.reason == "derniere_vente_commerciale"
    assert decision.mutation is None


def test_vente_ensemble_est_non_attribuable_et_expose_son_perimetre():
    """CASE-DVF-ATTR-04 (DVF-03/D-11): a block sale stays listable in the
    history, with the counters that let it be labelled « vente d'ensemble —
    N locaux, prix global » -- never a lot's price."""
    decision = decide_dvf_attribution([mutation(**_ENSEMBLE)], type_bien="appartement")

    assert decision.status == "non_attribuable"
    assert decision.reason == "vente_ensemble"
    assert decision.mutation is None
    assert decision.perimetre_ensemble is True
    assert decision.nb_locaux_residentiel == 4
    assert decision.nb_locaux_commercial == 0


def test_vente_maison_ne_nourrit_jamais_une_fiche_appartement():
    """CASE-DVF-ATTR-05 (D-10): strict type compatibility, both ways."""
    decision = decide_dvf_attribution([mutation(**_MAISON)], type_bien="appartement")

    assert decision.status == "non_attribuable"
    assert decision.reason == "type_incompatible"
    assert decision.mutation is None


def test_vente_appartement_ne_nourrit_jamais_une_fiche_maison():
    """CASE-DVF-ATTR-06 (D-10, réciproque)."""
    decision = decide_dvf_attribution([mutation()], type_bien="maison")

    assert decision.status == "non_attribuable"
    assert decision.reason == "type_incompatible"


def test_fiche_maison_mono_local_reste_compatible_parcelle_avec_sa_surface():
    """CASE-DVF-ATTR-07 (D-38): the ideal maison case still stops at parcel
    compatibility in Phase 57. The candidate surface travels INSIDE the
    candidate mutation, for Phase 58 to decide on -- no exact superficie."""
    decision = decide_dvf_attribution([mutation(**_MAISON)], type_bien="maison")

    assert decision.status == "compatible_parcelle"
    assert decision.status != "exacte", "aucune bascule exacte en 57 (D-38)"
    assert decision.mutation is not None
    assert decision.mutation.surface_bati_totale == 110
    assert decision.preuve_locale is None
    assert set(decision.evidence) <= set(DVF_EVIDENCES)


def test_deux_appartements_sur_la_parcelle_ne_produisent_jamais_exacte():
    """CASE-DVF-ATTR-08 (T-57-45): two mono-local flats sold on the same
    parcel. Neither identifies the consulted dwelling -- the most recent one
    is a candidate, never an attribution."""
    recente = mutation(id_mutation="CASE-DVF-A", date_mutation=date(2025, 1, 10))
    ancienne = mutation(id_mutation="CASE-DVF-B", date_mutation=date(2021, 5, 4))

    decision = decide_dvf_attribution([recente, ancienne], type_bien="appartement")

    assert decision.status == "compatible_parcelle"
    assert decision.status != "exacte"
    assert decision.preuve_locale is None
    assert decision.mutation is not None
    assert decision.mutation.id_mutation == "CASE-DVF-A"


def test_vefa_mono_local_sans_surface_ne_produit_aucun_prix_au_m2():
    """CASE-DVF-ATTR-09 (DVF-06): a VEFA without surface is identified as such
    and yields no price per m2 -- and no attribution either (D-13)."""
    vefa = mutation(
        id_mutation="CASE-DVF-VEFA",
        nature_mutation="Vente en l'état futur d'achèvement",
        hors_stats_vefa=True,
        surface_bati_totale=None,
        surface_reelle_bati=None,
        prix_m2=None,
        prix_m2_raison="surface_absente",
    )

    decision = decide_dvf_attribution([vefa], type_bien="appartement")

    assert decision.status == "non_attribuable"
    assert decision.reason == "vefa_sans_surface"
    assert decision.mutation is None


def test_vefa_avec_surface_reste_hors_du_perimetre_vente():
    """CASE-DVF-ATTR-10 (D-12/D-13, D-47): a VEFA carrying a surface is STILL
    never a candidate -- but the absence is named for what it is. Answering
    « ce n'est pas une vente » was false: a VEFA IS a sale, of another market."""
    vefa = mutation(
        id_mutation="CASE-DVF-VEFA2",
        nature_mutation="Vente en l'état futur d'achèvement",
        hors_stats_vefa=True,
    )

    decision = decide_dvf_attribution([vefa], type_bien="appartement")

    assert decision.status == "non_attribuable"
    assert decision.reason == "vente_sur_plan"
    assert decision.mutation is None


def test_une_vefa_avec_surface_ne_prend_jamais_le_pas_sur_une_vente_classique():
    """D-40 negative proof of D-12/D-13: the VEFA is the MOST RECENT mutation
    of the parcel and carries a surface, yet the scan walks past it and retains
    the older ordinary sale. A VEFA is never a candidate -- if this test ever
    goes green on `CASE-DVF-VEFA3`, the doctrine has been reintroduced."""
    vefa = mutation(
        id_mutation="CASE-DVF-VEFA3",
        date_mutation=date(2025, 4, 2),
        nature_mutation="Vente en l'état futur d'achèvement",
        hors_stats_vefa=True,
    )
    vente = mutation(id_mutation="CASE-DVF-VENTE", date_mutation=date(2022, 9, 8))

    decision = decide_dvf_attribution([vefa, vente], type_bien="appartement")

    assert decision.status == "compatible_parcelle"
    assert decision.mutation is not None
    assert decision.mutation.id_mutation == "CASE-DVF-VENTE"


def test_une_vefa_sans_surface_reste_nommee_par_sa_surface_manquante():
    """D-47 splits the VEFA absence in two, it does not merge it: without a
    surface the headline stays the missing surface (DVF-06), not the sale."""
    vefa = mutation(
        id_mutation="CASE-DVF-VEFA4",
        nature_mutation="Vente en l'état futur d'achèvement",
        hors_stats_vefa=True,
        surface_bati_totale=None,
        surface_reelle_bati=None,
        prix_m2=None,
        prix_m2_raison="surface_absente",
    )

    decision = decide_dvf_attribution([vefa], type_bien="appartement")

    assert decision.reason == "vefa_sans_surface"
    assert decision.reason != "vente_sur_plan"


@pytest.mark.parametrize(
    "nature", ["Adjudication", "Echange", "Expropriation", "Vente terrain à bâtir"]
)
def test_nature_hors_vente_est_non_attribuable(nature: str):
    """CASE-DVF-ATTR-11 (D-13): visible in the history, never a KPI."""
    hors_vente = mutation(**{**_ADJUDICATION, "nature_mutation": nature})
    decision = decide_dvf_attribution([hors_vente], type_bien="appartement")

    assert decision.status == "non_attribuable"
    assert decision.reason == "nature_hors_vente"


def test_aucune_mutation_produit_une_absence_expliquee():
    """CASE-DVF-ATTR-12 (D-16): the answer carries the absence AND its reason."""
    decision = decide_dvf_attribution([], type_bien="appartement")

    assert decision.status == "absente"
    assert decision.reason == "aucune_mutation"
    assert decision.mutation is None
    assert DVF_ATTRIBUTION_RAISONS[decision.reason]


@pytest.mark.parametrize(
    "entree",
    [
        None,
        "une chaine",
        [object()],
        [{"id_mutation": "CASE-DVF-TRONQUE"}],
        [mutation(classe_mutation="classe_inventee")],
        42,
    ],
    ids=["none", "chaine", "objet", "ligne_tronquee", "classe_inconnue", "entier"],
)
def test_entree_illisible_ferme_sans_exception(entree):
    """CASE-DVF-ATTR-13 (T-57-48): a degraded input closes conservatively and
    never raises -- /v1/resolve must not break on one dirty record."""
    decision = decide_dvf_attribution(entree, type_bien="appartement")

    assert decision.status in {"error", "non_attribuable", "absente"}
    assert decision.mutation is None
    assert decision.reason is not None


def test_type_de_bien_inconnu_ne_devine_jamais():
    """CASE-DVF-ATTR-14: without a resolved dwelling type, compatibility cannot
    be established -- the decision says so rather than picking at random."""
    decision = decide_dvf_attribution([mutation()], type_bien=None)

    assert decision.status == "non_attribuable"
    assert decision.reason == "type_bien_inconnu"
    assert decision.mutation is None


def test_le_candidat_est_le_plus_recent_des_compatibles():
    """CASE-DVF-ATTR-15: the scan walks from most recent to oldest and stops at
    the first compatible mutation -- ordering is the caller's contract."""
    decision = decide_dvf_attribution(
        [
            mutation(**_MIXTE),
            mutation(id_mutation="CASE-DVF-RECENT", date_mutation=date(2023, 7, 1)),
            mutation(id_mutation="CASE-DVF-VIEUX", date_mutation=date(2019, 2, 1)),
        ],
        type_bien="appartement",
    )

    assert decision.status == "compatible_parcelle"
    assert decision.mutation is not None
    assert decision.mutation.id_mutation == "CASE-DVF-RECENT"


def test_une_absence_ne_porte_jamais_de_mutation_sur_toute_la_matrice():
    """CASE-DVF-ATTR-16: the invariant read across every scenario at once."""
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        if decision.status in {"non_attribuable", "absente", "error"}:
            assert decision.mutation is None, decision
            assert decision.reason is not None, decision
        else:
            assert decision.mutation is not None, decision
            assert decision.reason is None, decision


def test_aucun_scenario_ne_produit_jamais_une_attribution_exacte():
    """CASE-DVF-ATTR-17 (D-37, le test central de ce plan): no local/lot proof
    source exists in Phase 57, so the ideal mono-local compatible sale -- with
    its surface, its type and its « Vente » nature -- stops at
    compatible_parcelle. `exacte` is unreachable from this data."""
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        assert decision.status != "exacte", (entree, type_bien)
        assert decision.preuve_locale is None, (entree, type_bien)


def test_toute_decision_publie_sa_version_de_regle():
    """CASE-DVF-ATTR-18 (D-37): rule/version explicable on every outcome."""
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        assert decision.rule_version == DVF_ATTRIBUTION_RULE_VERSION


def test_une_raison_publiee_appartient_toujours_au_vocabulaire_ferme():
    """CASE-DVF-ATTR-19: never a free string, on any path."""
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        if decision.reason is not None:
            assert decision.reason in DVF_ATTRIBUTION_RAISONS


def test_la_decision_accepte_indifferemment_dicts_et_resultatdvf():
    """CASE-DVF-ATTR-20: the caller may hand rows or projected models."""
    lignes = [mutation(**_COMMERCIALE), mutation()]
    modeles = [ResultatDvf.model_validate(ligne) for ligne in lignes]

    depuis_dicts = decide_dvf_attribution(lignes, type_bien="appartement")
    depuis_modeles = decide_dvf_attribution(modeles, type_bien="appartement")

    assert depuis_dicts.model_dump() == depuis_modeles.model_dump()


def test_le_module_de_decision_ne_fait_aucune_io():
    """CASE-DVF-ATTR-21: purity read on the source itself, not on intentions."""
    source = Path(attribution.__file__).read_text(encoding="utf-8")
    imports = [
        ligne
        for ligne in source.splitlines()
        if ligne.startswith("import ") or ligne.startswith("from ")
    ]
    assert imports, "le module doit importer quelque chose"
    for interdit in ("sqlalchemy", "psycopg", "httpx", "app.db", "app.database"):
        assert not any(interdit in ligne for ligne in imports), interdit


def test_le_docstring_cite_les_decisions_qui_le_fondent():
    """CASE-DVF-ATTR-22: acceptance criterion of the plan, read literally."""
    docstring = attribution.__doc__ or ""
    for reference in ("D-10", "D-11", "D-13", "D-16", "D-37", "DVF-04"):
        assert reference in docstring, reference


# --- Provenance du type déclaré (Phase 57 plan 18, D-41) --------------------


def test_la_matrice_entiere_trace_la_provenance_du_type():
    """CASE-DVF-ATTR-23 (D-41): sur TOUTE la matrice, la provenance dit d'où
    vient le type — déclaré ou inconnu — et ne peut jamais se contredire.

    Le détail par état vit dans ``test_resolve_dvf_type_bien.py`` ; ce test-ci
    verrouille l'invariant sur l'ensemble des scénarios déjà couverts.
    """
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        declare = type_bien in attribution.TYPES_BIEN_ADMIS
        assert decision.type_bien_source == ("utilisateur" if declare else "inconnu"), (
            entree,
            type_bien,
        )
        assert ("type_bien_fourni_utilisateur" in decision.evidence) is declare, (
            entree,
            type_bien,
        )


def test_un_type_declare_ne_promeut_jamais_en_exacte_sur_la_matrice():
    """CASE-DVF-ATTR-24 (D-41/D-37): la déclaration change ce qu'on peut DIRE,
    jamais le niveau de preuve. Aucune ligne de la matrice ne bascule."""
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        if decision.type_bien_source == "utilisateur":
            assert decision.status != "exacte", (entree, type_bien)
            assert decision.preuve_locale is None, (entree, type_bien)


def test_le_vocabulaire_des_types_a_une_seule_source():
    """CASE-DVF-ATTR-25 (D-41): le contrat porte LE vocabulaire, la politique le
    re-exporte et le couvre exactement. Deux listes qui divergent d'une version
    est la classe de defaut que cette phase ferme -- ce test la garde fermee."""
    assert attribution.TYPES_BIEN_ADMIS is DVF_TYPES_BIEN_CONSULTES
    assert tuple(attribution.CLASSE_PAR_TYPE_BIEN) == DVF_TYPES_BIEN_CONSULTES


def test_la_matrice_entiere_publie_la_valeur_du_type_consulte():
    """CASE-DVF-ATTR-26 (D-41): la provenance est COMPLETE sur toute la matrice
    -- source, valeur et trace, ou rien. Publier « utilisateur » sans dire QUEL
    type laissait le lecteur deviner sur quoi la decision avait ete prise."""
    for entree, type_bien in _MATRICE:
        decision = decide_dvf_attribution(entree, type_bien=type_bien)
        declare = type_bien in attribution.TYPES_BIEN_ADMIS
        attendu = type_bien if declare else None
        assert decision.type_bien_consulte == attendu, (entree, type_bien)


def test_la_provenance_hors_module_est_complete_aussi():
    """CASE-DVF-ATTR-27 (D-41): le bloc de provenance publie par
    ``provenance_type_bien`` -- le seul chemin supporte pour un etat construit
    HORS de la politique -- porte les trois champs, et rien de plus."""
    declare = attribution.provenance_type_bien("appartement")
    assert declare == {
        "type_bien_source": "utilisateur",
        "type_bien_consulte": "appartement",
        "evidence": ["type_bien_fourni_utilisateur"],
    }
    assert attribution.provenance_type_bien(None) == {
        "type_bien_source": "inconnu",
        "type_bien_consulte": None,
        "evidence": [],
    }
    # Une valeur inventee n'est PAS une declaration : elle ne trace rien.
    assert attribution.provenance_type_bien("loft")["type_bien_source"] == "inconnu"
