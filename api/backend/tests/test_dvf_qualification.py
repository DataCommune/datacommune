"""Wave 0 matrix of the pure DVF qualification policy (Phase 57, D-01/D-05/D-10..D-13).

Two functions are proven here, both without any database or network access:

- ``compte_lignes`` -- reduces the rows of ONE ``id_mutation`` (already grouped
  upstream) into flat counters: locals by type, surfaces summed, the mutation
  price counted ONCE regardless of how many rows carry it, and a divergence
  guard that logs without ever raising (Pattern 1, RESEARCH.md).
- ``qualifie_mutation`` -- the closed taxonomy matrix: mixte, commerciale,
  residentiel (maison/appartement), ensemble_residentiel, terrain_seul,
  hors_perimetre, plus the orthogonal VEFA/vente flags and the honest
  ``prix_m2``/``prix_m2_raison`` pair (D-15).

D-31 doctrine (anonymized fixtures): every factory below reproduces a
CONFIGURATION measured on real geo-dvf data (row counts, types, surfaces,
nature) -- never a real address, street name or ``id_parcelle``. Identifiers
are deliberately synthetic (``SYN-...``) so no fixture could ever be mistaken
for a real record. Test names citing a phase's own public bug report (e.g. a
mixed-use configuration already discussed by name in this repo's own
committed planning documents) narrate WHY a scenario matters -- they never
carry fixture DATA drawn from a real address.
"""

from decimal import Decimal

import pytest

pytestmark = pytest.mark.unit

from app.etl_dvf.qualification import (
    ComptesLocaux,
    compte_lignes,
    compte_locaux,
    qualifie_mutation,
)

# ---------------------------------------------------------------------------
# Synthetic row factories (geo-dvf CSV shape, D-31: never a real record)
# ---------------------------------------------------------------------------

_ID_MUTATION = "SYN-2024-000001"
_PARCELLE = "SYN-PARCELLE-0001"
_COMMUNE = "00000"


def _ligne_de_base(**overrides) -> dict:
    champs: dict = {
        "id_mutation": _ID_MUTATION,
        "numero_disposition": "000001",
        "nature_mutation": "Vente",
        "valeur_fonciere": Decimal("316000"),
        "code_type_local": None,
        "type_local": None,
        "surface_reelle_bati": None,
        "nombre_pieces_principales": None,
        "surface_terrain": None,
        "nombre_lots": None,
        "code_commune": _COMMUNE,
        "id_parcelle": _PARCELLE,
    }
    champs.update(overrides)
    return champs


def _ligne_maison(**overrides) -> dict:
    champs: dict = {
        "code_type_local": "1",
        "type_local": "Maison",
        "surface_reelle_bati": 90,
        "nombre_pieces_principales": 4,
    }
    champs.update(overrides)
    return _ligne_de_base(**champs)


def _ligne_appartement(**overrides) -> dict:
    champs: dict = {
        "code_type_local": "2",
        "type_local": "Appartement",
        "surface_reelle_bati": 71,
        "nombre_pieces_principales": 3,
    }
    champs.update(overrides)
    return _ligne_de_base(**champs)


def _ligne_commerce(**overrides) -> dict:
    champs: dict = {
        "code_type_local": "4",
        "type_local": "Local industriel et commercial ou assimiles",
        "surface_reelle_bati": 40,
    }
    champs.update(overrides)
    return _ligne_de_base(**champs)


def _ligne_dependance(**overrides) -> dict:
    champs: dict = {
        "code_type_local": "3",
        "type_local": "Dependance",
        "surface_reelle_bati": None,
    }
    champs.update(overrides)
    return _ligne_de_base(**champs)


def _ligne_terrain(**overrides) -> dict:
    champs: dict = {
        "code_type_local": None,
        "type_local": None,
        "surface_reelle_bati": None,
        "surface_terrain": 500,
    }
    champs.update(overrides)
    return _ligne_de_base(**champs)


def _qualifie(lignes: list[dict]):
    """Compose the two public functions -- the shape every real caller uses."""
    return qualifie_mutation(compte_lignes(lignes))


# ---------------------------------------------------------------------------
# Bloc 1 -- compte_lignes (DVF-01/DVF-02, Pattern 1 et Pitfall 3)
# ---------------------------------------------------------------------------


