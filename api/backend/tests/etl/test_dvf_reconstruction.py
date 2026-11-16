"""Preuves sans base de la reconstruction DVF (Phase 57, D-01/D-04/D-05/D-09).

Deux blocs narres, sans jamais ouvrir de connexion `psycopg.connect` reelle :

1. Proprietes du SQL d'agregation -- le gate anti-regression de l'invariant
   bloquant (D-04) : le grain exact, l'absence de filtre sur
   ``code_type_local`` dans le ``WHERE`` (seuls des agregats
   ``FILTER (WHERE ...)`` peuvent le lire), et les bons agregats sur
   ``valeur_fonciere``/``surface_reelle_bati``. Ce bloc est concu pour
   survivre a la phase : toute regression qui reintroduirait un filtre avant
   reconstruction le fait rougir.
2. Proprietes de sequencement -- une seule transaction pour les 4 RENAME
   (T-57-18), la garde 50% qui bloque avant toute bascule (T-57-20), et
   l'ordre d'appel dans ``pipeline.swap()``.

D-31 doctrine (fixtures anonymisees) : tout identifiant synthetique porte le
prefixe ``SYN-`` -- jamais une adresse, un ``id_parcelle`` ou un nom de
commune reels.
"""

from __future__ import annotations

import inspect
import re
from datetime import date
from decimal import Decimal

import pytest

from app.etl_dvf import reconstruction
from app.etl_dvf.pipeline import DvfPipeline
from app.etl_dvf.qualification import compte_lignes

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Block 1: proprietes structurelles de la requete d'agregation
# ---------------------------------------------------------------------------


def _clause_where_apres_from(sql: str) -> str:
    """Le WHERE de *sql*, ignorant tout ce qui precede ``FROM mutations``.

    Les clauses ``FILTER (WHERE ...)`` d'agregat vivent dans la liste
    SELECT, toujours avant ``FROM mutations`` dans cette requete -- couper la
    a cet endroit suffit a les ignorer, sans regex dediee de retrait de
    FILTER. Les commentaires SQL en ligne (``-- ...``) sont retires d'abord
    pour qu'une mention en prose ne puisse jamais faire rougir le gate.
    """
    sans_commentaires = re.sub(r"--[^\n]*", "", sql)
    apres_from = sans_commentaires.split("FROM mutations", 1)[1]
    apres_where = apres_from.split("WHERE", 1)[1]
    return apres_where.split("GROUP BY", 1)[0]


def test_aggregation_groups_only_by_department_and_mutation() -> None:
    """Le grain de reconstruction est exactement (code_departement, id_mutation)."""
    apres_group_by = reconstruction._AGGREGATION_SQL.strip().split("GROUP BY", 1)[1]
    colonnes = {c.strip() for c in apres_group_by.strip().split(",")}
    assert colonnes == {"code_departement", "id_mutation"}


def test_aggregation_has_no_code_type_local_filter_in_where() -> None:
    """Invariant bloquant : le WHERE reel de la requete ne filtre jamais
    code_type_local -- reconstruire avant de filtrer."""
    clause = _clause_where_apres_from(reconstruction._AGGREGATION_SQL)
    assert "code_type_local" not in clause


_SQL_AVEC_VRAI_FILTRE = """
    SELECT count(*) AS n
    FROM mutations
    WHERE code_type_local = '1' AND code_departement = ANY(%(departements)s)
    GROUP BY code_departement, id_mutation
"""

_SQL_AVEC_SEULEMENT_FILTER = """
    SELECT
        count(*) FILTER (WHERE code_type_local = '1') AS nb_locaux_maison
    FROM mutations
    WHERE code_departement = ANY(%(departements)s)
    GROUP BY code_departement, id_mutation
"""

_SQL_AVEC_COMMENTAIRE = """
    SELECT count(*) AS n
    FROM mutations
    -- code_type_local est deliberement absent du WHERE ci-dessous
    WHERE code_departement = ANY(%(departements)s)
    GROUP BY code_departement, id_mutation
"""


def test_where_filter_sensitivity_catches_real_filter() -> None:
    """ROUGE : un vrai filtre WHERE sur code_type_local EST detecte."""
    assert "code_type_local" in _clause_where_apres_from(_SQL_AVEC_VRAI_FILTRE)


