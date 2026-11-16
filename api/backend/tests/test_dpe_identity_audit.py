"""Offline tests of the DPE identity audit (plan 04-14, ``--identity-audit``).

No database, no network. The tracer follows the RNB provenance of one
archived source row (a real source archive written by ``archive_sources``)
through the stored projection to the aggregated, closed report. The other
cases cover: field not requested, missing file, empty field, faulty
transformation, data changed in a newer source, RNB derived from BAN, SQL
error, sentinel identifiers, and the D-110 usage variants derived without
touching the decided rule (N-12, P-D110-NULL).
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
import pytest

pytestmark = pytest.mark.unit

from scripts import mesure_dpe_documents as mesure
from scripts import verify_golden_cases
from scripts.verify_golden_cases import GoldenSecurityError, assert_select_only

POISON_DPE = "2644E0039039K"
POISON_BAN = "44021_0104_00273"
POISON_PARCELLE = "44021000AB0001"
POISON_RNB = "MYJRCKS1NNDB"
IMPORT_ID = "77d3bd67048c4f00a21f0ddd034480dc"
MOTIFS_FUITE = (
    re.compile(r"(?<![0-9A-Za-z])\d{4}[A-Z]\d{7}[A-Z](?![0-9A-Za-z])"),
    re.compile(r"\d{5}_[0-9A-Za-z]{4,6}_\d{5}"),
    re.compile(re.escape(POISON_PARCELLE)),
    re.compile(re.escape(POISON_RNB)),
)


def _normalise(sql: str) -> str:
    return " ".join(sql.split())


class _Resultat:
    def __init__(self, lignes: list[dict[str, Any]]) -> None:
        self._lignes = lignes

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._lignes)

    def fetchone(self) -> dict[str, Any] | None:
        return self._lignes[0] if self._lignes else None

    def __iter__(self):
        return iter(self._lignes)


class _Connexion:
    def __init__(
        self,
        reponses: dict[str, list[dict[str, Any]]] | None = None,
        erreur_sur: str | None = None,
    ) -> None:
        self.reponses = reponses or {}
        self.requetes: list[str] = []
        self.erreur_sur = erreur_sur
        self.read_only = False
        self.isolation_level = None
        self.fermee = False

    def execute(self, sql: str, params: Any = None) -> _Resultat:
        self.requetes.append(sql)
        if _normalise(sql) == _normalise(verify_golden_cases.READ_ONLY_STATE_SQL):
            return _Resultat([{"state": "on"}])
        if self.erreur_sur is not None and _normalise(sql) == _normalise(
            self.erreur_sur
        ):
            raise psycopg.errors.QueryCanceled("canceling statement")
        return _Resultat(self.reponses.get(_normalise(sql), []))

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.fermee = True


# --- archived generation ---------------------------------------------------------


def _ligne_source(numero: str, **valeurs: Any) -> dict[str, Any]:
    ligne: dict[str, Any] = dict.fromkeys(mesure.CHAMPS_SELECTIONNES, "")
    ligne.update(
        numero_dpe=numero,
        date_etablissement_dpe="2024-01-02",
        type_batiment="maison",
        surface_habitable_logement="80.5",
        etiquette_dpe="D",
        code_departement_ban="44",
        code_insee_ban="44109",
        identifiant_ban=POISON_BAN,
        coordonnee_cartographique_x_ban="355000.1",
        coordonnee_cartographique_y_ban="6690000.2",
        id_rnb=POISON_RNB,
        provenance_id_rnb="Reprise RNB",
        adresse_brut="12 rue du Port",
        version_dpe="2.4",
        date_derniere_modification_dpe="2024-02-01",
    )
    ligne.update(valeurs)
    return ligne


def _stockee(ligne: dict[str, Any], **surcharges: Any) -> dict[str, Any]:
    """The stored projection a correct import gives for an archived row."""
    stockee: dict[str, Any] = {}
    for champ, colonne in mesure.STOCKAGE.items():
        if colonne is None or colonne == "position":
            continue
        brut = str(ligne.get(champ, "")).strip() or None
        nature = mesure.NATURES_STOCKAGE.get(champ, ("texte", None, None))[0]
        if brut is not None and nature == "decimal":
            brut = Decimal(brut)
        elif brut is not None and nature == "entier":
            brut = int(brut)
        elif brut is not None and nature == "date":
            brut = date.fromisoformat(brut)
        stockee[colonne] = brut
    stockee["a_position"] = mesure.position_valide(
        ligne["coordonnee_cartographique_x_ban"],
        ligne["coordonnee_cartographique_y_ban"],
    )
    stockee["date_issues"] = []
    stockee.update(surcharges)
    return stockee


def _csv(lignes: list[dict[str, Any]]) -> str:
    flux = io.StringIO(newline="")
    ecrivain = csv.DictWriter(
        flux, fieldnames=list(mesure.CHAMPS_SELECTIONNES), quoting=csv.QUOTE_NOTNULL
    )
    ecrivain.writeheader()
    for ligne in lignes:
        ecrivain.writerow(ligne)
    return flux.getvalue()


def _archive(
    tmp_path: Path,
    fichiers: dict[str, str],
    *,
    dataset: str = "dpe",
    run_id: str = "29-20260925T200621Z",
    racine: Path | None = None,
    binaire: bool = False,
) -> Path:
    from app.etl import source_archive

    racine = racine or tmp_path / "archive"
    if not racine.exists():
        racine.mkdir()
        (racine / source_archive.SENTINEL_NAME).write_text(
            source_archive.SENTINEL_CONTENT, encoding="utf-8"
        )
    dl = tmp_path / f"dl-{dataset}-{run_id}"
    chemins = []
    for relpath, contenu in fichiers.items():
        chemin = dl / relpath
        chemin.parent.mkdir(parents=True, exist_ok=True)
        if binaire:
            chemin.write_bytes(contenu)  # type: ignore[arg-type]
        else:
            chemin.write_text(contenu, encoding="utf-8", newline="")
        chemins.append(chemin)
    source_archive.archive_sources(
        racine, dataset, run_id, chemins, dl, "telechargement"
    )
    return racine


def _relpath(slug: str, dep: str = "44") -> str:
    return f"raw/{IMPORT_ID}/{IMPORT_ID}-{slug}-{dep}.csv"


# --- tracer: RNB provenance from the archived row to the report ------------------


def _rapport_tracer(tmp_path: Path, **valeurs: Any) -> tuple[dict, Any]:
    """One archived row, its correct stored projection, the closed report."""
    ligne = _ligne_source(POISON_DPE, **valeurs)
    racine = _archive(
        tmp_path,
        {
            _relpath("dpe03existant"): _csv([ligne]),
            _relpath("dpe02neuf"): _csv([]),
        },
    )
    stockee = _stockee(ligne, numero_dpe=POISON_DPE)
    conn = _Connexion(
        {
            _normalise(mesure.GENERATIONS_DPE_SQL): [
                {
                    "code_departement": "44",
                    "regime": "existant",
                    "source_slug": "dpe03existant",
                    "import_id": IMPORT_ID,
                    "source_version": "v1",
                    "observed_at": "2026-09-25T20:30:05Z",
                    "effectif": 1,
                    "documents": 1,
                }
            ],
            _normalise(mesure.LIGNES_DPE_DEPARTEMENT_SQL): [stockee],
            _normalise(mesure.RNB_LIGNEE_SQL): [
                {
                    "code_departement": "44",
                    "type_batiment": "maison",
                    "provenance": "Reprise RNB",
                    "id_rnb_renseigne": True,
                    "cle_jointe": True,
                    "cle_dans_rnb_liens": True,
                    "id_rnb_lie_a_la_cle": True,
                    "id_rnb_dans_rnb_liens": True,
                    "effectif": 1,
                    "id_rnb": POISON_RNB,
                }
            ],
        }
    )
    mesures = mesure.mesurer_identite(
        conn,
        ["44"],
        {"date_import": "2026-09-25", "date_mesure": "2026-10-06"},
        racine,
    )
    rapport = mesure.construire_rapport_identite(mesures)
    return rapport, conn


def test_tracer_provenance_rnb_de_la_ligne_archivee_au_rapport(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The 04-14 state, frozen here: version_dpe and one lifecycle date were
    # requested and archived but not stored. Plan 04-15 stores them (see
    # test_les_champs_stockes_par_0415_sont_identiques_a_l_archive); the
    # detection of such a loss must keep working.
    for champ in ("version_dpe", "date_derniere_modification_dpe"):
        monkeypatch.setitem(mesure.STOCKAGE, champ, None)
    rapport, conn = _rapport_tracer(tmp_path)
    assert tuple(rapport) == mesure.CLES_RAPPORT_IDENTITE

    avb = rapport["lineage"]["archive_vs_base"]
    assert avb["statut"] == "mesure"
    assert avb["run_archive"] == "29-20260925T200621Z"
    existant = avb["departements"]["44"]["par_source"]["dpe03existant"]
    assert existant["champs"]["id_rnb"] == {"identique": 1}
    assert existant["champs"]["provenance_id_rnb"] == {"identique": 1}
    assert existant["renseigne"]["provenance_id_rnb"] == 1

    champ = rapport["fields"]["provenance_id_rnb"]
    assert champ["etat"] == "stocke"
    assert champ["archive"]["dpe03existant-44"] == "archive"
    assert champ["renseigne_archive"]["dpe03existant-44"]["numerateur"] == 1
    assert champ["renseigne_archive"]["dpe03existant-44"]["denominateur"] == 1

    rnb = rapport["lineage"]["rnb"]["departements"]["44"]["Reprise RNB"]
    assert rnb["id_rnb_lie_a_la_cle_ban"]["numerateur"] == 1
    ambigu = [e for e in rapport["losses"] if e["type"] == "rnb_correle_ban"]
    assert ambigu and ambigu[0]["classe"] == "ambiguite"

    # requested and archived, never stored: a storage loss with its proof
    non_stockes = {
        e["champ"] for e in rapport["losses"] if e["type"] == "champ_archive_non_stocke"
    }
    assert {"version_dpe", "date_derniere_modification_dpe"} <= non_stockes
    assert rapport["fields"]["version_dpe"]["etat"] == "archive_non_stocke"

    for sql in conn.requetes:
        assert_select_only(sql, mesure.ALLOWED_SQL | mesure.ALLOWED_SQL_IDENTITE)
    texte = mesure.serialiser_identite(rapport)
    for motif in MOTIFS_FUITE:
        assert not motif.search(texte)


def test_les_champs_stockes_par_0415_sont_identiques_a_l_archive(
    tmp_path: Path,
) -> None:
    """Plan 04-15: C1 to C3 are stored; a correct import shows no loss."""
    rapport, _ = _rapport_tracer(
        tmp_path,
        date_reception_dpe="2024-01-30",
        conso_5_usages_par_m2_ep="226.10",
        numero_dpe_immeuble_associe="",
    )
    existant = rapport["lineage"]["archive_vs_base"]["departements"]["44"][
        "par_source"
    ]["dpe03existant"]
    assert existant["champs"]["version_dpe"] == {"identique": 1}
    assert existant["champs"]["date_reception_dpe"] == {"identique": 1}
    assert existant["champs"]["conso_5_usages_par_m2_ep"] == {"identique": 1}
    assert existant["champs"]["numero_dpe_immeuble_associe"] == {"vides": 1}
    for champ in ("version_dpe", "conso_5_usages_par_m2_ep"):
        assert rapport["fields"][champ]["etat"] == "stocke"
    assert not [e for e in rapport["losses"] if e["type"] == "champ_archive_non_stocke"]


# --- field classes -------------------------------------------------------------


@pytest.mark.parametrize(
    ("champ", "brut", "stocke", "issues", "attendu"),
    [
        ("adresse_brut", " 12 rue du Port ", "12 rue du Port", (), "identique"),
        ("adresse_brut", "", None, (), "vides"),
        ("adresse_brut", "12 rue du Port", None, (), "perte"),
        ("adresse_brut", "", "12 rue du Port", (), "ajout"),
        (
            "adresse_brut",
            "12 rue\r\ndu Port",
            "12 rue\ndu Port",
            (),
            "fin_de_ligne_normalisee",
        ),
        ("adresse_brut", "12 rue du Port", "12 rue du Pont", (), "difference"),
        ("surface_habitable_logement", "80.5", Decimal("80.50"), (), "identique"),
        ("surface_habitable_logement", "80.555", Decimal("80.56"), (), "arrondi"),
        ("surface_habitable_logement", "1234567", Decimal("999999.99"), (), "borne"),
        ("surface_habitable_logement", "80.5", Decimal("81"), (), "difference"),
        ("identifiant_ban", "x" * 60, "x" * 50, (), "tronque"),
        ("annee_construction", "1975", 1975, (), "identique"),
        ("annee_construction", "1975", 1976, (), "difference"),
        ("date_etablissement_dpe", "2024-01-02", date(2024, 1, 2), (), "identique"),
        (
            "date_etablissement_dpe",
            "2024-13-45",
            None,
            ("invalid_establishment_date",),
            "date_invalide_videe",
        ),
        ("date_fin_validite_dpe", "2034-01-02", None, (), "perte"),
    ],
)
def test_classer_valeur(
    champ: str, brut: str, stocke: Any, issues: tuple[str, ...], attendu: str
) -> None:
    assert mesure.classer_valeur(champ, brut, stocke, issues) == attendu


def test_position_meme_predicat_que_le_pipeline() -> None:
    from app.etl_dpe.pipeline import DpePipeline

    for x, y in (
        ("355000.1", "6690000.2"),
        ("0", "0"),
        ("", "6690000"),
        ("nan", "1"),
        ("inf", "1"),
        (None, None),
        ("abc", "1"),
    ):
        ligne = {
            "coordonnee_cartographique_x_ban": x,
            "coordonnee_cartographique_y_ban": y,
        }
        assert mesure.position_valide(x, y) is not DpePipeline._invalid_geometry(ligne)
    assert mesure.classer_position("1", "2", False) == "perte"
    assert mesure.classer_position("", "", True) == "ajout"
    assert mesure.classer_position("1", "2", True) == "presence_conforme"


def test_une_perte_injectee_et_une_transformation_fautive_sont_detectees() -> None:
    a = _ligne_source("A0000000000AA", adresse_complete_brut="12 rue du Port 44000")
    b = _ligne_source("B0000000000BB", surface_habitable_logement="70")
    c = _ligne_source("C0000000000CC")
    base = {
        "A0000000000AA": _stockee(a, adresse_complete_brut=None),
        "B0000000000BB": _stockee(b, surface_habitable=Decimal("71")),
    }
    bloc = mesure.comparer_generation(
        {"dpe03existant": [a, b, c, dict(c)], "dpe02neuf": []}, base
    )
    champs = bloc["par_source"]["dpe03existant"]["champs"]
    assert champs["adresse_complete_brut"]["perte"] == 1
    assert champs["surface_habitable_logement"]["difference"] == 1
    assert bloc["absents_de_la_base"] == 1
    assert bloc["doublons"] == {"identiques": 1, "differents": 0}
    assert bloc["par_source"]["dpe03existant"]["premieres_occurrences"] == 3
    types = {
        (e["type"], e.get("champ"))
        for e in mesure.registre_ecarts(
            {"archive_vs_base": {"statut": "mesure", "departements": {"44": bloc}}}
        )
    }
    assert ("perte", "adresse_complete_brut") in types
    assert ("difference", "surface_habitable_logement") in types
    assert ("lignes_absentes_de_la_base", None) in types
    assert ("doublon_source_identique", None) in types


def test_champ_vide_garde_son_denominateur() -> None:
    lignes = [_ligne_source(f"A00000000{i:02d}AA", id_rnb="") for i in range(3)]
    bloc = mesure.comparer_generation(
        {"dpe03existant": lignes, "dpe02neuf": []},
        {lig["numero_dpe"]: _stockee(lig) for lig in lignes},
    )
    source = bloc["par_source"]["dpe03existant"]
    assert source["renseigne"]["id_rnb"] == 0
    assert source["champs"]["id_rnb"] == {"vides": 3}
    champs = mesure.inventorier_champs(
        {"archive_vs_base": {"statut": "mesure", "departements": {"44": bloc}}}, None
    )
    renseigne = champs["id_rnb"]["renseigne_archive"]["dpe03existant-44"]
    assert (renseigne["numerateur"], renseigne["denominateur"]) == (0, 3)
    assert renseigne["part"] == 0


def test_champ_non_demande_n_est_pas_absent_chez_le_producteur() -> None:
    schema = [{"key": c, "type": "string"} for c in mesure.CHAMPS_SELECTIONNES]
    schema += [
        {"key": "numero_rpls_logement", "type": "string", "x-cardinality": 80},
        {"key": "conso_5_usages_par_m2_ep", "type": "number"},
    ]
    schemas = {slug: {"schema": schema} for slug in mesure.REGIMES_SOURCES}
    champs = mesure.inventorier_champs({}, schemas)
    rpls = champs["numero_rpls_logement"]
    assert rpls["etat"] == "non_selectionne"
    assert rpls["schema"] == {"dpe03existant": "present", "dpe02neuf": "present"}
    assert rpls["stockage"] == "non_demande"
    assert rpls["cardinalite_declaree"]["dpe02neuf"] == 80
    assert champs["conso_5_usages_par_m2_ep"]["famille"] == "valeurs_par_m2"
    # searched family member truly absent from both schemas (since 04-15,
    # numero_dpe_immeuble_associe is requested and stored, C3)
    assert champs["complement_adresse_batiment"]["etat"] == "absent_du_schema"
    assert champs["numero_dpe_immeuble_associe"]["etat"] == "stocke"
    # without metadata nothing is declared absent at the producer
    sans = mesure.inventorier_champs({}, None)
    assert sans["numero_rpls_logement"]["schema"] == {
        "dpe03existant": "non_mesure",
        "dpe02neuf": "non_mesure",
    }
    assert sans["numero_rpls_logement"]["etat"] == "non_selectionne"
    assert "absent_du_schema" not in {c["etat"] for c in sans.values()}


def test_fichier_absent_est_une_panne_pas_un_zero(tmp_path: Path) -> None:
    racine = _archive(
        tmp_path, {_relpath("dpe03existant"): _csv([_ligne_source("A0000000000AA")])}
    )
    conn = _Connexion()
    archives, avb = mesure.mesurer_lignee_archive(conn, ["44"], racine, {IMPORT_ID})
    assert archives["statut"] == "mesure"
    assert avb["departements"]["44"]["etat"] == "non_mesure"
    assert "dpe02neuf" in avb["departements"]["44"]["raison"]
    ecarts = mesure.registre_ecarts({"archive_vs_base": avb})
    assert [e["classe"] for e in ecarts] == ["panne"]
    assert ecarts[0]["etat"] == "non_mesure"
    assert "numerateur" not in ecarts[0]

    _, sans_archive = mesure.mesurer_lignee_archive(conn, ["44"], None, {IMPORT_ID})
    assert sans_archive["etat"] == "non_mesure"
    _, autre_generation = mesure.mesurer_lignee_archive(
        conn, ["44"], racine, {"0" * 32}
    )
    assert autre_generation["etat"] == "non_mesure"


def test_donnee_changee_dans_une_source_plus_recente_n_est_pas_une_perte(
    tmp_path: Path,
) -> None:
    ancienne = _ligne_source("A0000000000AA")
    recente = _ligne_source("A0000000000AA", adresse_brut="14 rue du Port")
    fichiers = {_relpath("dpe02neuf"): _csv([])}
    racine = _archive(
        tmp_path,
        {**fichiers, _relpath("dpe03existant"): _csv([ancienne])},
        run_id="12-20260924T224808Z",
    )
    autre = "0" * 31 + "1"
    _archive(
        tmp_path,
        {
            f"raw/{autre}/{autre}-dpe03existant-44.csv": _csv([recente]),
            f"raw/{autre}/{autre}-dpe02neuf-44.csv": _csv([]),
        },
        run_id="40-20261001T000000Z",
        racine=racine,
    )
    conn = _Connexion(
        {_normalise(mesure.LIGNES_DPE_DEPARTEMENT_SQL): [_stockee(ancienne)]}
    )
    archives, avb = mesure.mesurer_lignee_archive(conn, ["44"], racine, {IMPORT_ID})
    assert avb["run_archive"] == "12-20260924T224808Z"
    evolution = archives["evolution_entre_records"][0]
    assert evolution["fichiers_identiques"] < evolution["fichiers_communs"]
    assert "pas une perte" in evolution["lecture"]
    champs = avb["departements"]["44"]["par_source"]["dpe03existant"]["champs"]
    assert champs["adresse_brut"] == {"identique": 1}
    assert not mesure.registre_ecarts({"archive_vs_base": avb}) or all(
        e["type"] == "champ_archive_non_stocke"
        for e in mesure.registre_ecarts({"archive_vs_base": avb})
    )


def test_rnb_derive_de_ban_est_une_ambiguite_pas_une_preuve() -> None:
    lignes = [
        {
            "code_departement": "44",
            "type_batiment": "maison",
            "provenance": provenance,
            "id_rnb_renseigne": provenance is not None,
            "cle_jointe": True,
            "cle_dans_rnb_liens": provenance is not None,
            "id_rnb_lie_a_la_cle": lie,
            "id_rnb_dans_rnb_liens": provenance is not None,
            "effectif": n,
        }
        for provenance, lie, n in (
            ("Reprise RNB", True, 9),
            ("Reprise RNB", False, 1),
            ("Logiciel", True, 2),
            ("Logiciel", False, 2),
            (None, False, 5),
        )
    ]
    rnb = mesure.agreger_rnb_lignee(lignes)
    reprise = rnb["departements"]["44"]["Reprise RNB"]["id_rnb_lie_a_la_cle_ban"]
    assert (reprise["numerateur"], reprise["denominateur"]) == (9, 10)
    logiciel = rnb["departements"]["44"]["Logiciel"]["id_rnb_lie_a_la_cle_ban"]
    assert logiciel["part"] == 0.5
    sans = rnb["departements"]["44"]["(null)"]["id_rnb_lie_a_la_cle_ban"]
    assert sans["denominateur"] == 0 and sans["part"] is None
    assert "jamais une preuve" in rnb["lecture"]
    ecarts = mesure.registre_ecarts({"rnb": rnb})
    assert [e["classe"] for e in ecarts] == ["ambiguite"]


def test_erreur_sql_sortie_1_sans_rapport(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    conn = _Connexion(erreur_sur=mesure.GENERATIONS_DPE_SQL)
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    monkeypatch.delenv("SOURCE_ARCHIVE_DIR", raising=False)
    monkeypatch.setattr(verify_golden_cases.psycopg, "connect", lambda *a, **k: conn)
    assert mesure.main(["--base", "--identity-audit", "--json", "-"]) == 1
    sortie = capsys.readouterr()
    assert sortie.out == ""
    assert "bloquee" in sortie.err
    assert conn.fermee


def test_sentinelles_refusees_par_le_serialiseur() -> None:
    for fuite in (POISON_DPE, POISON_BAN, POISON_PARCELLE, POISON_RNB):
        with pytest.raises(GoldenSecurityError):
            mesure.serialiser_identite({"fuite": fuite})
    mesure.serialiser_identite(
        {
            "import_ids": [IMPORT_ID],
            "sha": "a" * 64,
            "usage": "Indifférencié",
            "provenance": "Reprise RNB",
        }
    )


def test_variantes_usage_derivees_sans_toucher_la_regle_ni_la_concordance() -> None:
    decidee = mesure.FAITS_DOCUMENTS_BASE_SQL
    assert decidee.count("bb.usage_1 IS DISTINCT FROM 'Annexe'") == 1
    debut = decidee.index("CASE\n           WHEN NOT cle_jointe THEN NULL")
    for nom, sql in mesure.VARIANTES_USAGE.items():
        assert sql != decidee, nom
        assert "IS DISTINCT FROM 'Annexe'" not in sql
        # the concordance verdict block is byte-identical
        assert (
            sql[sql.index("CASE\n           WHEN NOT cle_jointe THEN NULL") :]
            == (decidee[debut:])
        )
        assert_select_only(sql, mesure.ALLOWED_SQL_IDENTITE)


def test_variante_refusee_si_le_compte_n_est_pas_trouve_une_fois(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mesure, "FAITS_DOCUMENTS_BASE_SQL", "SELECT 1")
    with pytest.raises(ValueError):
        mesure.variante_usage_sql("COUNT(*) AS n_hors_annexe")


def test_variantes_usage_mesurent_un_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    def fait(hors_annexe: str) -> dict[str, Any]:
        return {
            "code_departement": "44",
            "regime": "existant",
            "type_batiment": "maison",
            "cle_vide": False,
            "cle_jointe": True,
            "adresse_sans_position": False,
            "graphe_incoherent": False,
            "tombstone": False,
            "premier_de_cle": True,
            "premiere_maison_consultable_de_cle": True,
            "classe_docs_cle": "1",
            "batiments_parcelle": "2+",
            "batiments_hors_annexe": hors_annexe,
            "concordance_adresse": "concordant",
            "effectif": 1,
        }

    reponses = {
        _normalise(sql): [fait("2+" if "indifferencie" in nom else "1")]
        for nom, sql in mesure.VARIANTES_USAGE.items()
    }
    reponses[_normalise(mesure.USAGES_BATIMENTS_SQL)] = [
        {"code_departement": "44", "usage": "Indifférencié", "effectif": 3},
        {"code_departement": "44", "usage": "Annexe", "effectif": 1},
    ]
    decide = mesure.agreger_maisons_d110([fait("1")])
    resultat = mesure.mesurer_variantes_usage(
        _Connexion(reponses), ["44"], date(2026, 9, 25), date(2026, 10, 6), decide
    )
    dep = resultat["departements"]["44"]
    assert dep["regle_decidee"]["iv_cumul_hors_annexes"] == 1
    assert dep["variantes"]["usage_null_exclu_du_compte"]["delta_iv"] == 0
    assert (
        dep["variantes"]["aucun_constat_si_usage_null_ou_indifferencie"]["delta_iv"]
        == -1
    )
    assert dep["usages_batiments"]["Indifférencié"] == 3
    assert "ni rouverte" in resultat["regle_decidee"]


def test_adresses_hors_parcelle_partition_et_accord_rnb() -> None:
    def adresse(cle: str, commune: str, dans: bool, **rnb: Any) -> dict[str, Any]:
        return {
            "ban_id": cle,
            "code_commune": commune,
            "a_position": True,
            "point_dans_parcelle": dans,
            "n_batiments_rnb": rnb.get("n", 0),
            "n_parcelles_rnb": rnb.get("p", 0),
            "n_parcelles_rnb_locales": rnb.get("l", 0),
            "point_dans_parcelles_rnb": rnb.get("inclus", False),
        }

    adresses = [
        adresse("k1", "44109", True, n=1, p=1, l=1, inclus=True),
        adresse("k2", "44109", True, n=2, p=1, l=1, inclus=False),
        adresse("k3", "44999", False, n=1, p=1, l=0),
        adresse("k4", "44109", False),
        adresse("k5", "44109", False, n=1, p=1, l=1),
        adresse("k6", "44109", False),
        adresse("k7", "44109", False),
        {**adresse("k8", "44109", False), "a_position": False},
    ]
    ban = {
        "k1": (["P1"], "entrée"),
        "k2": (["P1"], "entrée"),
        "k3": (["P9"], "parcelle"),
        "k4": ([], "entrée"),
        "k5": (["PX"], "parcelle"),
        "k6": (["P1"], "entrée"),
    }
    bloc = mesure.agreger_adresses_parcelle(
        "44",
        adresses,
        ban,
        parcelles_locales={"P1", "P2"},
        communes_locales={"44109"},
        parcelles_source={"P1", "P2", "PX"},
        communes_source={"44109", "44999"},
    )
    assert bloc["point_hors_parcelle"]["numerateur"] == 5
    assert bloc["point_hors_parcelle"]["denominateur"] == 7
    assert bloc["hors_parcelle_par_cause"] == {
        "absente_du_csv_ban": 1,
        "commune_absente_du_cadastre_local": 1,
        "cad_parcelles_vide": 1,
        "parcelle_citee_absente_du_cadastre_local": 1,
        "parcelle_citee_presente_point_hors_geometrie": 1,
    }
    assert bloc["controle_partition"] is True
    assert (
        bloc["hors_parcelle_type_parcelle_par_cause"][
            "parcelle_citee_absente_du_cadastre_local"
        ]
        == 1
    )
    assert bloc["part_expliquee_par_le_cadastre_local"]["numerateur"] == 2
    assert bloc["source_archivee_pour_les_causes_cadastre"] == {
        "commune_dans_la_source_archivee": 1,
        "parcelle_citee_dans_la_source_archivee": 1,
    }
    rnb = bloc["rnb_liens"]
    assert rnb["hors_parcelle_avec_lien"]["numerateur"] == 2
    accord = rnb["accord_avec_la_parcelle_du_point"]
    assert accord["accord_parcelle_unique"]["numerateur"] == 1
    assert accord["accord_parcelle_unique"]["denominateur"] == 2
    ecarts = mesure.registre_ecarts(
        {"adresse_batiment_parcelle": {"departements": {"44": bloc}}}
    )
    assert {e["type"] for e in ecarts} == {"cadastre_perte_import"}
    assert ecarts[0]["numerateur"] == 2


def test_lire_cadastre_source_et_comparaison() -> None:
    lignes = [
        '{"type":"FeatureCollection","features":[\n',
        '{"type":"Feature","id":"P1","geometry":null,"properties":{"id":"P1",'
        '"commune":"44109","prefixe":"000"}},\n',
        '{"type":"Feature","id":"P2","geometry":null,"properties":{"id":"P2",'
        '"commune":"44110","prefixe":"000"}}\n',
        "]}\n",
    ]
    lu = mesure.lire_cadastre_source(lignes)
    assert lu["ids"] == {"P1", "P2"}
    assert lu["communes"] == {"44109", "44110"}
    assert (lu["features"], lu["non_reconnues"]) == (2, 0)
    assert mesure.comparer_cadastre({"P1", "P3"}, lu["ids"]) == {
        "parcelles_source_archivee": 2,
        "parcelles_en_base": 2,
        "source_absentes_de_la_base": 1,
        "base_absentes_de_la_source": 1,
    }
    assert (
        mesure.lire_cadastre_source(['{"type":"Feature","x":1}'])["non_reconnues"] == 1
    )


def test_toutes_les_requetes_identite_passent_la_garde() -> None:
    assert mesure.ALLOWED_SQL_IDENTITE
    for sql in mesure.ALLOWED_SQL_IDENTITE:
        assert_select_only(sql, mesure.ALLOWED_SQL_IDENTITE)
        texte = _normalise(sql).upper()
        assert texte.startswith(("SELECT", "WITH"))
        for verbe in ("INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"):
            assert f" {verbe} " not in f" {texte} "
    with pytest.raises(GoldenSecurityError):
        mesure._executer_identite(_Connexion(), "SELECT 1", {})


def test_finalisation_hors_ligne_complete_le_schema(tmp_path: Path) -> None:
    mesures = {
        "generations": {
            "observations_en_base": [
                {"source": "dpe02neuf", "source_version": "v-neuf"},
            ]
        },
        "lineage": {"archive_vs_base": {"statut": "non_mesure", "raison": "x"}},
        "commands": [{"commande": "ct"}],
    }
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps({"identity_audit": mesure.construire_rapport_identite(mesures)}),
        encoding="utf-8",
    )
    meta = tmp_path / "neuf.json"
    meta.write_text(
        json.dumps(
            {
                "id": "autre",
                "slug": "dpe02neuf",
                "schema": [{"key": "numero_rpls_logement"}],
            }
        ),
        encoding="utf-8",
    )
    sortie = tmp_path / "sortie.json"
    assert (
        mesure.main(
            [
                "--identity-audit",
                "--rapport-base",
                str(base),
                "--schema-metadata",
                f"dpe02neuf={meta}",
                "--json",
                str(sortie),
            ]
        )
        == 0
    )
    rapport = json.loads(sortie.read_text(encoding="utf-8"))
    assert tuple(rapport) == tuple(sorted(mesure.CLES_RAPPORT_IDENTITE))
    neuf = rapport["sources"]["dpe02neuf"]["schema_live"]
    assert neuf["compatible_avec_la_selection"] is False
    assert neuf["meme_version_que_la_generation"] is False
    assert rapport["sources"]["dpe03existant"]["schema_live"]["etat"] == "non_mesure"
    assert rapport["commands"][0]["commande"] == "ct"
    assert len(rapport["commands"]) == 2
    assert rapport["losses"][0]["classe"] == "panne"
    assert rapport["fields"]["numero_rpls_logement"]["schema"]["dpe02neuf"] == (
        "present"
    )


def test_identite_sans_base_ni_rapport_sort_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert mesure.main(["--identity-audit"]) == 2
    assert "usage" in capsys.readouterr().err.lower()


def test_cadastre_gz_lisible_dans_une_archive(tmp_path: Path) -> None:
    contenu = gzip.compress(
        b'{"type":"FeatureCollection","features":[\n'
        b'{"type":"Feature","id":"P1","geometry":null,"properties":{"id":"P1",'
        b'"commune":"44109","prefixe":"000"}}\n]}\n'
    )
    racine = _archive(
        tmp_path,
        {"raw/cadastre-44-parcelles.json.gz": contenu},
        dataset="cadastre",
        run_id="35-20260926T095932Z",
        binaire=True,
    )
    from app.etl.source_archive import load_replay

    replay = load_replay(racine, "cadastre", "35-20260926T095932Z")
    objet = racine / "cadastre" / replay.files[0]["object"]
    with gzip.open(objet, "rt", encoding="utf-8") as flux:
        assert mesure.lire_cadastre_source(flux)["ids"] == {"P1"}