def test_prix_compte_une_fois():
    """DVF-01: 3 lignes d'une meme mutation partagent le meme prix -- il n'est
    jamais multiplie par le nombre de lignes."""
    lignes = [
        _ligne_appartement(),
        _ligne_dependance(),
        _ligne_dependance(),
    ]

    compteurs = compte_lignes(lignes)

    assert compteurs.valeur_fonciere == Decimal("316000")
    assert compteurs.valeur_divergente is False
    assert compteurs.nb_lignes == 3


def test_pas_de_count_star_pour_lots():
    """Pitfall 3: nombre_lots varie par ligne au sein d'une meme disposition --
    il n'est ni somme ni maximise, et n'existe meme pas dans le contrat."""
    lignes = [
        _ligne_appartement(numero_disposition="000001", nombre_lots=2),
        _ligne_dependance(numero_disposition="000001", nombre_lots=1),
        _ligne_dependance(numero_disposition="000001", nombre_lots=2),
    ]

    compteurs = compte_lignes(lignes)

    assert not hasattr(compteurs, "nombre_lots")
    assert compteurs.nb_lignes == 3
    assert compteurs.surface_bati_totale == 71


def test_valeur_divergente_entre_dispositions_est_comptee(caplog):
    """Pattern 1: une divergence de valeur_fonciere entre dispositions d'une
    meme mutation est loguee et retenue au maximum, jamais une exception."""
    lignes = [
        _ligne_maison(numero_disposition="000001", valeur_fonciere=Decimal("140000")),
        _ligne_maison(numero_disposition="000002", valeur_fonciere=Decimal("150000")),
    ]

    with caplog.at_level("WARNING"):
        compteurs = compte_lignes(lignes)

    assert compteurs.valeur_divergente is True
    assert compteurs.valeur_fonciere == Decimal("150000")
    assert any(_ID_MUTATION in message for message in caplog.messages)


def test_valeur_fonciere_nulle_est_une_valeur_et_non_une_absence():
    """Un prix declare a 0 est une valeur (comme dans la reconstruction SQL,
    ``max``/``count(DISTINCT)``), jamais confondu avec un prix absent."""
    compteurs = compte_lignes([_ligne_maison(valeur_fonciere=Decimal("0"))])

    assert compteurs.valeur_fonciere == Decimal("0")
    assert compteurs.valeur_divergente is False

    divergente = compte_lignes(
        [
            _ligne_maison(numero_disposition="000001", valeur_fonciere=Decimal("0")),
            _ligne_maison(
                numero_disposition="000002", valeur_fonciere=Decimal("150000")
            ),
        ]
    )
    assert divergente.valeur_divergente is True
    assert divergente.valeur_fonciere == Decimal("150000")


def test_valeur_fonciere_absente_reste_absente():
    """Aucune ligne ne porte de prix : None, et aucune divergence."""
    compteurs = compte_lignes(
        [
            _ligne_maison(numero_disposition="000001", valeur_fonciere=None),
            _ligne_maison(numero_disposition="000002", valeur_fonciere=None),
        ]
    )

    assert compteurs.valeur_fonciere is None
    assert compteurs.valeur_divergente is False


def test_valeur_fonciere_positive_ignore_une_ligne_sans_prix():
    """Une ligne sans prix n'entre pas dans les valeurs distinctes."""
    compteurs = compte_lignes(
        [
            _ligne_maison(numero_disposition="000001", valeur_fonciere=None),
            _ligne_maison(
                numero_disposition="000002", valeur_fonciere=Decimal("150000")
            ),
        ]
    )

    assert compteurs.valeur_fonciere == Decimal("150000")
    assert compteurs.valeur_divergente is False


def test_compte_lignes_entree_vide_ne_leve_jamais():
    """Entree degradee (liste vide) : compte_lignes ferme prudemment, jamais
    d'exception, et qualifie_mutation retombe sur hors_perimetre."""
    compteurs = compte_lignes([])
    resultat = qualifie_mutation(compteurs)

    assert compteurs.nb_lignes == 0
    assert resultat.classe_mutation == "hors_perimetre"
    assert resultat.prix_m2 is None