def test_where_filter_sensitivity_ignores_aggregate_filter() -> None:
    """VERT : une clause FILTER (WHERE ...) d'agregat n'est jamais confondue
    avec un vrai filtre."""
    assert "code_type_local" not in _clause_where_apres_from(_SQL_AVEC_SEULEMENT_FILTER)


def test_where_filter_sensitivity_ignores_comment_line() -> None:
    """VERT : une mention en commentaire n'est jamais confondue avec un vrai
    filtre."""
    assert "code_type_local" not in _clause_where_apres_from(_SQL_AVEC_COMMENTAIRE)


def test_valeur_fonciere_agregee_par_max_jamais_somme() -> None:
    """DVF-01 : le prix est compte une seule fois (max), jamais somme."""
    sql = reconstruction._AGGREGATION_SQL
    assert "max(valeur_fonciere)" in sql
    assert "sum(valeur_fonciere)" not in sql


def test_compteurs_de_locaux_hors_du_sql() -> None:
    """Phase 04.4 (D-02) : le SQL ne compte plus les locaux ni ne somme leurs
    surfaces et pieces -- il transmet les locaux a ``compte_locaux``, seul
    endroit de la regle de dedoublonnage."""
    sql = reconstruction._AGGREGATION_SQL
    for alias in (
        "nb_locaux_maison",
        "nb_locaux_appartement",
        "nb_locaux_commercial",
        "nb_locaux_dependance",
        "surface_bati_totale",
        "nombre_pieces_totales",
    ):
        assert f"AS {alias}" not in sql, alias
    assert "sum(surface_reelle_bati)" not in sql
    assert "sum(nombre_pieces_principales)" not in sql
    for alias, colonne in (
        ("locaux_disposition", "numero_disposition"),
        ("locaux_type", "code_type_local"),
        ("locaux_surface", "surface_reelle_bati"),
        ("locaux_pieces", "nombre_pieces_principales"),
        ("locaux_parcelle", "id_parcelle"),
        ("locaux_culture", "code_nature_culture"),
        ("locaux_terrain", "surface_terrain"),
    ):
        assert re.search(
            rf"array_agg\({colonne} ORDER BY id\)\s+FILTER \(WHERE[^)]*\)\s+AS {alias}",
            sql,
        ), alias
    # IN-05: the land surface is counted by compte_locaux, never summed.
    assert "sum(surface_terrain)" not in sql
    assert "count(*) AS nb_lignes" in sql


def test_compte_locaux_appele_par_la_reconstruction() -> None:
    source = inspect.getsource(reconstruction._compteurs_depuis_ligne)
    assert "compte_locaux(" in source


def test_suffixe_lu_sur_la_ligne_d_adresse_retenue() -> None:
    """C44 (plan 04.1-14) : le suffixe est celui de la ligne dont le numero et
    la voie sont gardes, jamais un agregat d'autres lignes. Phase 04.4 (D-04) :
    cette ligne est la premiere (``id`` croissant) de la commune retenue
    ``min(code_commune)`` -- les cinq champs d'adresse sont lus sur elle."""
    sql = " ".join(reconstruction._AGGREGATION_SQL.split())
    for colonne in (
        "nom_commune",
        "adresse_numero",
        "adresse_suffixe",
        "adresse_nom_voie",
        "code_postal",
    ):
        assert (
            f"(array_agg({colonne} ORDER BY code_commune, id))[1] AS {colonne}" in sql
        ), colonne
    assert "min(code_commune) AS code_commune" in sql
    assert "ORDER BY id))[1]" not in sql
    assert "adresse_suffixe" in reconstruction._GRAIN_COLUMNS


def test_suffixe_ecrit_dans_sa_colonne() -> None:
    ligne = _ligne_agregee(adresse_suffixe="B")
    compteurs = reconstruction._compteurs_depuis_ligne(ligne)
    resultat = reconstruction.qualifie_mutation(compteurs)

    copie = reconstruction._ligne_copy(ligne, compteurs, resultat)

    assert len(copie) == len(reconstruction._GRAIN_COLUMNS)
    colonnes = dict(zip(reconstruction._GRAIN_COLUMNS, copie, strict=True))
    assert (colonnes["adresse_numero"], colonnes["adresse_suffixe"]) == ("1", "B")


