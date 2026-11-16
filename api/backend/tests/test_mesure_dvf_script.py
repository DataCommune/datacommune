"""Offline tests for the DVF measurement script (Phase 57, D-22/D-24/T-57-14).

Everything here runs without a database: query execution is faked through a
minimal connection double (``_FausseConnexion``) that records every SQL string
the script emits, and rendering is exercised directly against synthetic
``donnees`` dicts shaped exactly like ``_construire_rapport`` produces them.
The matrix proves two independent things:

- the Markdown/JSON rendering is DETERMINISTIC and COMPLETE (a class or a
  guard counter at zero is a measured zero, never a missing row) -- this is
  what the D-24 checkpoint reads to choose a coverage threshold;
- every SQL statement the script can possibly emit is accepted by the REAL
  ``assert_select_only`` guard imported from ``verify_golden_cases`` (not a
  local copy), the same mechanism that makes the D-30 production replay safe
  (T-57-14).
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

pytestmark = pytest.mark.unit

from app.etl_dvf.qualification import CLASSES_MUTATION
from scripts import mesure_dvf_44_85 as mesure
from scripts.verify_golden_cases import assert_select_only

# ---------------------------------------------------------------------------
# Fake connection: records emitted SQL, returns configured rows by statement.
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


# ---------------------------------------------------------------------------
# Synthetic report data (shape of _construire_rapport's ``donnees``)
# ---------------------------------------------------------------------------


def _donnees_exemple() -> dict[str, Any]:
    return {
        "etiquette": "test",
        "genere_le": "2026-01-01",
        "departements": ["44"],
        "paliers_effectif": list(mesure.PALIERS_EFFECTIF),
        "volumes_par_classe": [
            {
                "code_departement": "44",
                "millesime": 2024,
                "classe_mutation": "residentiel_maison",
                "effectif": 10,
            }
        ],
        "volumes_population_stats": [
            {
                "code_departement": "44",
                "classe_mutation": "residentiel_maison",
                "effectif": 8,
            }
        ],
        "distribution_prix_m2": [
            {
                "code_departement": "44",
                "millesime": 2024,
                "classe_mutation": "residentiel_maison",
                "effectif": 8,
                "q1": Decimal("1500.00"),
                "mediane": Decimal("1800.00"),
                "q3": Decimal("2100.00"),
            }
        ],
        "effectifs_communaux_12m": [
            {
                "code_departement": "44",
                "code_commune": "44100",
                "classe_mutation": "residentiel_maison",
                "effectif": 2,
            },
        ],
        "taux_non_attribuable": [
            {"code_departement": "44", "non_attribuable": 3, "total": 20},
        ],
        "vefa": [
            {
                "code_departement": "44",
                "vefa_total": 20,
                "vefa_sans_surface": 5,
                "vefa_sans_surface_avec_carrez": 2,
            }
        ],
        "garde_valeur_divergente": [
            {
                "code_departement": "44",
                "millesime": 2024,
                "valeur_divergente": 0,
                "multi_disposition": 5,
            }
        ],
        "multi_parcelle_commune": [
            {
                "code_departement": "44",
                "multi_parcelles": 1,
                "multi_communes": 0,
                "total": 20,
            }
        ],
        "controle_integrite": {"lignes_orphelines": 0, "mutations_sans_parcelle": 0},
    }


def _extraire_section(markdown: str, titre_prefixe: str) -> str:
    lignes = markdown.splitlines()
    debut = next(i for i, ligne in enumerate(lignes) if ligne.startswith(titre_prefixe))
    fin = next(
        (i for i in range(debut + 1, len(lignes)) if lignes[i].startswith("## ")),
        len(lignes),
    )
    return "\n".join(lignes[debut:fin])


# ---------------------------------------------------------------------------
# Rendering behaviour
# ---------------------------------------------------------------------------


def test_rendu_deterministe() -> None:
    donnees = _donnees_exemple()
    premiere = mesure._rendre_markdown(donnees)
    seconde = mesure._rendre_markdown(donnees)
    assert premiere == seconde


def test_ordre_des_classes_suit_classes_mutation() -> None:
    donnees = _donnees_exemple()
    section = _extraire_section(mesure._rendre_markdown(donnees), "## 1.")
    position_precedente = -1
    for classe in CLASSES_MUTATION:
        position = section.index(classe)
        assert position > position_precedente
        position_precedente = position


def test_classe_a_effectif_nul_apparait_avec_zero() -> None:
    donnees = _donnees_exemple()
    section = _extraire_section(mesure._rendre_markdown(donnees), "## 1.")
    assert "| 44 | 2024 | hors_perimetre | 0 |" in section
    assert "| 44 | 2024 | TOTAL | 10 |" in section


def test_bloc2_zero_fill_departement_sans_donnees() -> None:
    donnees = _donnees_exemple()
    donnees["departements"] = ["44", "85"]
    markdown = mesure._rendre_markdown(donnees)
    section = _extraire_section(markdown, "## 2.")
    assert "| 85 | residentiel_maison | 0 |" in section
    assert "| 85 | residentiel_appartement | 0 |" in section


def test_bloc4_separe_maison_et_appartement() -> None:
    donnees = _donnees_exemple()
    donnees["effectifs_communaux_12m"].append(
        {
            "code_departement": "44",
            "code_commune": "44100",
            "classe_mutation": "residentiel_appartement",
            "effectif": 7,
        }
    )
    section = _extraire_section(mesure._rendre_markdown(donnees), "## 4.")
    assert "| 44 | residentiel_maison | 1 | 0 | 1 | 1 | 1 | 1 | 1 |" in section
    assert "| 44 | residentiel_appartement | 1 | 0 | 0 | 0 | 1 | 1 | 1 |" in section


def test_bloc4_compte_commune_sans_donnees_dans_le_cout_nd() -> None:
    donnees = _donnees_exemple()
    donnees["effectifs_communaux_12m"].append(
        {
            "code_departement": "44",
            "code_commune": "44100",
            "classe_mutation": "residentiel_appartement",
            "effectif": 0,
        }
    )
    section = _extraire_section(mesure._rendre_markdown(donnees), "## 4.")
    assert "| 44 | residentiel_appartement | 1 | 1 | 1 | 1 | 1 | 1 | 1 |" in section


def test_requete_bloc4_reproduit_population_matview() -> None:
    sql = mesure.EFFECTIF_COMMUNAL_12M_SQL
    assert "g.classe_mutation" in sql
    assert "g.est_vente = true" in sql
    assert "g.hors_stats_vefa = false" in sql
    assert "g.surface_bati_totale > 0" in sql
    assert "g.valeur_fonciere > 0" in sql
    assert "g.nb_communes = 1" in sql
    assert "FROM geo_communes" in sql
    assert "CROSS JOIN classes" in sql
    assert "COALESCE(e.effectif, 0)" in sql
    assert "g.date_mutation >= borne.date_max" in sql


def test_garde_valeur_divergente_apparait_a_zero() -> None:
    donnees = _donnees_exemple()
    section = _extraire_section(mesure._rendre_markdown(donnees), "## 7.")
    assert "| 44 | 2024 | 0 | 5 |" in section


def test_bloc_vefa_distingue_trois_nombres() -> None:
    donnees = _donnees_exemple()
    section = _extraire_section(mesure._rendre_markdown(donnees), "## 6.")
    assert "| 44 | 20 | 5 | 2 |" in section


def test_json_contient_les_memes_nombres_que_le_markdown() -> None:
    reponses = {
        _normalise(mesure.VOLUMES_PAR_CLASSE_SQL): [
            {
                "code_departement": "44",
                "millesime": 2024,
                "classe_mutation": "residentiel_maison",
                "effectif": 42,
            }
        ],
    }
    conn = _FausseConnexion(reponses)
    markdown, donnees = mesure._construire_rapport(
        conn, ["44"], "test", date(2026, 1, 1)
    )
    assert "| 44 | 2024 | residentiel_maison | 42 |" in markdown
    contenu_json = json.loads(json.dumps(donnees, cls=mesure._EncodeurDecimal))
    assert contenu_json["volumes_par_classe"][0]["effectif"] == 42


# ---------------------------------------------------------------------------
# Read-only guard (T-57-14): every emitted statement must clear the REAL gate
# ---------------------------------------------------------------------------


def test_toutes_les_requetes_sont_acceptees_par_la_garde_reelle() -> None:
    conn = _FausseConnexion()
    mesure._construire_rapport(conn, ["44"], "test", date(2026, 1, 1))
    assert conn.requetes_emises, "le script doit emettre au moins une requete"
    for sql in conn.requetes_emises:
        assert_select_only(sql, mesure.ALLOWED_SQL)