# ---------------------------------------------------------------------------
# Bloc 2 -- qualifie_mutation (D-10..D-13, matrice de classification)
# ---------------------------------------------------------------------------


def test_residentiel_appartement_avec_dependances():
    """Configuration mesuree sur le corpus reel dept 44/2024 (appartement 71 m2
    + 2 dependances sans surface) -- classee residentiel_appartement, prix
    honnete malgre les dependances."""
    lignes = [
        _ligne_appartement(),
        _ligne_dependance(),
        _ligne_dependance(),
    ]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "residentiel_appartement"
    assert resultat.mono_local is True
    assert resultat.est_vente is True
    assert resultat.hors_stats_vefa is False
    assert resultat.prix_m2 is not None
    assert resultat.prix_m2_raison is None


def test_residentiel_maison():
    resultat = _qualifie([_ligne_maison()])

    assert resultat.classe_mutation == "residentiel_maison"
    assert resultat.mono_local is True
    assert resultat.prix_m2 is not None
    assert resultat.prix_m2_raison is None


def test_ensemble_residentiel():
    """D-11: plusieurs locaux residentiels sous une meme mutation ne donnent
    jamais un prix de bien isole."""
    lignes = [_ligne_appartement(surface_reelle_bati=45 + i) for i in range(8)]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "ensemble_residentiel"
    assert resultat.mono_local is False
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "mutation_multi_locaux"


def test_classe_mixte():
    """D-05/D-10: un local residentiel et un local commercial sous le meme
    id_mutation ferment la classe sur mixte, jamais attribuable."""
    lignes = [_ligne_appartement(), _ligne_commerce()]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "mixte"
    assert resultat.mono_local is False
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "vente_mixte"


def test_pornichet_non_attribuable():
    """DVF-04/DVF-05: configuration reproduisant le signalement d'un commerce
    au rez-de-chaussee sous le meme id_mutation qu'un ou plusieurs logements a
    l'etage -- jamais un KPI logement, quel que soit le nombre de locaux
    residentiels impliques."""
    lignes = [
        _ligne_appartement(numero_disposition="000001"),
        _ligne_appartement(numero_disposition="000001", surface_reelle_bati=54),
        _ligne_commerce(numero_disposition="000001"),
    ]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "mixte"
    assert resultat.mono_local is False
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "vente_mixte"


def test_commerciale():
    lignes = [_ligne_commerce(), _ligne_commerce(surface_reelle_bati=80)]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "commerciale"
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "mutation_commerciale"


def test_vefa_sans_surface():
    """DVF-06: 97,7% des lignes VEFA du corpus mesure n'ont pas de
    surface_reelle_bati -- aucun prix/m2 n'est invente."""
    lignes = [
        _ligne_appartement(
            nature_mutation="Vente en l'état futur d'achèvement",
            surface_reelle_bati=None,
        )
    ]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "residentiel_appartement"
    assert resultat.hors_stats_vefa is True
    assert resultat.est_vente is False
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "surface_absente"


def test_vefa_avec_surface_reste_hors_stats():
    """Le flag hors_stats_vefa est orthogonal a la classe et au prix_m2 : une
    VEFA avec surface reste hors stats agregees (D-12) mais son prix au grain
    mutation reste calculable."""
    lignes = [
        _ligne_appartement(
            nature_mutation="Vente en l'état futur d'achèvement",
            surface_reelle_bati=65,
        )
    ]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "residentiel_appartement"
    assert resultat.hors_stats_vefa is True
    assert resultat.prix_m2 is not None
    assert resultat.prix_m2_raison is None


def test_terrain_seul():
    resultat = _qualifie([_ligne_terrain()])

    assert resultat.classe_mutation == "terrain_seul"
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "terrain_seul"


def test_adjudication_mono_local_reste_non_vendue():
    """D-13: le perimetre KPI/stats est nature_mutation == 'Vente' seule --
    une adjudication garde sa classe residentielle mais aucun prix."""
    lignes = [_ligne_maison(nature_mutation="Adjudication")]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "residentiel_maison"
    assert resultat.mono_local is True
    assert resultat.est_vente is False
    assert resultat.hors_stats_vefa is False
    assert resultat.prix_m2 is None
    assert resultat.prix_m2_raison == "nature_hors_vente"