def test_marqueur_du_suffixe_pose_sur_la_candidate_avant_bascule() -> None:
    """La colonne de la candidate porte le marqueur de calcul avant la bascule :
    une base migree mais pas encore reimportee ne le porte pas (manque)."""
    source = inspect.getsource(reconstruction.reconstruit_mutations_grain)
    pose = source.index("_marquer_suffixe")
    assert pose < source.index("_bascule_deux_paires")
    marquage = inspect.getsource(reconstruction._marquer_suffixe)
    assert "COMMENT ON COLUMN mutations_grain_staging.adresse_suffixe" in marquage
    assert "MARQUEUR_ADRESSE_SUFFIXE" in marquage


# ---------------------------------------------------------------------------
# Block 2: appel de la qualification et garde de divergence (sans base)
# ---------------------------------------------------------------------------


class _FakeServerCursor:
    """Faux curseur serveur nomme : lignes pre-chargees, servies par lots."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = list(rows)

    def __enter__(self) -> _FakeServerCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> _FakeServerCursor:
        return self

    def fetchmany(self, size: int) -> list[dict]:
        batch, self._rows = self._rows[:size], self._rows[size:]
        return batch


class _FakeCopy:
    """Faux objet COPY : enregistre les octets ecrits, sans base reelle."""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def __enter__(self) -> _FakeCopy:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def write(self, data: bytes) -> None:
        self.chunks.append(data)


class _FakeWriteCursor:
    """Faux curseur d'ecriture exposant `.copy()`, sans base reelle."""

    def __init__(self) -> None:
        self.copy_obj = _FakeCopy()

    def __enter__(self) -> _FakeWriteCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> _FakeWriteCursor:
        return self

    def copy(self, sql: str) -> _FakeCopy:
        return self.copy_obj


class _FakeSettingCursor:
    """Faux curseur ordinaire, utilise seulement pour `SET statement_timeout`."""

    def __enter__(self) -> _FakeSettingCursor:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: object = None) -> _FakeSettingCursor:
        return self


class _FakeEcritureConnexion:
    """Fausse connexion d'ecriture -- la `conn` de l'appelant. N'ouvre jamais
    de curseur nomme (T-57-2x : la lecture vit sur une connexion separee)."""

    def __init__(self) -> None:
        self.writer: _FakeWriteCursor | None = None

    def cursor(
        self, name: str | None = None, row_factory: object = None
    ) -> _FakeWriteCursor:
        assert name is None, "la connexion d'ecriture n'ouvre jamais de curseur nomme"
        self.writer = _FakeWriteCursor()
        return self.writer


