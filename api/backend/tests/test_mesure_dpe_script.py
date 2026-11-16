"""Offline tests for the DPE-house measurement script (Phase 58, T-58-01/T-58-02/
T-57-14).

Everything here runs without a database (and without a network -- bloc 6's
HTTP fetcher is injectable, see ``_neuf_non_interroge``): query execution is
faked through a minimal connection double (``_FausseConnexion``) that
records every SQL string the script emits, on the exact model of
``tests/test_mesure_dvf_script.py``. The matrix proves the three Wave 0
holes named by ``58-VALIDATION.md``, extended by 58-03 Task 3 with two more:

- every SQL statement the script can possibly emit is accepted by the REAL
  ``assert_select_only`` guard imported from ``verify_golden_cases`` (not a
  local copy) -- T-57-14, T-58-01;
- the report (Markdown AND JSON) never republishes a BAN key or a DPE
  number, even when the underlying rows carry one in a column not destined
  for the report -- T-58-02;
- no `score_ban` threshold is hard-coded anywhere outside the
  ``PALIERS_SCORE_BAN``-parameterised rendering loop (D-03 b, D-06);
- every query joining ``adresses`` by ``ban_id`` picks its row
  deterministically (``ban_id`` is not unique -- P-5);
- the D-04 sample (``--echantillon``) is the ONLY artifact carrying
  identifiers -- the main report stays identifier-free even when it runs
  (T-58-10/T-58-11).
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date
from decimal import Decimal
from itertools import permutations
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.unit

from scripts import mesure_dpe_maison as mesure
from scripts.verify_golden_cases import assert_select_only


def _ligne_v2(
    numero: str = "DPE0001",
    *,
    cle: str = "44021_0104_00273",
    dept: str = "44",
    type_batiment: str | None = "maison",
    remplace: str | None = None,
    **autres: Any,
) -> dict:
    return {
        "numero_dpe": numero,
        "identifiant_ban": cle,
        "code_departement": dept,
        "departement_reference": dept,
        "numero_dpe_remplace": remplace,
        "type_batiment": type_batiment,
        **autres,
    }


def _assert_v2_unassigned(statut: dict, *, tronque: bool = False) -> None:
    """Lock the non-attributive V2 projection and its closed measurement shape."""
    assert statut == {
        "statut": "unassigned",
        "raison": "dwelling_identity_unproven",
        "tronque": tronque,
    }


@pytest.mark.parametrize("type_batiment", ["maison", "appartement", "immeuble", None])
def test_v2_terminal_unique_ne_signifie_pas_exact(type_batiment) -> None:
    row = _ligne_v2(type_batiment=type_batiment)
    _assert_v2_unassigned(mesure._statut_simule(row["identifiant_ban"], [row]))


@pytest.mark.parametrize("autre_type", ["appartement", "immeuble"])
def test_v2_tous_types_participent_a_la_meme_cle(autre_type) -> None:
    lignes = [_ligne_v2("a"), _ligne_v2("b", type_batiment=autre_type)]
    for ordre in permutations(lignes):
        _assert_v2_unassigned(
            mesure._statut_simule(lignes[0]["identifiant_ban"], ordre)
        )


def test_v2_chaine_maison_appartement_et_permutation() -> None:
    lignes = [_ligne_v2("a"), _ligne_v2("b", type_batiment="appartement", remplace="a")]
    for ordre in permutations(lignes):
        statut = mesure._statut_simule(lignes[0]["identifiant_ban"], ordre)
        _assert_v2_unassigned(statut)
        graphe = mesure._auditer_remplacements(ordre, ["44", "85"], 10)
        assert graphe["mesures"]["44"]["liens_changement_type"] == 1
        assert graphe["mesures"]["44"]["predecesseurs_avec_successeur"] == 1


def test_v2_remplacement_inter_ban_departement_reste_separe_du_classifier() -> None:
    ancien = _ligne_v2("a")
    nouveau = _ligne_v2("b", cle="85001_0100_00001", dept="85", remplace="a")
    # Current API never loads the foreign BAN successor: keep that baseline.
    _assert_v2_unassigned(mesure._statut_simule(ancien["identifiant_ban"], [ancien]))
    for ordre in permutations([ancien, nouveau]):
        compte = mesure._auditer_remplacements(ordre, ["44", "85"], 10)["mesures"]
        assert compte["85"]["liens_changement_ban"] == 1
        assert compte["85"]["liens_changement_departement"] == 1
        assert compte["44"]["predecesseurs_avec_successeur"] == 1
        assert compte["44"]["liens_declares"] == 0


def test_v2_references_absentes_cycles_autoliens_et_fourches() -> None:
    lignes = [
        _ligne_v2("a", remplace="absent"),
        _ligne_v2("b", remplace="a"),
        _ligne_v2("c", remplace="a"),
        _ligne_v2("d", remplace="e"),
        _ligne_v2("e", remplace="d"),
        _ligne_v2("f", remplace="f"),
    ]
    for ordre in (lignes, list(reversed(lignes))):
        compte = mesure._auditer_remplacements(ordre, ["44"], 10)["mesures"]["tous"]
        assert compte["references_absentes_base"] == 1
        assert compte["noeuds_chaine_reference_absente"] == 3
        assert compte["noeuds_dans_cycle"] == 3
        assert compte["cycles_touchant_perimetre"] == 2
        assert compte["auto_liens"] == 1
        assert compte["predecesseurs_plusieurs_successeurs"] == 1
    # Graph shape never promotes an address-level document to a dwelling.
    _assert_v2_unassigned(
        mesure._statut_simule(lignes[0]["identifiant_ban"], lignes[:1])
    )
    _assert_v2_unassigned(
        mesure._statut_simule(lignes[0]["identifiant_ban"], lignes[3:5])
    )


def test_v2_graphe_tronque_ou_numero_ambigu_est_inconnu() -> None:
    lignes = [_ligne_v2("a", remplace="b"), _ligne_v2("b")]
    tronque = mesure._auditer_remplacements(lignes, ["44"], 1)
    assert tronque["statut"] == "inconnu_borne_lecture"
    assert tronque["mesures"] is None
    ambigu = mesure._auditer_remplacements([lignes[0], lignes[0]], ["44"], 10)
    assert ambigu["statut"] == "inconnu_numeros_non_uniques"
    assert ambigu["mesures"] is None


def test_v2_fourche_vers_reference_absente_ferme_la_politique() -> None:
    lignes = [_ligne_v2("a", remplace="absent"), _ligne_v2("b", remplace="absent")]
    statut = mesure._statut_simule(lignes[0]["identifiant_ban"], lignes)
    _assert_v2_unassigned(statut)
    compte = mesure._auditer_remplacements(lignes, ["44"], 10)["mesures"]["tous"]
    assert compte["references_absentes_base"] == 2
    assert compte["references_absentes_avec_plusieurs_successeurs"] == 1


def test_v2_candidats_distinguables_ne_sont_pas_promus_en_multiple() -> None:
    lignes = [
        _ligne_v2("a", complement_adresse_logement="porte 1"),
        _ligne_v2(
            "b", type_batiment="appartement", complement_adresse_logement="porte 2"
        ),
    ]
    for ordre in permutations(lignes):
        _assert_v2_unassigned(
            mesure._statut_simule(lignes[0]["identifiant_ban"], ordre)
        )


def test_v2_limite_sentinelle_et_tri_reel() -> None:
    lignes = [
        _ligne_v2(f"{i:04d}", remplace=f"{i - 1:04d}" if i else None)
        for i in range(121)
    ]
    cle = lignes[0]["identifiant_ban"]
    _assert_v2_unassigned(mesure._statut_simule(cle, lignes[:120]))
    for ordre in (lignes, list(reversed(lignes))):
        statut = mesure._statut_simule(cle, ordre)
        _assert_v2_unassigned(statut, tronque=True)


def test_v2_ne_cree_pas_de_nouvelles_gates() -> None:
    row = _ligne_v2(
        score_ban=0.01,
        adresse_brut="autre adresse",
        date_fin_validite_dpe=date(2000, 1, 1),
    )
    statut = mesure._statut_simule(row["identifiant_ban"], [row])
    _assert_v2_unassigned(statut)
    assert "perime" not in statut
    sans_validite = mesure._statut_simule(row["identifiant_ban"], [_ligne_v2()])
    _assert_v2_unassigned(sans_validite)
    assert "validite_absente" not in sans_validite


def test_v2_donnees_documentaires_invalides_ne_creent_pas_attribution() -> None:
    row = _ligne_v2(date_fin_validite_dpe="date invalide")
    statut = mesure._statut_simule(row["identifiant_ban"], [row])
    _assert_v2_unassigned(statut)


def test_v2_contrat_sql_de_lecture_et_deduplication_ban() -> None:
    from app.dpe.queries import _ATTRIBUTION_COLS, dpe_par_identifiant_ban

    sql = _normalise(mesure.CANDIDATS_EXACT_CORRIGE_SQL)
    assert "SELECT DISTINCT a.code_departement, a.ban_id" in sql
    assert "d.identifiant_ban = a.ban_id" in sql
    assert "d.code_departement = CASE WHEN a.ban_id LIKE '97%%'" in sql
    assert "ORDER BY d.numero_dpe LIMIT %(limite_exact_sentinelle)s" in sql
    assert "type_batiment =" not in sql
    assert all(f"d.{champ}" in sql for champ in _ATTRIBUTION_COLS)
    assert (
        inspect.signature(dpe_par_identifiant_ban).parameters["limit"].default
        == mesure.LIMITE_EXACT
    )
    graphe = mesure.GRAPHE_REMPLACEMENTS_SQL
    assert "ANY(%(departements)s)" not in graphe
    assert "type_batiment =" not in graphe


def _population_v2_empoisonnee() -> dict:
    return {
        "code_departement": "44",
        "diagnostics": {
            "lignes_dpe": 3,
            "diagnostics_distincts": 3,
            "cles_dpe_distinctes": 1,
            "numero_dpe": "DPE-PRIVE",
        },
        "adresses": {
            "lignes_adresses_avec_cle": 3,
            "cles_adresses_distinctes": 2,
            "lignes_adresses_doublons_cle": 1,
            "cles_adresses_avec_candidats_api": 1,
            "ban_id": "44021_0104_00273",
        },
        "lignes_adresses_sans_cle": 1,
        "adresse_brut": "ADRESSE PRIVEE",
    }


def test_v2_rapport_agrege_denominateurs_gardes_et_absence_identifiants() -> None:
    reponses = {
        _normalise(mesure.POPULATIONS_CORRIGEES_SQL): [_population_v2_empoisonnee()],
        _normalise(mesure.CANDIDATS_EXACT_CORRIGE_SQL): [_ligne_v2("DPE-PRIVE")],
        _normalise(mesure.GRAPHE_REMPLACEMENTS_SQL): [_ligne_v2("DPE-PRIVE")],
    }
    conn = _FausseConnexion(reponses)
    donnees = mesure._construire_rapport_corrige(conn, ["44"], "test", date(2026, 9, 3))
    for sql in conn.requetes_emises:
        assert_select_only(sql, mesure.ALLOWED_SQL)
    assert mesure.COLLISIONS_TERMINAUX_SQL not in conn.requetes_emises
    assert mesure.GATE_EVALUABILITE_SQL not in conn.requetes_emises
    assert mesure.PERIMETRE_NEUF_SQL not in conn.requetes_emises
    assert donnees["meta"]["schema"] == "58-22-v2"
    simulation = donnees["simulation_exact"][0]
    assert simulation["denominateur_cles_reference"] == 2
    assert simulation["unassigned"] == 1
    assert simulation["raison_dwelling_identity_unproven"] == 1
    assert simulation["exact"] == 0
    assert simulation["multiple"] == 0
    assert simulation["building"] == 0
    assert simulation["inevaluable_classificateur"] == 0
    assert simulation["cles_sans_ligne_ban_exacte"] == 1
    assert simulation["absent_sans_contexte_batiment"] == 1
    assert "absent" not in simulation
    assert donnees["populations"][0]["adresses"]["lignes_adresses_avec_cle"] == 3
    assert donnees["observations_indisponibles"]["fiches_exactes_live"] is None
    for contenu in (json.dumps(donnees), mesure._rendre_markdown_corrige(donnees)):
        for prive in ("DPE-PRIVE", "44021_0104_00273", "ADRESSE PRIVEE"):
            assert prive not in contenu


def test_v2_ecart_de_population_ne_produit_pas_un_faux_taux() -> None:
    populations = mesure._populations_corrigees([_population_v2_empoisonnee()])
    with pytest.raises(mesure.GoldenSecurityError):
        mesure._resultats_simulation(populations, {})


def test_v2_corroboration_ne_choisit_pas_une_parcelle_arbitraire() -> None:
    sql = mesure.CORROBORATION_DVF_SQL
    assert "SELECT DISTINCT a.code_departement, a.ban_id, p.id_parcelle" in sql
    assert "LIMIT 1" not in sql
    assert "date_mutation_min" in sql and "date_mutation_max" in sql
    assert "cles_observations_contradictoires" in sql


def test_v2_cli_explicite_sans_modifier_mode_historique() -> None:
    assert mesure._parse_args([]).couverture_corrigee is False
    args = mesure._parse_args(["--couverture-corrigee", "--departements", "44,85"])
    assert args.departements == ["44", "85"]
    with pytest.raises(SystemExit):
        mesure._parse_args(["--couverture-corrigee", "--echantillon", "interdit"])
    with pytest.raises(SystemExit):
        mesure._parse_args(["--max-lignes-graphe", "0"])


# ---------------------------------------------------------------------------
# Fake connection: records emitted SQL, returns configured rows by statement.
# Duplicated from ``test_mesure_dvf_script.py`` (57-PATTERNS) -- no network,
# no DB, no local re-implementation of the guard itself.
# ---------------------------------------------------------------------------


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


class _FausseCurseur:
    def __init__(self, lignes: list[dict[str, Any]]) -> None:
        self._lignes = lignes

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._lignes)


class _FausseConnexion:
    """Duck-typed stand-in for ``psycopg.Connection`` -- no network, no DB.

    Records every statement passed to ``.execute`` so the guard test can feed
    them, one by one, to the real ``assert_select_only``.
    """

    def __init__(self, reponses: dict[str, list[dict[str, Any]]] | None = None) -> None:
        self.requetes_emises: list[str] = []
        self._reponses = reponses or {}

    def execute(self, sql: str, params: Any = None) -> _FausseCurseur:
        self.requetes_emises.append(sql)
        return _FausseCurseur(self._reponses.get(_normalise(sql), []))

    def cursor(self, name: str) -> _FauxCurseurServeur:
        return _FauxCurseurServeur(self)


class _FauxCurseurServeur:
    def __init__(self, conn: _FausseConnexion) -> None:
        self.conn = conn
        self.lignes: list[dict] = []

    def __enter__(self) -> _FauxCurseurServeur:
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def execute(self, sql: str, params: Any) -> None:
        self.lignes = self.conn.execute(sql, params).fetchall()

    def fetchmany(self, taille: int) -> list[dict]:
        resultat, self.lignes = self.lignes[:taille], self.lignes[taille:]
        return resultat


# ---------------------------------------------------------------------------
# 1. Read-only guard (T-57-14, T-58-01): every emitted statement must clear
#    the REAL gate, never a local copy.
# ---------------------------------------------------------------------------


def test_toutes_les_requetes_sont_acceptees_par_la_garde_reelle() -> None:
    conn = _FausseConnexion()
    mesure._construire_rapport(conn, ["44"], "test", date(2026, 1, 1))
    assert conn.requetes_emises, "le script doit emettre au moins une requete"
    for sql in conn.requetes_emises:
        assert_select_only(sql, mesure.ALLOWED_SQL)


# ---------------------------------------------------------------------------
# 2. Write guard (T-58-01): the only door to the database is
#    ``_open_read_only_connection``, and no allowlisted statement is a
#    mutation.
# ---------------------------------------------------------------------------

_VERBES_ECRITURE: tuple[str, ...] = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "COPY",
    "GRANT",
)


def test_ecriture_impossible() -> None:
    source_main = inspect.getsource(mesure.main)
    assert "_open_read_only_connection(" in source_main
    assert "psycopg.connect(" not in source_main

    for sql in mesure.ALLOWED_SQL:
        normalise = _normalise(sql).strip().upper()
        assert not normalise.startswith(_VERBES_ECRITURE), sql
        for verbe in _VERBES_ECRITURE:
            assert f" {verbe} " not in f" {normalise} ", (verbe, sql)


# ---------------------------------------------------------------------------
# 3. Confidentiality (T-58-02): the report never republishes a BAN key or a
#    DPE number, even when the underlying rows carry one in a column not
#    destined for the report -- proves the whitelist projection, not a
#    reformulation of it.
# ---------------------------------------------------------------------------

POISON_BAN = "44021_0104_00273"
POISON_DPE = "2644E0039039K"


def _reponses_empoisonnees() -> dict[str, list[dict[str, Any]]]:
    return {
        _normalise(mesure.TETE_ALEMBIC_SQL): [{"version_num": "090_dpe_adresse_brute"}],
        _normalise(mesure.VOLUMES_PAR_FORME_CLE_SQL): [
            {
                "code_departement": "44",
                "segments": 3,
                "effectif": 10,
                "joignant_adresses": 9,
                # Colonne volontairement NON destinee au rapport (T-58-02) :
                # prouve que la projection en liste blanche l'ecarte.
                "identifiant_ban": POISON_BAN,
            }
        ],
        _normalise(mesure.DISTRIBUTION_SCORE_BAN_SQL): [
            {
                "code_departement": "44",
                "type_batiment": "maison",
                "segments": 3,
                "palier": 0.8,
                "effectif": 10,
                "score_min": Decimal("0.10"),
                "score_max": Decimal("0.90"),
                "score_moyenne": Decimal("0.50"),
                "decile_10": Decimal("0.20"),
                "decile_20": Decimal("0.30"),
                "decile_30": Decimal("0.40"),
                "decile_40": Decimal("0.45"),
                "decile_50": Decimal("0.50"),
                "decile_60": Decimal("0.60"),
                "decile_70": Decimal("0.70"),
                "decile_80": Decimal("0.80"),
                "decile_90": Decimal("0.90"),
                "score_ban_null": 0,
                "n_au_dessus_du_palier": 8,
                "numero_dpe": POISON_DPE,
                "identifiant_ban": POISON_BAN,
            }
        ],
        _normalise(mesure.COLLISIONS_TERMINAUX_SQL): [
            {
                "code_departement": "44",
                "classe": "1",
                "nb_cles": 5,
                "nb_lignes": 5,
                "identifiant_ban": POISON_BAN,
                "numero_dpe": POISON_DPE,
            }
        ],
    }


def test_rapport_ne_publie_aucun_identifiant() -> None:
    conn = _FausseConnexion(_reponses_empoisonnees())
    donnees = mesure._construire_rapport(conn, ["44"], "test", date(2026, 1, 1))

    markdown = mesure._rendre_markdown(donnees)
    contenu_json = json.dumps(donnees, cls=mesure._EncodeurDecimal, ensure_ascii=False)

    for poison in (POISON_BAN, POISON_DPE):
        assert poison not in markdown
        assert poison not in contenu_json

    # La projection en liste blanche doit avoir retire les cles empoisonnees
    # des dicts eux-memes, pas seulement de leur rendu texte.
    for row in donnees["bloc1"] + donnees["bloc3"] + donnees["bloc4"]:
        assert "identifiant_ban" not in row
        assert "numero_dpe" not in row


# ---------------------------------------------------------------------------
# 3bis. T-58-20 (58-05 threat register): bloc 2 compares adresse_brut/
#    nom_rue_ban entirely in SQL -- the report only ever receives a verdict
#    + a count, never a text fragment. Proven the same way as test 3 above
#    (poisoned mock rows carrying a real address fragment / BAN key / DPE
#    number in a column NOT destined for the report, whitelist projection
#    must drop it): a raw substring search on the SQL text itself would
#    false-positive on the normalisation CTE's own column ALIASES
#    (``adresse_brut_normalisee`` legitimately contains the substring
#    ``adresse_brut``), so the report OUTPUT is the correct thing to prove
#    here, not the SQL text.
# ---------------------------------------------------------------------------

POISON_ADRESSE_FRAGMENT = "9 Rue du Poison"
POISON_VOIE_CORROMPUE = "Rue Beno*u00eet Poison"


def _reponses_bloc2_empoisonnees() -> dict[str, list[dict[str, Any]]]:
    reponses = _reponses_empoisonnees()
    reponses[_normalise(mesure.CONCORDANCE_NUMERO_SQL)] = [
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "segments": 3,
            "verdict": "ecart",
            "effectif": 4,
            # Colonne volontairement NON destinee au rapport (T-58-02/T-58-20).
            "adresse_brut": POISON_ADRESSE_FRAGMENT,
        }
    ]
    reponses[_normalise(mesure.CONCORDANCE_VOIE_SQL)] = [
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "segments": 3,
            "verdict": "absente",
            "effectif": 3,
            "effectif_corrompu": 1,
            "nom_rue_ban": POISON_VOIE_CORROMPUE,
        }
    ]
    reponses[_normalise(mesure.CONCORDANCE_STATUT_GEOCODAGE_SQL)] = [
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "statut_geocodage": "adresse geocodee ban a l'adresse",
            "verdict_numero": "ecart",
            "verdict_voie": "absente",
            "effectif": 2,
            "score_ban_median": Decimal("0.55"),
            "identifiant_ban": POISON_BAN,
        }
    ]
    reponses[_normalise(mesure.CONCORDANCE_SCORE_BAN_PALIER_SQL)] = [
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "gate_globale": "concordant",
            "palier": 0.8,
            "effectif": 5,
            "n_au_dessus_du_palier": 3,
            "numero_dpe": POISON_DPE,
        }
    ]
    return reponses


def test_bloc2_ne_publie_aucun_fragment_adresse() -> None:
    conn = _FausseConnexion(_reponses_bloc2_empoisonnees())
    donnees = mesure._construire_rapport(conn, ["44"], "test", date(2026, 1, 1))

    markdown = mesure._rendre_markdown(donnees)
    contenu_json = json.dumps(donnees, cls=mesure._EncodeurDecimal, ensure_ascii=False)

    for poison in (
        POISON_ADRESSE_FRAGMENT,
        POISON_VOIE_CORROMPUE,
        POISON_BAN,
        POISON_DPE,
    ):
        assert poison not in markdown
        assert poison not in contenu_json

    bloc2 = donnees["bloc2"]
    for row in (
        bloc2["numero"]
        + bloc2["voie"]
        + bloc2["statut_geocodage"]
        + bloc2["score_ban_palier"]
    ):
        for cle_interdite in (
            "adresse_brut",
            "nom_rue_ban",
            "identifiant_ban",
            "numero_dpe",
        ):
            assert cle_interdite not in row


# ---------------------------------------------------------------------------
# 4. No hard-coded threshold (D-03 b, D-06): PALIERS_SCORE_BAN only feeds
#    bind parameters and the rendering loop, never a literal comparison.
# ---------------------------------------------------------------------------

_COMPARAISON_LITTERALE = re.compile(r"(?:>=|<=|>|<)\s*0\.\d+|0\.\d+\s*(?:>=|<=|>|<)")


def test_aucun_seuil_code_en_dur() -> None:
    source = inspect.getsource(mesure)
    lignes_utiles = "\n".join(
        ligne for ligne in source.splitlines() if not ligne.strip().startswith("#")
    )
    comparaisons = _COMPARAISON_LITTERALE.findall(lignes_utiles)
    assert not comparaisons, comparaisons


def test_le_test_de_seuil_ignore_les_commentaires() -> None:
    """Proves test 4's own claim: a comment containing a literal threshold
    comparison must NOT fail the gate above (58-PLAN acceptance criteria)."""
    source_avec_commentaire = (
        inspect.getsource(mesure) + "\n# une note qui dit score_ban >= 0.80\n"
    )
    lignes_utiles = "\n".join(
        ligne
        for ligne in source_avec_commentaire.splitlines()
        if not ligne.strip().startswith("#")
    )
    assert not _COMPARAISON_LITTERALE.findall(lignes_utiles)


# ---------------------------------------------------------------------------
# 5. D-11/P-5 (58-03 Task 3): every ALLOWED_SQL statement that JOINS
#    `adresses` keyed by `ban_id` (fan-out risk: `ban_id` is NOT unique,
#    192/97 duplicates 44/85) picks its candidate row deterministically.
#    Blocs 5/7/8's canonical-parcel join all alias `adresses` as `a` and pick
#    via `ROW_NUMBER() OVER (PARTITION BY <dpe_key>.numero_dpe ORDER BY a.id)
#    ... WHERE rn = 1` -- `ORDER BY a.id` is therefore a precise marker tied
#    to THIS specific pick (unlike a bare `ROW_NUMBER() OVER`/`LIMIT 1`
#    substring search, which would also match the UNRELATED parcelle-lateral
#    ROW_NUMBER/LIMIT 1 these same queries carry, producing a false negative
#    if the adresses pick were the one broken). Chosen over `DISTINCT ON`
#    for performance: measured >120s with a prior `DISTINCT ON` over the
#    full ~1,02M-row `adresses` table locally, vs <10s with join-first +
#    ROW_NUMBER().
#    A bare `FROM adresses` with no JOIN carries no fan-out risk (an EXISTS
#    check collapses to a boolean regardless of match count -- bloc 1; a
#    `SELECT DISTINCT` projection into a value-SET is unaffected by upstream
#    row duplicates -- D-11's `RESOLVE_LIEU_DIT_AVEC_NUMEROTEE_SQL`), so
#    those are excluded from this guard on purpose.
# ---------------------------------------------------------------------------

_MARQUEURS_DETERMINISME: tuple[str, ...] = ("DISTINCT ON", "ORDER BY a.id")


_JOIN_ADRESSES_TABLE = re.compile(r"\bJOIN adresses\b")


def _requetes_joignant_adresses_par_ban_id() -> list[str]:
    # `\b` after "adresses" excludes CTE aliases like "adresses_prefixe"
    # (D-11's RESOLVE_LIEU_DIT_AVEC_NUMEROTEE_SQL joins that CTE, not the
    # table itself) -- a bare substring match would false-positive on it.
    return [sql for sql in mesure.ALLOWED_SQL if _JOIN_ADRESSES_TABLE.search(sql)]


def test_lecture_ban_est_deterministe() -> None:
    requetes = _requetes_joignant_adresses_par_ban_id()
    assert requetes, "au moins une requete doit joindre adresses par ban_id (P-5)"
    for sql in requetes:
        assert any(marqueur in sql for marqueur in _MARQUEURS_DETERMINISME), sql


def test_lecture_ban_est_deterministe_detecte_une_regression() -> None:
    """Mutation-test the guard above: stripping the determinism marker from a
    real query that JOINS adresses by ban_id must fail the assertion
    (58-PLAN acceptance criteria)."""
    assert "JOIN adresses" in mesure.GATE_EVALUABILITE_SQL
    assert "ORDER BY a.id" in mesure.GATE_EVALUABILITE_SQL
    mutee = mesure.GATE_EVALUABILITE_SQL.replace("ORDER BY a.id", "")
    assert not any(marqueur in mutee for marqueur in _MARQUEURS_DETERMINISME)


# ---------------------------------------------------------------------------
# 6. T-58-10/T-58-11 (58-03 Task 3): the D-04 sample is the ONLY artifact
#    carrying identifiers -- the main report (Markdown + JSON) stays
#    identifier-free even when `--echantillon` is used.
# ---------------------------------------------------------------------------

POISON_ECHANTILLON_BAN = "44099_9999_00099"
POISON_ECHANTILLON_DPE = "9999E9999999X"


def _reponses_echantillon() -> dict[str, list[dict[str, Any]]]:
    ligne_poison = {
        "code_departement": "44",
        "identifiant_ban": POISON_ECHANTILLON_BAN,
        "numero_dpe": POISON_ECHANTILLON_DPE,
        "score_ban": Decimal("0.10"),
        "adresse_brut": "9 RUE POISON",
        "adresse_ban": "9 Rue Poison 44000 Nantes",
        "numero_voie_ban": "9",
        "nom_rue_ban": "Rue Poison",
        "statut_geocodage": "adresse géocodée ban à l'adresse",
        "forme_cle": 3,
        "nombre_dpe_terminaux": 1,
        "numero_dc": "10",
        "rep_dc": "bis",
        "nom_voie_dc": "Rue Antidote",
        "code_postal_dc": "44000",
        "nom_commune_dc": "Nantes",
        "date_etablissement_dpe": date(2026, 1, 1),
        "etiquette_dpe": "G",
    }
    reponses = _reponses_empoisonnees()
    for sql in (
        mesure.ECHANTILLON_SCORE_BAN_BAS_SQL,
        mesure.ECHANTILLON_COLLISION_SQL,
        mesure.ECHANTILLON_LIEU_DIT_SQL,
    ):
        reponses[_normalise(sql)] = [ligne_poison]
    return reponses


def test_echantillon_est_le_seul_porteur_d_identifiants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reponses = _reponses_echantillon()
    conn_rapport = _FausseConnexion(reponses)
    donnees = mesure._construire_rapport(conn_rapport, ["44"], "test", date(2026, 1, 1))
    markdown = mesure._rendre_markdown(donnees)
    contenu_json = json.dumps(donnees, cls=mesure._EncodeurDecimal, ensure_ascii=False)

    for poison in (POISON_ECHANTILLON_BAN, POISON_ECHANTILLON_DPE):
        assert poison not in markdown
        assert poison not in contenu_json

    monkeypatch.setattr(mesure, "_RACINE_DEPOT", tmp_path)
    (tmp_path / ".gitignore").write_text(
        mesure._MOTIF_GITIGNORE_ECHANTILLON + "\n", encoding="utf-8"
    )
    chemin_echantillon = tmp_path / ".planning" / "58-ECHANTILLON-DPE-MAISON.md"
    chemin_echantillon.parent.mkdir()
    conn_echantillon = _FausseConnexion(reponses)
    mesure._tirer_echantillon(conn_echantillon, ["44"], str(chemin_echantillon))
    contenu_echantillon = chemin_echantillon.read_text(encoding="utf-8")

    for poison in (POISON_ECHANTILLON_BAN, POISON_ECHANTILLON_DPE):
        assert poison in contenu_echantillon

    for sql in conn_echantillon.requetes_emises:
        assert_select_only(sql, mesure.ALLOWED_SQL)


def test_echantillon_separe_fiche_unique_et_diagnostics() -> None:
    ligne_a = {
        "code_departement": "44",
        "identifiant_ban": POISON_ECHANTILLON_BAN,
        "numero_dpe": "1111E1111111A",
        "score_ban": Decimal("0.20"),
        "adresse_brut": "9 RUE POISON",
        "adresse_ban": "9 Rue Poison 44000 Nantes",
        "numero_voie_ban": "9",
        "nom_rue_ban": "Rue Poison",
        "statut_geocodage": "adresse géocodée ban à l'adresse",
        "forme_cle": 3,
        "nombre_dpe_terminaux": 2,
        "numero_dc": "10",
        "rep_dc": "bis",
        "nom_voie_dc": "Rue Antidote",
        "code_postal_dc": "44000",
        "nom_commune_dc": "Nantes",
        "date_etablissement_dpe": date(2025, 1, 1),
        "etiquette_dpe": "F",
    }
    ligne_b = {**ligne_a, "numero_dpe": "2222E2222222B"}

    cas, fiches = mesure._preparer_echantillon(
        [
            ("score_ban_bas", [ligne_a]),
            ("collision_cle", [ligne_a, ligne_b]),
        ]
    )

    assert len(fiches) == 1
    assert fiches[0]["fiche_ref"] == "F-01"
    assert fiches[0]["etat_fiche"] == ""
    assert fiches[0]["observe_le"] == ""
    assert fiches[0]["adresse_datacommune"] == "10bis Rue Antidote, 44000 Nantes"
    assert fiches[0]["url_fiche"] == (
        "https://datacommune.fr/bien/10bis-rue-antidote-44000-nantes?type_bien=maison"
    )
    assert len(cas) == 2
    assert cas[0]["signaux"] == ["score_ban_bas", "collision_cle"]
    assert {ligne["fiche_ref"] for ligne in cas} == {"F-01"}
    assert all(ligne["verdict_documentaire"] == "" for ligne in cas)
    for champ in mesure._CHAMPS_ECHANTILLON:
        assert champ in cas[0]


def test_echantillon_documente_les_etats_sans_prejuger_du_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mesure, "_RACINE_DEPOT", tmp_path)
    (tmp_path / ".gitignore").write_text(
        mesure._MOTIF_GITIGNORE_ECHANTILLON + "\n", encoding="utf-8"
    )
    chemin = tmp_path / ".planning" / "58-ECHANTILLON-DPE-MAISON.md"
    chemin.parent.mkdir()
    conn = _FausseConnexion(_reponses_echantillon())

    mesure._tirer_echantillon(conn, ["44"], str(chemin))

    contenu = chemin.read_text(encoding="utf-8")
    for etat in (
        "exact",
        "multiple",
        "building",
        "absent",
        "temporary",
        "sans_fiche",
    ):
        assert f"`{etat}`" in contenu
    assert "`temporary` est une panne transitoire" in contenu
    assert "L'etat de la Fiche ne remplit et ne suggere jamais ce verdict" in contenu
    assert "| fiche_ref |" in contenu
    assert "| signaux | fiche_ref |" in contenu
    assert "adresse_datacommune" in contenu
    assert "10bis Rue Antidote, 44000 Nantes" in contenu


def test_echantillon_refuse_un_chemin_versionnable(tmp_path: Path) -> None:
    chemin = tmp_path / "58-ECHANTILLON-DPE-MAISON.md"

    with pytest.raises(mesure.GoldenSecurityError, match=r"sous \.planning"):
        mesure._tirer_echantillon(_FausseConnexion(), ["44"], str(chemin))


def test_echantillon_refuse_si_le_motif_gitignore_disparait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mesure, "_RACINE_DEPOT", tmp_path)
    (tmp_path / ".planning").mkdir()
    (tmp_path / ".gitignore").write_text("*.tmp\n", encoding="utf-8")
    chemin = tmp_path / ".planning" / "58-ECHANTILLON-DPE-MAISON.md"

    with pytest.raises(mesure.GoldenSecurityError, match="motif de confidentialite"):
        mesure._tirer_echantillon(_FausseConnexion(), ["44"], str(chemin))


def test_lieu_dit_sans_adresse_dc_est_marque_sans_fiche() -> None:
    ligne = {
        **_reponses_echantillon()[_normalise(mesure.ECHANTILLON_LIEU_DIT_SQL)][0],
        "identifiant_ban": "44099_9999",
        "forme_cle": 2,
        "numero_dc": None,
        "rep_dc": None,
        "nom_voie_dc": None,
        "code_postal_dc": None,
        "nom_commune_dc": None,
    }

    _, fiches = mesure._preparer_echantillon([("lieu_dit", [ligne])])

    assert fiches[0]["adresse_datacommune"] == "ABSENT"
    assert fiches[0]["url_fiche"] == ""
    assert fiches[0]["etat_fiche"] == "sans_fiche"


def test_requetes_echantillon_lisent_adresse_dc_deterministement() -> None:
    for sql in (
        mesure.ECHANTILLON_SCORE_BAN_BAS_SQL,
        mesure.ECHANTILLON_COLLISION_SQL,
        mesure.ECHANTILLON_LIEU_DIT_SQL,
    ):
        assert "FROM adresses a" in sql
        assert "ORDER BY a.id" in sql
        assert "nombre_dpe_terminaux" in sql


def test_table_markdown_ne_permet_pas_a_une_adresse_de_casser_une_ligne() -> None:
    contenu = mesure._table_markdown(["adresse"], [["12 rue\r\nEtage | <porte>"]])

    assert "12 rue<br>Etage \\| &lt;porte&gt;" in contenu
    assert contenu.count("\n") == 2


@pytest.mark.parametrize(
    ("adresse", "slug"),
    [
        ("12 Rue de la Paix, 44000 Nantes", "12-rue-de-la-paix-44000-nantes"),
        ("Église, 44000 Nantes", "eglise-44000-nantes"),
        ("  12,   Rue Foo -- Bar  ", "12-rue-foo-bar"),
    ],
)
def test_slug_echantillon_reste_aligne_avec_le_frontend(
    adresse: str, slug: str
) -> None:
    assert mesure._slugifier_adresse_echantillon(adresse) == slug


# ---------------------------------------------------------------------------
# 7. Review correction C4 (58-06): the numero-de-voie head regex must be
#    bounded by a negative lookahead so a run of >5 digits renders NULL
#    instead of a truncated, fabricated number. Two guards: (a) the 3 SQL
#    sites embed ONE module-level constant (they cannot diverge -- a
#    site-local literal regressing to the old ``{1,5}``-only pattern would
#    be caught here); (b) a table-driven semantic check via Python `re`
#    (PostgreSQL ARE and `re` share the same lookahead syntax, so this is a
#    faithful proxy for the SQL behaviour without a DB connection).
# ---------------------------------------------------------------------------

_SITES_REGEX_NUMERO_TETE: tuple[tuple[str, str], ...] = (
    ("CONCORDANCE_NUMERO_SQL", mesure.CONCORDANCE_NUMERO_SQL),
    ("CONCORDANCE_STATUT_GEOCODAGE_SQL", mesure.CONCORDANCE_STATUT_GEOCODAGE_SQL),
    ("CONCORDANCE_SCORE_BAN_PALIER_SQL", mesure.CONCORDANCE_SCORE_BAN_PALIER_SQL),
)

# Ancien pattern bugue (avant C4) : capture toujours les 5 premiers chiffres,
# meme quand la tete numerique en compte davantage -- ne doit plus apparaitre
# nulle part ailleurs que dans ce commentaire de test.
_ANCIEN_PATTERN_SANS_LOOKAHEAD = r"^\s*([0-9]{1,5})"


def test_regex_numero_tete_est_une_constante_unique_sur_les_3_sites() -> None:
    """(a) The 3 SQL sites embed ``NUMERO_TETE_RE`` -- no other numero-head
    literal (in particular not the pre-C4 pattern without the lookahead)
    survives anywhere in the module source."""
    for nom, sql in _SITES_REGEX_NUMERO_TETE:
        assert sql.count(mesure.NUMERO_TETE_RE) == 2, (
            f"{nom} ne porte pas exactement 2 occurrences de NUMERO_TETE_RE"
        )

    source = inspect.getsource(mesure)
    occurrences_ancien_pattern = source.count(_ANCIEN_PATTERN_SANS_LOOKAHEAD)
    # NUMERO_TETE_RE lui-meme CONTIENT le prefixe de l'ancien pattern (c'est
    # le meme prefixe, prolonge par le lookahead) -- sa propre definition
    # (1 occurrence) et ses 6 usages `.replace()`-sourced ne comptent PAS ici
    # puisqu'ils passent par le token ``__NUMERO_TETE_RE__``, jamais par le
    # literal brut. Seule la definition de la constante elle-meme matche ce
    # prefixe (elle le prolonge par le lookahead sur la meme ligne).
    assert occurrences_ancien_pattern == 1, (
        f"pattern sans lookahead trouve {occurrences_ancien_pattern} fois, "
        "attendu 1 (uniquement la definition de NUMERO_TETE_RE elle-meme)"
    )


_CAS_NUMERO_TETE: tuple[tuple[str, str | None], ...] = (
    ("123456789012 RUE X", None),
    ("12 RUE", "12"),
    ("12BIS RUE", "12"),
    (" 7 RUE", "7"),
    ("12345 RUE", "12345"),
    ("123456 RUE", None),
    ("RUE SANS NUMERO", None),
)


def test_regex_numero_tete_bornee_par_lookahead() -> None:
    """(b) Table-driven semantic check (PostgreSQL ARE lookahead == `re`
    lookahead): a 6+-digit head must render NULL/None, never a truncated
    5-digit prefix (bug found by review: ``123456789012 RUE X`` used to
    fabricate ``12345`` instead of NULL)."""
    for entree, attendu in _CAS_NUMERO_TETE:
        m = re.match(mesure.NUMERO_TETE_RE, entree)
        obtenu = m.group(1) if m else None
        assert obtenu == attendu, f"{entree!r} -> {obtenu!r} (attendu {attendu!r})"