def test_qualifie_mutation_code_type_local_inconnu_ferme_prudemment():
    """Entree degradee (code_type_local inconnu) : fermeture prudente vers
    hors_perimetre, jamais d'exception."""
    lignes = [_ligne_de_base(code_type_local="9", type_local="Autre")]

    resultat = _qualifie(lignes)

    assert resultat.classe_mutation == "hors_perimetre"
    assert resultat.mono_local is False


# ---------------------------------------------------------------------------
# Bloc 3 -- compte_locaux (phase 04.4, D-02 : un local repete par parcelle)
# ---------------------------------------------------------------------------


def _local(
    parcelle: str | None,
    code_type: str | None = "1",
    surface: int | None = 131,
    pieces: int | None = 5,
    disposition: str | None = "000002",
    culture: str | None = None,
    terrain: int | None = None,
) -> tuple:
    """One ``(disposition, type, surface, pieces, parcelle, culture,
    terrain)`` tuple (D-31)."""
    return (disposition, code_type, surface, pieces, parcelle, culture, terrain)


def test_local_repete_sur_deux_parcelles_compte_une_fois():
    """D-02 : la source n'a pas d'identifiant de local ; la meme maison
    repetee sur deux parcelles de la mutation est un seul local, sa surface et
    ses pieces comptent une fois."""
    comptes = compte_locaux([_local("SYN-P2"), _local("SYN-P3")])

    assert comptes == ComptesLocaux(
        nb_locaux_maison=1,
        nb_locaux_appartement=0,
        nb_locaux_commercial=0,
        nb_locaux_dependance=0,
        surface_bati_totale=131,
        nombre_pieces_totales=5,
        surface_terrain_totale=None,
    )


def test_deux_lignes_identiques_sur_une_meme_parcelle_restent_deux_locaux():
    """D-02 : deux lots identiques d'un programme sur une meme parcelle."""
    comptes = compte_locaux(
        [_local("SYN-P1", "2", 45, 2), _local("SYN-P1", "2", 45, 2)]
    )

    assert comptes.nb_locaux_appartement == 2
    assert comptes.surface_bati_totale == 90
    assert comptes.nombre_pieces_totales == 4


def test_multiplicite_est_le_maximum_par_parcelle():
    """Deux identiques sur chacune de deux parcelles : 2 locaux, pas 4."""
    locaux = [_local(p, "2", 45, 2) for p in ("SYN-P1", "SYN-P1", "SYN-P2", "SYN-P2")]

    comptes = compte_locaux(locaux)

    assert comptes.nb_locaux_appartement == 2
    assert comptes.surface_bati_totale == 90


def test_dispositions_differentes_restent_deux_locaux():
    comptes = compte_locaux(
        [_local("SYN-P1", disposition="000001"), _local("SYN-P2", disposition="000002")]
    )

    assert comptes.nb_locaux_maison == 2
    assert comptes.surface_bati_totale == 262


def test_surfaces_differentes_restent_deux_locaux():
    comptes = compte_locaux(
        [_local("SYN-P1", surface=131), _local("SYN-P2", surface=90)]
    )

    assert comptes.nb_locaux_maison == 2
    assert comptes.surface_bati_totale == 221


def test_dependances_sans_surface_repetees_sur_deux_parcelles_comptent_une_fois():
    comptes = compte_locaux(
        [_local("SYN-P1", "3", None, 0), _local("SYN-P2", "3", None, 0)]
    )

    assert comptes.nb_locaux_dependance == 1
    assert comptes.surface_bati_totale is None
    assert comptes.nombre_pieces_totales == 0


def test_ligne_sans_type_surface_ni_pieces_ne_contribue_pas():
    comptes = compte_locaux([_local("SYN-P1", None, None, None)])

    assert comptes == ComptesLocaux(0, 0, 0, 0, None, None, None)


def test_parcelle_absente_est_une_valeur_de_parcelle():
    """``id_parcelle`` NULL est une parcelle comme une autre : repete sur NULL
    et sur une parcelle, un local ; deux fois sur NULL, deux locaux."""
    assert compte_locaux([_local(None), _local("SYN-P1")]).nb_locaux_maison == 1
    assert compte_locaux([_local(None), _local(None)]).nb_locaux_maison == 2