class _FakeLectureConnexion:
    """Fausse connexion de lecture, ouverte via `psycopg.connect(dsn)` A
    L'INTERIEUR de `_reconstruire_mutations` -- distincte de la connexion
    d'ecriture de l'appelant. Un named cursor (FETCH) et un COPY ne peuvent
    pas partager une seule connexion (deadlock trouve empiriquement sur le
    premier run reel, 57-07) : ce fake modelise la separation, pas une seule
    connexion a double role."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.server: _FakeServerCursor | None = None

    def __enter__(self) -> _FakeLectureConnexion:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def cursor(
        self, name: str | None = None, row_factory: object = None
    ) -> _FakeServerCursor | _FakeSettingCursor:
        if name is not None:
            self.server = _FakeServerCursor(self._rows)
            return self.server
        return _FakeSettingCursor()


def _ligne_agregee(**overrides: object) -> dict:
    """Une ligne de resultat SQL synthetique (D-31 : identifiants SYN-*)."""
    champs: dict = {
        "code_departement": "44",
        "id_mutation": "SYN-2024-000001",
        "date_mutation": date(2024, 3, 15),
        "nature_mutation": "Vente",
        "valeur_fonciere": Decimal("250000"),
        "valeur_divergente": False,
        "nb_lignes": 1,
        "nb_dispositions": 1,
        "nb_communes": 1,
        "nb_parcelles": 1,
        "locaux_disposition": ["000001"],
        "locaux_type": ["1"],
        "locaux_surface": [90],
        "locaux_pieces": [4],
        "locaux_parcelle": ["SYN000AA0001"],
        "locaux_culture": ["S"],
        "locaux_terrain": [None],
        "code_commune": "44000",
        "nom_commune": "SYN-COMMUNE",
        "adresse_numero": "1",
        "adresse_suffixe": None,
        "adresse_nom_voie": "SYN-VOIE",
        "code_postal": "44000",
        "position": None,
    }
    champs.update(overrides)
    return champs


def _ligne_agregee_depuis(lignes: list[dict]) -> dict:
    """What ``_AGGREGATION_SQL`` returns for *lignes* (one mutation, id order).

    Mirrors the SQL aggregates the reconstruction keeps and its seven aligned
    ``locaux_*`` arrays, filtered on the lines that carry a type, a surface, a
    number of rooms or a land surface -- the oracle of the equivalence below.
    """

    def distincts(champ: str) -> int:
        return len({ligne[champ] for ligne in lignes if ligne.get(champ)})

    retenues = [
        ligne
        for ligne in lignes
        if ligne.get("code_type_local") is not None
        or ligne.get("surface_reelle_bati") is not None
        or ligne.get("nombre_pieces_principales") is not None
        or ligne.get("surface_terrain") is not None
    ]

    def tableau(champ: str) -> list | None:
        return [ligne.get(champ) for ligne in retenues] or None

    valeurs = {
        ligne["valeur_fonciere"] for ligne in lignes if ligne.get("valeur_fonciere")
    }
    return _ligne_agregee(
        nature_mutation=max(ligne["nature_mutation"] for ligne in lignes),
        valeur_fonciere=max(valeurs) if valeurs else None,
        valeur_divergente=len(valeurs) > 1,
        nb_lignes=len(lignes),
        nb_dispositions=distincts("numero_disposition"),
        nb_communes=distincts("code_commune"),
        nb_parcelles=distincts("id_parcelle"),
        locaux_disposition=tableau("numero_disposition"),
        locaux_type=tableau("code_type_local"),
        locaux_surface=tableau("surface_reelle_bati"),
        locaux_pieces=tableau("nombre_pieces_principales"),
        locaux_parcelle=tableau("id_parcelle"),
        locaux_culture=tableau("code_nature_culture"),
        locaux_terrain=tableau("surface_terrain"),
    )


def _ligne_source(parcelle: str | None, **champs: object) -> dict:
    ligne: dict = {
        "id_mutation": "SYN-2021-000001",
        "numero_disposition": "000002",
        "nature_mutation": "Vente",
        "valeur_fonciere": Decimal("262000"),
        "code_type_local": None,
        "surface_reelle_bati": None,
        "nombre_pieces_principales": None,
        "surface_terrain": None,
        "code_commune": "44999",
        "id_parcelle": parcelle,
    }
    ligne.update(champs)
    return ligne


_MAISON = {
    "code_type_local": "1",
    "surface_reelle_bati": 131,
    "nombre_pieces_principales": 5,
}

# Shapes of the rule's cases (D-31: SYN- parcels). The first one is the shape
# of source mutation 2021-748011 (DVF archive, 44, run 48).
_FORMES: dict[str, list[dict]] = {
    "maison_sur_deux_parcelles": [
        _ligne_source("SYN-P1", surface_terrain=675),
        _ligne_source("SYN-P2", surface_terrain=2721, **_MAISON),
        _ligne_source("SYN-P3", surface_terrain=1714, **_MAISON),
        _ligne_source("SYN-P4", surface_terrain=1009),
    ],
    "lots_identiques_meme_parcelle": [
        _ligne_source("SYN-P1", code_type_local="2", surface_reelle_bati=45),
        _ligne_source("SYN-P1", code_type_local="2", surface_reelle_bati=45),
    ],
    "local_distinct": [
        _ligne_source("SYN-P1", **_MAISON),
        _ligne_source("SYN-P2", code_type_local="1", surface_reelle_bati=60),
    ],
    "dependances_et_parcelle_absente": [
        _ligne_source("SYN-P1", code_type_local="3", nombre_pieces_principales=0),
        _ligne_source("SYN-P2", code_type_local="3", nombre_pieces_principales=0),
        _ligne_source(None, code_type_local="2", surface_reelle_bati=51),
        _ligne_source("SYN-P1", code_type_local="2", surface_reelle_bati=51),
    ],
    "terrain_seul": [_ligne_source("SYN-P1", surface_terrain=500)],
    # Variant V of D-02: a house repeated on the S and J culture lines of its
    # parcel, and two identical lots on one parcel and one culture.
    "maison_repetee_par_culture": [
        _ligne_source(
            "SYN-P1", code_nature_culture="S", surface_terrain=412, **_MAISON
        ),
        _ligne_source(
            "SYN-P1", code_nature_culture="J", surface_terrain=230, **_MAISON
        ),
    ],
    # IN-05: a house and two identical dependances on the S and J lines, the
    # land of each culture repeated on the three locals.
    "maison_et_dependances_par_culture": [
        _ligne_source(
            "SYN-P1", code_nature_culture=culture, surface_terrain=terrain, **local
        )
        for culture, terrain in (("S", 412), ("J", 230))
        for local in (
            _MAISON,
            {"code_type_local": "3", "nombre_pieces_principales": 0},
            {"code_type_local": "3", "nombre_pieces_principales": 0},
        )
    ],
    "lots_identiques_meme_parcelle_meme_culture": [
        _ligne_source(
            "SYN-P1",
            code_nature_culture="S",
            code_type_local="2",
            surface_reelle_bati=45,
        ),
        _ligne_source(
            "SYN-P1",
            code_nature_culture="S",
            code_type_local="2",
            surface_reelle_bati=45,
        ),
    ],
}


@pytest.mark.parametrize("forme", sorted(_FORMES))
def test_tableaux_sql_equivalent_a_compte_lignes(forme: str) -> None:
    """D-02 / D-05 de la phase 57 : la reconstruction (tableaux SQL) et la
    mesure sur archive (``compte_lignes`` sur les lignes) donnent les memes
    compteurs -- la regle de dedoublonnage n'existe qu'a un endroit."""
    lignes = _FORMES[forme]

    compteurs = reconstruction._compteurs_depuis_ligne(_ligne_agregee_depuis(lignes))

    assert compteurs == compte_lignes(lignes)


def test_maison_repetee_par_culture_reconstruite_en_une_maison() -> None:
    """Variante V de D-02 (decision Ianis du 2026-10-21) : le tableau
    ``locaux_culture`` porte la culture de chaque local ; une maison repetee
    sur les lignes S et J de sa parcelle est une maison isolee, prix au m2."""
    lignes = _FORMES["maison_repetee_par_culture"]

    compteurs = reconstruction._compteurs_depuis_ligne(_ligne_agregee_depuis(lignes))
    resultat = reconstruction.qualifie_mutation(compteurs)

    assert (compteurs.nb_locaux_maison, compteurs.surface_bati_totale) == (1, 131)
    assert compteurs.nombre_pieces_totales == 5
    assert resultat.classe_mutation == "residentiel_maison"
    assert resultat.prix_m2 == Decimal("262000") / Decimal(131)


def test_lots_identiques_meme_parcelle_meme_culture_restent_deux() -> None:
    lignes = _FORMES["lots_identiques_meme_parcelle_meme_culture"]

    compteurs = reconstruction._compteurs_depuis_ligne(_ligne_agregee_depuis(lignes))

    assert (compteurs.nb_locaux_appartement, compteurs.surface_bati_totale) == (2, 90)


def test_tableaux_null_signifient_aucun_local() -> None:
    ligne = _ligne_agregee(
        locaux_disposition=None,
        locaux_type=None,
        locaux_surface=None,
        locaux_pieces=None,
        locaux_parcelle=None,
        locaux_culture=None,
        locaux_terrain=None,
    )

    compteurs = reconstruction._compteurs_depuis_ligne(ligne)

    assert (compteurs.nb_locaux_maison, compteurs.surface_bati_totale) == (0, None)
    assert compteurs.surface_terrain_totale is None
    assert (
        reconstruction.qualifie_mutation(compteurs).classe_mutation == "hors_perimetre"
    )


def test_terrain_nu_porte_par_les_tableaux() -> None:
    """IN-05 : une ligne de terrain nu entre dans les tableaux pour sa seule
    surface de terrain ; aucun local, classe terrain_seul."""
    lignes = _FORMES["terrain_seul"]

    compteurs = reconstruction._compteurs_depuis_ligne(_ligne_agregee_depuis(lignes))

    assert (compteurs.nb_locaux_maison, compteurs.surface_terrain_totale) == (0, 500)
    assert reconstruction.qualifie_mutation(compteurs).classe_mutation == "terrain_seul"


def test_terrain_repete_par_local_compte_une_fois() -> None:
    lignes = _FORMES["maison_et_dependances_par_culture"]

    compteurs = reconstruction._compteurs_depuis_ligne(_ligne_agregee_depuis(lignes))

    assert compteurs.surface_terrain_totale == 642
    assert compteurs.nb_locaux_dependance == 2


def test_qualifie_mutation_called_once_per_mutation(monkeypatch) -> None:
    """qualifie_mutation (D-05) est appelee exactement une fois par mutation
    du curseur, jamais plus, jamais moins."""
    lignes = [_ligne_agregee(id_mutation=f"SYN-2024-{i:06d}") for i in range(3)]
    conn = _FakeEcritureConnexion()
    monkeypatch.setattr(
        reconstruction.psycopg,
        "connect",
        lambda dsn: _FakeLectureConnexion(lignes),
    )

    appels: list[reconstruction.CompteursMutation] = []
    original = reconstruction.qualifie_mutation

    def _qualifie_et_compte(compteurs: reconstruction.CompteursMutation):
        appels.append(compteurs)
        return original(compteurs)

    monkeypatch.setattr(reconstruction, "qualifie_mutation", _qualifie_et_compte)

    resultat = reconstruction._reconstruire_mutations(conn, "postgresql://fake", ["44"])

    assert len(appels) == 3
    assert resultat["mutations_reconstruites"] == 3


def test_valeur_divergente_logs_warning_sans_lever(monkeypatch, caplog) -> None:
    """Une mutation a valeur_fonciere divergente produit un log warning et
    JAMAIS une exception (Pattern 1, RESEARCH.md)."""
    lignes = [_ligne_agregee(valeur_divergente=True)]
    conn = _FakeEcritureConnexion()
    monkeypatch.setattr(
        reconstruction.psycopg,
        "connect",
        lambda dsn: _FakeLectureConnexion(lignes),
    )

    with caplog.at_level("WARNING"):
        resultat = reconstruction._reconstruire_mutations(
            conn, "postgresql://fake", ["44"]
        )

    assert resultat["valeur_divergente_count"] == 1
    assert any("divergente" in message for message in caplog.messages)


def test_lecture_et_ecriture_sur_deux_connexions_distinctes(monkeypatch) -> None:
    """Regression (bug trouve sur le premier run reel, 57-07) : le curseur
    nomme (lecture) et le COPY (ecriture) ne doivent JAMAIS partager la
    connexion `conn` de l'appelant -- sinon un FETCH emis pendant un COPY
    ouvert deadlocke (protocole filaire PostgreSQL, aucune erreur explicite,
    0% CPU indefiniment)."""
    lignes = [_ligne_agregee()]
    conn = _FakeEcritureConnexion()
    dsn_recus: list[str] = []

    def _connect_espionne(dsn: str) -> _FakeLectureConnexion:
        dsn_recus.append(dsn)
        return _FakeLectureConnexion(lignes)

    monkeypatch.setattr(reconstruction.psycopg, "connect", _connect_espionne)

    reconstruction._reconstruire_mutations(conn, "postgresql://fake-dsn", ["44"])

    assert dsn_recus == ["postgresql://fake-dsn"], (
        "la lecture doit ouvrir SA PROPRE connexion via psycopg.connect(dsn), "
        "jamais reutiliser `conn`"
    )
    assert conn.writer is not None, "l'ecriture reste bien sur `conn` (COPY)"


# ---------------------------------------------------------------------------
# Block 2 (suite): atomicite de la bascule et garde 50% (sans base)
# ---------------------------------------------------------------------------


class _EventCursor:
    """Enregistre chaque instruction executee -- et RIEN d'autre : pas de
    `.commit()`. Un curseur n'expose reellement jamais `.commit()` (seule une
    connexion en a un) ; modeliser cette absence rend structurellement
    impossible qu'une regression a l'interieur de `_bascule_deux_paires`
    glisse un commit -- elle leverait `AttributeError` immediatement, pas
    seulement un echec d'assertion.
    """

    def __init__(self, events: list[tuple], count_result: tuple = (0,)) -> None:
        self._events = events
        self._count_result = count_result

    def execute(self, sql: str, params: object = None) -> _EventCursor:
        self._events.append(("execute", " ".join(sql.split())))
        return self

    def fetchone(self) -> tuple:
        return self._count_result

    def fetchall(self) -> list[tuple]:
        return []  # logged-candidate guard: every modelled candidate is logged


def test_atomicity_no_commit_between_first_and_last_rename() -> None:
    """Le test d'atomicite (T-57-18) : aucun commit n'est emis entre le
    premier et le dernier RENAME des 4 tables (2 paires)."""
    events: list[tuple] = []
    cur = _EventCursor(events, count_result=(0,))  # production vide -> garde OK

    reconstruction._garde_50_pourcent(cur, staging_count=10)
    reconstruction._bascule_deux_paires(cur, ["44"])
    events.append(("commit",))  # modelise le conn.commit() unique de l'appelant

    renames = [
        i for i, e in enumerate(events) if e[0] == "execute" and "RENAME" in e[1]
    ]
    commits = [i for i, e in enumerate(events) if e[0] == "commit"]

    assert len(renames) >= 4, f"attendu >= 4 RENAME, obtenu {renames}"
    assert not any(renames[0] < c < renames[-1] for c in commits), (
        "un commit a ete emis entre le premier et le dernier RENAME"
    )
    assert commits and commits[0] > renames[-1], (
        "le commit doit venir strictement apres le dernier RENAME"
    )


def test_garde_50_pourcent_blocks_bascule_before_any_rename() -> None:
    """La garde 50% (T-57-20) leve RuntimeError AVANT tout RENAME quand le
    staging porte moins de 50% de la production existante."""
    events: list[tuple] = []
    cur = _EventCursor(events, count_result=(100,))  # production = 100 lignes

    with pytest.raises(RuntimeError, match="50%"):
        reconstruction._garde_50_pourcent(cur, staging_count=10)  # 10 < 50
        reconstruction._bascule_deux_paires(cur, ["44"])  # jamais atteint

    assert not any(e[0] == "execute" and "RENAME" in e[1] for e in events), (
        "un RENAME a ete emis malgre la RuntimeError de la garde 50%"
    )


# ---------------------------------------------------------------------------
# Block 2 (suite): sequencement dans pipeline.swap()
# ---------------------------------------------------------------------------


def test_pipeline_swap_appelle_reconstruction_apres_register_old_table() -> None:
    """La reconstruction est appelee APRES register_old_table -- mutations
    porte deja son nom definitif quand la reconstruction commence."""
    source = inspect.getsource(DvfPipeline.swap)
    register_idx = source.index("register_old_table")
    reconstruct_idx = source.index("reconstruit_mutations_grain(")
    assert register_idx < reconstruct_idx


def test_pipeline_swap_reconstruction_call_not_wrapped_in_try_except() -> None:
    """Aucun try/except n'entoure l'appel : un echec de reconstruction doit
    faire echouer tout le run (D-04).

    Cherche le mot-cle Python ``try:`` (un vrai bloc), pas la sous-chaine
    ``try`` -- qui apparaitrait aussi dans un commentaire expliquant
    l'absence intentionnelle de try/except, faux positif a eviter.
    """
    source = inspect.getsource(DvfPipeline.swap)
    register_idx = source.index("register_old_table")
    reconstruct_idx = source.index("reconstruit_mutations_grain(")
    segment = source[register_idx:reconstruct_idx]
    assert re.search(r"\btry\s*:", segment) is None