def test_compte_locaux_vide():
    assert compte_locaux([]) == ComptesLocaux(0, 0, 0, 0, None, None, None)


def test_compte_lignes_maison_repetee_sur_deux_parcelles_reste_une_maison():
    """Forme de la mutation source 2021-748011 (archive DVF 44, run 48) : 4
    lignes, maison de 131 m2 sur deux parcelles, deux lignes de terrain."""
    lignes = [
        _ligne_terrain(numero_disposition="000002", id_parcelle="SYN-P1"),
        _ligne_maison(
            numero_disposition="000002",
            id_parcelle="SYN-P2",
            surface_reelle_bati=131,
            nombre_pieces_principales=5,
            surface_terrain=2721,
        ),
        _ligne_maison(
            numero_disposition="000002",
            id_parcelle="SYN-P3",
            surface_reelle_bati=131,
            nombre_pieces_principales=5,
            surface_terrain=1714,
        ),
        _ligne_terrain(numero_disposition="000002", id_parcelle="SYN-P4"),
    ]

    compteurs = compte_lignes(lignes)
    resultat = qualifie_mutation(compteurs)

    assert compteurs.nb_lignes == 4
    assert compteurs.nb_parcelles == 4
    assert compteurs.nb_locaux_maison == 1
    assert compteurs.surface_bati_totale == 131
    assert compteurs.nombre_pieces_totales == 5
    assert resultat.classe_mutation == "residentiel_maison"
    assert resultat.mono_local is True
    assert resultat.prix_m2 == Decimal("316000") / Decimal(131)


# ---------------------------------------------------------------------------
# Bloc 4 -- variante V de D-02 (decision Ianis du 2026-10-21) : un local repete
# sur les lignes de culture d'une parcelle compte une fois
# ---------------------------------------------------------------------------


def _maison_par_culture(culture: str, surface_terrain: int, **overrides) -> dict:
    """One line of a house repeated on each culture line of its parcel, the
    shape measured on the DVF archive (plan 04.4-03, D-02 alone: 7/15)."""
    champs: dict = {
        "numero_disposition": "000001",
        "id_parcelle": "SYN-P1",
        "code_nature_culture": culture,
        "surface_terrain": surface_terrain,
        "surface_reelle_bati": 96,
        "nombre_pieces_principales": 4,
        "nombre_lots": 0,
    }
    champs.update(overrides)
    return _ligne_maison(**champs)


def test_maison_unique_repetee_sur_les_lignes_s_et_j_reste_une_maison():
    """Variante V : une maison sur une parcelle, repetee sur la ligne S (sol)
    et la ligne J (jardin) avec deux surfaces de terrain, sans lot, est une
    maison isolee avec son prix au m2."""
    lignes = [_maison_par_culture("S", 412), _maison_par_culture("J", 230)]

    compteurs = compte_lignes(lignes)
    resultat = qualifie_mutation(compteurs)

    assert compteurs.nb_lignes == 2
    assert compteurs.nb_parcelles == 1
    assert compteurs.nb_locaux_maison == 1
    assert compteurs.surface_bati_totale == 96
    assert compteurs.nombre_pieces_totales == 4
    assert compteurs.surface_terrain_totale == 642
    assert resultat.classe_mutation == "residentiel_maison"
    assert resultat.mono_local is True
    assert resultat.prix_m2 == Decimal("316000") / Decimal(96)
    assert resultat.prix_m2_raison is None


def test_maison_et_dependances_repetees_par_culture_restent_une_maison():
    """Variante V : la maison et ses deux dependances identiques, repetees sur
    S et J : une maison, deux dependances (deux lignes par culture, maximum
    deux), la surface et les pieces de la maison comptees une fois."""
    lignes = []
    for culture, terrain in (("S", 412), ("J", 230)):
        lignes += [
            _maison_par_culture(culture, terrain),
            _ligne_dependance(
                id_parcelle="SYN-P1",
                code_nature_culture=culture,
                surface_terrain=terrain,
                nombre_pieces_principales=0,
            ),
            _ligne_dependance(
                id_parcelle="SYN-P1",
                code_nature_culture=culture,
                surface_terrain=terrain,
                nombre_pieces_principales=0,
            ),
        ]

    compteurs = compte_lignes(lignes)
    resultat = qualifie_mutation(compteurs)

    assert compteurs.nb_lignes == 6
    assert compteurs.nb_locaux_maison == 1
    assert compteurs.nb_locaux_dependance == 2
    assert compteurs.surface_bati_totale == 96
    assert compteurs.nombre_pieces_totales == 4
    # IN-05: the land of S and J once each, not once per local (1 926).
    assert compteurs.surface_terrain_totale == 642
    assert resultat.classe_mutation == "residentiel_maison"
    assert resultat.prix_m2 == Decimal("316000") / Decimal(96)


def test_deux_lignes_identiques_meme_parcelle_meme_culture_restent_deux_locaux():
    """Ce que D-02 garantit deja et que V garde : deux lots identiques d'un
    programme sur une meme parcelle ET une meme culture sont deux locaux."""
    lignes = [
        _ligne_appartement(
            id_parcelle="SYN-P1",
            code_nature_culture="S",
            surface_reelle_bati=45,
            nombre_pieces_principales=2,
        ),
        _ligne_appartement(
            id_parcelle="SYN-P1",
            code_nature_culture="S",
            surface_reelle_bati=45,
            nombre_pieces_principales=2,
        ),
    ]

    compteurs = compte_lignes(lignes)

    assert compteurs.nb_locaux_appartement == 2
    assert compteurs.surface_bati_totale == 90
    assert compteurs.nombre_pieces_totales == 4
    assert qualifie_mutation(compteurs).classe_mutation == "ensemble_residentiel"


# ---------------------------------------------------------------------------
# Bloc 5 -- IN-05 (revue 04.4) : la surface de terrain d'une ligne de culture
# repetee pour chaque local de la parcelle compte une fois
# ---------------------------------------------------------------------------


def test_terrain_repete_par_local_compte_une_fois():
    """Une maison et deux dependances sur la ligne S de 500 m2 : 500, pas
    1 500."""
    comptes = compte_locaux(
        [
            _local("SYN-P1", culture="S", terrain=500),
            _local("SYN-P1", "3", None, 0, culture="S", terrain=500),
            _local("SYN-P1", "3", None, 0, culture="S", terrain=500),
        ]
    )

    assert comptes.surface_terrain_totale == 500


def test_subdivisions_d_une_meme_culture_s_additionnent():
    """Deux subdivisions T de 300 et 200 m2 sous une maison et sa dependance :
    chaque valeur repetee deux fois, terrain 500."""
    locaux = [
        _local("SYN-P1", code, surface, pieces, culture="T", terrain=terrain)
        for code, surface, pieces in (("1", 131, 5), ("3", None, 0))
        for terrain in (300, 200)
    ]

    assert compte_locaux(locaux).surface_terrain_totale == 500


def test_terrain_de_parcelles_et_cultures_differentes_s_additionne():
    """Paires distinctes : maison sur P1 (S 400, J 100), terrain nu sur P2."""
    comptes = compte_locaux(
        [
            _local("SYN-P1", culture="S", terrain=400),
            _local("SYN-P1", culture="J", terrain=100),
            _local("SYN-P2", None, None, None, culture="T", terrain=2000),
        ]
    )

    assert comptes.nb_locaux_maison == 1
    assert comptes.surface_terrain_totale == 2500


def test_terrain_absent_reste_absent():
    assert compte_locaux([_local("SYN-P1")]).surface_terrain_totale is None


def test_terrain_seul_garde_sa_classe():
    """La classe terrain_seul ne lit que le signe du terrain : inchangee."""
    lignes = [
        _ligne_terrain(id_parcelle="SYN-P1", code_nature_culture="T"),
        _ligne_terrain(id_parcelle="SYN-P2", code_nature_culture="T"),
    ]

    compteurs = compte_lignes(lignes)

    assert compteurs.surface_terrain_totale == 1000
    assert qualifie_mutation(compteurs).classe_mutation == "terrain_seul"
