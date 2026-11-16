#!/usr/bin/env python3
"""BDNB equivalence gate: the file rule against the open API (04.7-19, T5).

The ``construction`` theme will read an imported table built from the
producer's departmental zips instead of ``api.bdnb.io``. This script checks,
on a stratified sample of parcels, that the rule written in the plan
reproduces what the API returns today:

    for a parcel, every row of ``rel_batiment_groupe_parcelle`` (main or not,
    de-duplicated by (parcel, group)) LEFT JOINed on ``batiment_groupe_ffo_bat``
    (de-duplicated by group): year, wall and roof material.

Four modes, JSON on stdout (a summary) and in ``--out`` (the full document).
Standard library only: no ``app`` import, no database, no archive extraction
(members are read by name from the zips, never extracted):

    echantillon --zips DIR --graine 20261102 --out FILE
        Strata S1-S6 drawn from every ``dep*.zip`` of DIR, ordered by
        ``sha256(graine || parcel)``, plus two fixed parcels. Writes the exact
        page cost: sum(g // 10 + 1) for a parcel of g groups, plus the one
        request that reads the API vintage. Over ``--budget``, S5 then S6 are
        reduced and the reason is recorded.
    attendu --zips DIR --echantillon FILE --out FILE
        The rule applied to the files, for the parcels of the sample.
    collecte --echantillon FILE --out FILE --budget 200 --reserve 7000
        Real calls, at most one per second. Only the planned pages are asked.
        Stops at the budget, under the quota reserve or at the first 429.
    compare --echantillon FILE --attendu FILE --collecte FILE
            [--explications FILE] --out FILE
        Classes per parcel: egal, ecart_explique, ecart_a_consigner,
        panne_api, non_collecte. Exit 1 while one of the last three remains.

A gap is ``ecart_explique`` only when ``explications.json`` names that exact
parcel, group, field and both values, with a written proof. No threshold ever
absorbs a gap. An API vintage that is unknown or different is stated in the
output; every gap stays classified.

Copies of the ETL file rules (members, encoding, empty -> NULL, integer year)
are deliberate: the application package is not importable here.
tests/test_bdnb_equivalence.py checks the rule against hand-built zips.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API_BASE = "https://api.bdnb.io/v1/bdnb"
PAGE = 10  # X-Max-Items-Per-Call of the open tier
MILLESIME_REQUETES = 1  # the one request that reads the API description

MEMBRE_RELATIONS = "csv/rel_batiment_groupe_parcelle.csv"
MEMBRE_FFO = "csv/batiment_groupe_ffo_bat.csv"
MEMBRE_VERSION = "csv/metadonnees_bdnb_version.csv"
MEMBRE_PARCELLES = "csv/parcelle.csv"
MEMBRE_MAX_OCTETS = 2 * 1024**3

COLONNES_RELATIONS = (
    "batiment_groupe_id",
    "parcelle_id",
    "code_departement_insee",
    "parcelle_principale",
)
COLONNES_FFO = (
    "batiment_groupe_id",
    "annee_construction",
    "mat_mur_txt",
    "mat_toit_txt",
)

COMPTEURS = (
    "relations_lues",
    "relations_ecartees",
    "relations_doublons",
    "ffo_lues",
    "ffo_ecartees",
    "ffo_doublons",
    "ffo_divergents",
)

PARCELLES_FIXES: tuple[str, ...] = ("85128000ZZ0655", "85172000AE0114")
GRAINE_DEFAUT = 20261102

# Target size of each stratum (04.7-19, "Porte d'équivalence API").
CIBLES: dict[str, int] = {"S1": 40, "S2": 40, "S3": 30, "S4": 30, "S5": 10, "S6": 10}

CLASSES = (
    "egal",
    "ecart_explique",
    "ecart_a_consigner",
    "panne_api",
    "non_collecte",
)
CLASSES_BLOQUANTES = ("ecart_a_consigner", "panne_api", "non_collecte")
CHAMPS = ("annee_construction", "mat_mur_txt", "mat_toit_txt")
SELECT = "batiment_groupe_id,annee_construction,mat_mur_txt,mat_toit_txt,l_parcelle_id"
PARCELLE_RE = re.compile(r"^[0-9A-Z]{14}$")
MILLESIME_RE = re.compile(r"\b(20\d{2})-(\d{2})[.-]([a-z])\b")

EXIT_ECART = 1
EXIT_USAGE = 2


class EquivalenceError(Exception):
    """A refusal: the input is not what the rule accepts."""


class BudgetDepasseError(EquivalenceError):
    """The exact cost of the sample is above the request budget."""


# ------------------------------------------------------------------ files --


def _ouvrir_csv(zf: zipfile.ZipFile, nom: str) -> Iterator[list[str]]:
    """Rows of one named member, strict UTF-8 (``utf-8-sig`` only for a BOM)."""
    try:
        info = zf.getinfo(nom)
    except KeyError as exc:
        raise EquivalenceError(f"membre absent: {nom}") from exc
    if info.file_size > MEMBRE_MAX_OCTETS:
        raise EquivalenceError(f"membre trop grand: {nom} ({info.file_size} o)")
    with zf.open(info) as brut:
        tete = brut.peek(3)[:3]
        encodage = "utf-8-sig" if tete == b"\xef\xbb\xbf" else "utf-8"
        texte = io.TextIOWrapper(brut, encoding=encodage, errors="strict", newline="")
        try:
            yield from csv.reader(texte, delimiter=";")
        except UnicodeDecodeError as exc:
            raise EquivalenceError(f"octets non UTF-8 dans {nom}: {exc}") from exc


def _indices(entete: Sequence[str], requises: Iterable[str], nom: str) -> dict:
    index = {col: i for i, col in enumerate(entete)}
    manquantes = [c for c in requises if c not in index]
    if manquantes:
        raise EquivalenceError(f"colonnes absentes de {nom}: {manquantes}")
    return index


def _vide_en_none(valeur: str) -> str | None:
    return valeur if valeur != "" else None


def _annee(valeur: str, nom: str) -> int | None:
    if valeur == "":
        return None
    try:
        return int(valeur)
    except ValueError as exc:
        raise EquivalenceError(f"année qui n'est pas un entier dans {nom}") from exc


def millesime_du_zip(zf: zipfile.ZipFile) -> str:
    """Label of the most recent line of the version history (``2026-02.b``)."""
    lignes = _ouvrir_csv(zf, MEMBRE_VERSION)
    entete = next(lignes, None)
    if entete is None:
        raise EquivalenceError(f"{MEMBRE_VERSION} est vide")
    idx = _indices(entete, ("millesime", "date_publication"), MEMBRE_VERSION)
    meilleur: tuple[datetime, str] | None = None
    for ligne in lignes:
        try:
            date = datetime.strptime(ligne[idx["date_publication"]], "%Y/%m/%d")
        except (ValueError, IndexError) as exc:
            raise EquivalenceError("date_publication illisible") from exc
        etiquette = ligne[idx["millesime"]]
        if meilleur is None or date > meilleur[0]:
            meilleur = (date, etiquette)
    if meilleur is None:
        raise EquivalenceError(f"{MEMBRE_VERSION} sans millésime")
    return meilleur[1]


@dataclass
class Regle:
    """The two members of every zip, merged under the plan's rule."""

    # parcel -> group -> main flag (bool_or over duplicates)
    relations: dict[str, dict[str, bool]]
    # group -> (year, wall, roof)
    ffo: dict[str, tuple[int | None, str | None, str | None]]
    parcelle_departement: dict[str, str]
    millesime: str
    compteurs: dict[str, int]

    def groupes(self, parcelle: str) -> list[dict[str, Any]]:
        """Rows the API should return for one parcel, ordered by group id."""
        lignes = []
        for gid, principale in sorted(self.relations.get(parcelle, {}).items()):
            annee, mur, toit = self.ffo.get(gid, (None, None, None))
            lignes.append(
                {
                    "batiment_groupe_id": gid,
                    "annee_construction": annee,
                    "mat_mur_txt": mur,
                    "mat_toit_txt": toit,
                    "parcelle_principale": principale,
                }
            )
        return lignes


def _code_departement(zip_path: Path) -> str:
    m = re.fullmatch(r"dep(\w+)\.zip", zip_path.name.lower())
    if not m:
        raise EquivalenceError(f"nom de zip inattendu: {zip_path.name}")
    return m.group(1).upper()


def lister_zips(repertoire: Path) -> list[Path]:
    zips = sorted(repertoire.glob("dep*.zip"))
    if not zips:
        raise EquivalenceError(f"aucun dep*.zip dans {repertoire}")
    return zips


def _lire_relations(
    zf: zipfile.ZipFile,
    dep: str,
    relations: dict[str, dict[str, bool]],
    departement: dict[str, str],
    compteurs: Counter[str],
) -> None:
    lignes = _ouvrir_csv(zf, MEMBRE_RELATIONS)
    idx = _indices(next(lignes, []), COLONNES_RELATIONS, MEMBRE_RELATIONS)
    for ligne in lignes:
        compteurs["relations_lues"] += 1
        gid = ligne[idx["batiment_groupe_id"]]
        pid = ligne[idx["parcelle_id"]]
        if not gid or not pid:
            compteurs["relations_ecartees"] += 1
            continue
        principale = ligne[idx["parcelle_principale"]] == "1"
        par_groupe = relations.setdefault(pid, {})
        if gid in par_groupe:
            compteurs["relations_doublons"] += 1
        par_groupe[gid] = par_groupe.get(gid, False) or principale
        departement.setdefault(pid, dep)


def _lire_ffo(
    zf: zipfile.ZipFile,
    ffo: dict[str, tuple[int | None, str | None, str | None]],
    compteurs: Counter[str],
) -> None:
    lignes = _ouvrir_csv(zf, MEMBRE_FFO)
    idx = _indices(next(lignes, []), COLONNES_FFO, MEMBRE_FFO)
    for ligne in lignes:
        compteurs["ffo_lues"] += 1
        gid = ligne[idx["batiment_groupe_id"]]
        if not gid:
            compteurs["ffo_ecartees"] += 1
            continue
        valeur = (
            _annee(ligne[idx["annee_construction"]], MEMBRE_FFO),
            _vide_en_none(ligne[idx["mat_mur_txt"]]),
            _vide_en_none(ligne[idx["mat_toit_txt"]]),
        )
        if gid not in ffo:
            ffo[gid] = valeur
            continue
        compteurs["ffo_doublons"] += 1
        if ffo[gid] != valeur:
            compteurs["ffo_divergents"] += 1


def charger_regle(zips: Sequence[Path]) -> Regle:
    """Read relations and ffo of every zip; apply the de-duplication rules.

    Relations are de-duplicated by (parcel, group) with ``bool_or`` of the main
    flag; ffo by group. Two ffo lines of one group that disagree on year, wall
    or roof are counted in ``ffo_divergents`` (the ETL refuses to publish).
    """
    relations: dict[str, dict[str, bool]] = {}
    ffo: dict[str, tuple[int | None, str | None, str | None]] = {}
    departement: dict[str, str] = {}
    compteurs: Counter[str] = Counter(dict.fromkeys(COMPTEURS, 0))
    millesimes: set[str] = set()
    for chemin in zips:
        with zipfile.ZipFile(chemin) as zf:
            millesimes.add(millesime_du_zip(zf))
            _lire_relations(
                zf, _code_departement(chemin), relations, departement, compteurs
            )
            _lire_ffo(zf, ffo, compteurs)
    if len(millesimes) != 1:
        raise EquivalenceError(f"millésimes différents entre les zips: {millesimes}")
    return Regle(relations, ffo, departement, millesimes.pop(), dict(compteurs))


def parcelles_sans_relation(
    zips: Sequence[Path], regle: Regle
) -> Iterator[tuple[str, str]]:
    """(parcel, departement) of ``csv/parcelle.csv`` rows with no relation."""
    for chemin in zips:
        dep = _code_departement(chemin)
        with zipfile.ZipFile(chemin) as zf:
            lignes = _ouvrir_csv(zf, MEMBRE_PARCELLES)
            idx = _indices(next(lignes, []), ("parcelle_id",), MEMBRE_PARCELLES)
            for ligne in lignes:
                pid = ligne[idx["parcelle_id"]]
                if pid and pid not in regle.relations:
                    yield pid, dep


# ----------------------------------------------------------------- sample --


def cle_de_tri(graine: int, parcelle: str) -> str:
    """``sha256(graine || parcel)`` as hex: the order of every draw."""
    return hashlib.sha256(f"{graine}{parcelle}".encode()).hexdigest()


def pages_prevues(groupes: int) -> int:
    """Pages of ``PAGE`` rows to ask for a parcel of ``groupes`` groups.

    The last planned page is partial, or empty when ``groupes`` is a multiple
    of ``PAGE``: that page is the proof that the list is complete.
    """
    return groupes // PAGE + 1


def cout_total(parcelles: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    pages = sum(pages_prevues(p["groupes"]) for p in parcelles)
    return {
        "pages": pages,
        "millesime_api": MILLESIME_REQUETES,
        "total": pages + MILLESIME_REQUETES,
    }


def _tirer(
    candidats: Iterable[tuple[str, str]],
    n: int,
    graine: int,
    deja: set[str],
) -> list[tuple[str, str]]:
    """First ``n`` candidates in hash order that are not already taken."""
    marge = n + len(deja)
    classes = heapq.nsmallest(
        marge, ((cle_de_tri(graine, pid), pid, dep) for pid, dep in candidats)
    )
    retenus = [(pid, dep) for _, pid, dep in classes if pid not in deja]
    return retenus[:n]


def _predicats(regle: Regle) -> dict[str, Callable[[str], bool]]:
    def annee_absente(pid: str) -> bool:
        return any(
            regle.ffo.get(gid, (None, None, None))[0] is None
            for gid in regle.relations[pid]
        )

    return {
        "S1": lambda p: len(regle.relations[p]) == 1 and not annee_absente(p),
        "S2": lambda p: 2 <= len(regle.relations[p]) <= PAGE,
        "S3": lambda p: not all(regle.relations[p].values()),
        "S4": annee_absente,
        "S5": lambda p: len(regle.relations[p]) > PAGE,
    }


def _tirer_strates(
    regle: Regle,
    sans_relation: Iterable[tuple[str, str]],
    graine: int,
    cibles: Mapping[str, int],
    fixes: Sequence[str],
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Fill the strata in order: (parcels per stratum, departement per parcel)."""
    for pid in fixes:
        if pid not in regle.relations:
            raise EquivalenceError(f"parcelle fixe absente des fichiers: {pid}")
    departements = {pid: regle.parcelle_departement[pid] for pid in fixes}
    ordre: dict[str, list[str]] = {"fixe": list(fixes)}
    predicats = _predicats(regle)
    for strate, predicat in predicats.items():
        candidats = (
            (pid, regle.parcelle_departement[pid])
            for pid in regle.relations
            if predicat(pid)
        )
        choisis = _tirer(candidats, cibles.get(strate, 0), graine, set(departements))
        ordre[strate] = [pid for pid, _ in choisis]
        departements.update(choisis)
    choisis = _tirer(sans_relation, cibles.get("S6", 0), graine, set(departements))
    ordre["S6"] = [pid for pid, _ in choisis]
    departements.update(choisis)
    return ordre, departements


def _lignes_echantillon(
    regle: Regle, ordre: Mapping[str, Sequence[str]], departements: Mapping[str, str]
) -> list[dict[str, Any]]:
    lignes = []
    for strate, pids in ordre.items():
        for pid in pids:
            g = len(regle.relations.get(pid, {}))
            lignes.append(
                {
                    "parcelle": pid,
                    "strate": strate,
                    "departement": departements[pid],
                    "groupes": g,
                    "pages": pages_prevues(g),
                }
            )
    return lignes


def choisir_echantillon(
    regle: Regle,
    sans_relation: Iterable[tuple[str, str]],
    graine: int = GRAINE_DEFAUT,
    cibles: Mapping[str, int] = CIBLES,
    fixes: Sequence[str] = PARCELLES_FIXES,
    budget: int | None = None,
) -> dict[str, Any]:
    """Draw the strata; strata are filled in order and never share a parcel.

    Over ``budget``, S5 then S6 are reduced (tail of the hash order first) and
    every removal is recorded with its reason.
    """
    ordre, departements = _tirer_strates(regle, sans_relation, graine, cibles, fixes)
    parcelles = _lignes_echantillon(regle, ordre, departements)
    reductions: list[dict[str, Any]] = []
    if budget is not None:
        for strate in ("S5", "S6"):
            while cout_total(parcelles)["total"] > budget and ordre[strate]:
                reductions.append(
                    {
                        "strate": strate,
                        "parcelle": ordre[strate].pop(),
                        "motif": f"cout au-dela du budget de {budget} requetes",
                    }
                )
                parcelles = _lignes_echantillon(regle, ordre, departements)
    return {
        "graine": graine,
        "millesime_fichier": regle.millesime,
        "cibles": dict(cibles),
        "retenues": {s: len(p) for s, p in ordre.items()},
        "budget": budget,
        "reductions": reductions,
        "cout": cout_total(parcelles),
        "parcelles": parcelles,
    }


def attendu(regle: Regle, echantillon: Mapping[str, Any]) -> dict[str, Any]:
    """The rule applied to the files, for the parcels of the sample."""
    if echantillon["millesime_fichier"] != regle.millesime:
        raise EquivalenceError(
            f"millésime de l'échantillon {echantillon['millesime_fichier']} "
            f"différent de celui des zips {regle.millesime}"
        )
    parcelles: dict[str, list[dict[str, Any]]] = {}
    for p in echantillon["parcelles"]:
        lignes = regle.groupes(p["parcelle"])
        if len(lignes) != p["groupes"]:
            raise EquivalenceError(
                f"{p['parcelle']}: {len(lignes)} groupes, l'échantillon en annonce "
                f"{p['groupes']}"
            )
        parcelles[p["parcelle"]] = lignes
    return {
        "regle": "toutes les relations de la parcelle (principale ou non), "
        "dédoublonnées, en jointure gauche sur batiment_groupe_ffo_bat",
        "millesime_fichier": regle.millesime,
        "compteurs": regle.compteurs,
        "parcelles": parcelles,
    }


# ------------------------------------------------------------- collection --


@dataclass
class Reponse:
    status: int  # 0 = no HTTP answer (network error, timeout)
    headers: Mapping[str, str]
    body: bytes


Fetch = Callable[[str], Reponse]


def fetch_http(url: str) -> Reponse:
    """One GET, no key, no retry. HTTP errors are answers, not exceptions."""
    requete = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "datacommune-bdnb-equivalence/1",
        },
    )
    try:
        with urllib.request.urlopen(requete, timeout=30) as rep:
            return Reponse(rep.status, dict(rep.headers.items()), rep.read())
    except urllib.error.HTTPError as exc:
        return Reponse(exc.code, dict(exc.headers.items()), exc.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return Reponse(0, {}, b"")


def url_parcelle(parcelle: str, page: int, pages: int) -> str:
    if not PARCELLE_RE.fullmatch(parcelle):
        raise EquivalenceError(f"identifiant de parcelle invalide: {parcelle!r}")
    params = [
        f"parcelle_id=eq.{urllib.parse.quote(parcelle)}",
        f"select={SELECT}",
        f"limit={PAGE}",
    ]
    if pages > 1:
        # Paging needs a stable order; a single page keeps the plain request.
        params.append("order=batiment_groupe_id.asc")
        if page > 0:
            params.append(f"offset={page * PAGE}")
    return f"{API_BASE}/donnees/batiment_groupe_complet/parcelle?" + "&".join(params)


def _entete(rep: Reponse, nom: str) -> str | None:
    for cle, valeur in rep.headers.items():
        if cle.lower() == nom.lower():
            return valeur
    return None


def _quota_restant(rep: Reponse) -> int | None:
    valeur = _entete(rep, "X-Quota-Remaining")
    try:
        return int(valeur) if valeur is not None else None
    except ValueError:
        return None


def lire_millesime_api(fetch: Fetch) -> tuple[Reponse, dict[str, Any]]:
    """Read the API vintage from its published description, if it carries one."""
    rep = fetch(f"{API_BASE}/")
    info: dict[str, Any] = {"millesime": None, "source": None}
    if rep.status == 200:
        try:
            doc = json.loads(rep.body)
            description = doc.get("info", {})
            texte = (
                f"{description.get('version', '')} {description.get('description', '')}"
            )
        except (ValueError, AttributeError):
            texte = ""
        trouve = MILLESIME_RE.search(texte)
        if trouve:
            info["millesime"] = f"{trouve[1]}-{trouve[2]}.{trouve[3]}"
            info["source"] = "description publiée de l'API"
    return rep, info


class _Arret(Exception):  # noqa: N818 - control flow, not an error
    def __init__(self, motif: str) -> None:
        super().__init__(motif)
        self.motif = motif


class _Collecteur:
    """Counts every request; raises ``_Arret`` at the budget or the first 429."""

    def __init__(
        self,
        fetch: Fetch,
        budget: int,
        pause: float,
        sleep: Callable[[float], None],
        horloge: Callable[[], float],
    ) -> None:
        self.fetch = fetch
        self.budget = budget
        self.pause = pause
        self.sleep = sleep
        self.horloge = horloge
        self.requetes = 0
        self.dernier: float | None = None
        self.quotas: list[int] = []
        self.pannes = 0

    def appeler(self, url: str) -> Reponse:
        if self.requetes >= self.budget:
            raise _Arret("budget")
        if self.dernier is not None:
            attente = self.pause - (self.horloge() - self.dernier)
            if attente > 0:
                self.sleep(attente)
        self.dernier = self.horloge()
        self.requetes += 1
        rep = self.fetch(url)
        quota = _quota_restant(rep)
        if quota is not None:
            self.quotas.append(quota)
        if rep.status == 429:
            raise _Arret("429")
        return rep

    def sous_la_reserve(self, reserve: int) -> bool:
        return bool(self.quotas) and self.quotas[-1] < reserve

    def page(self, url: str) -> tuple[Reponse, list[dict[str, Any]] | str]:
        """One page with a single counted retry; rows, or the reason of a failure."""
        rep = self.appeler(url)
        if rep.status == 0 or rep.status >= 500:
            rep = self.appeler(url)
        if rep.status != 200:
            return rep, f"HTTP {rep.status}"
        try:
            rows = json.loads(rep.body)
        except ValueError:
            return rep, "réponse illisible"
        if not isinstance(rows, list) or not all(
            isinstance(r, dict) and r.get("batiment_groupe_id") for r in rows
        ):
            return rep, "réponse illisible"
        return rep, rows

    def parcelle(self, p: Mapping[str, Any], reserve: int, res: dict[str, Any]) -> None:
        pages = p["pages"]
        lignes: list[dict[str, Any]] = []
        for page in range(pages):
            if page > 0 and self.sous_la_reserve(reserve):
                raise _Arret("reserve")  # never leave a parcel half-read
            _, rows = self.page(url_parcelle(p["parcelle"], page, pages))
            res["pages_demandees"] += 1
            if isinstance(rows, str):
                res.update(statut="panne_api", motif=rows)
                self.pannes += 1
                return
            self.pannes = 0
            lignes.extend(rows)
            res["groupes"] = lignes
            if len(rows) < PAGE:  # partial or empty page: the list is complete
                res.update(statut="collecte", complet=True)
                return
        res["motif"] = "page terminale pleine: l'API a plus de groupes"


def collecter(
    echantillon: Mapping[str, Any],
    fetch: Fetch,
    *,
    budget: int,
    reserve: int,
    pause: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
    horloge: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Ask only the planned pages; stop at budget, reserve or first 429."""
    cout = cout_total(echantillon["parcelles"])
    if cout["total"] > budget:
        raise BudgetDepasseError(
            f"coût exact {cout['total']} requêtes ({cout['pages']} pages + "
            f"{cout['millesime_api']} millésime) au-delà du budget {budget}: "
            "réduire S5 puis S6 avec le mode echantillon --budget"
        )
    col = _Collecteur(fetch, budget, pause, sleep, horloge)
    arret: str | None = None
    millesime: dict[str, Any] = {"millesime": None, "source": None}
    try:
        _, millesime = lire_millesime_api(col.appeler)
    except _Arret as exc:
        arret = exc.motif
    if arret is None and col.sous_la_reserve(reserve):
        arret = "reserve"
    resultats: list[dict[str, Any]] = []
    for p in echantillon["parcelles"]:
        res: dict[str, Any] = {
            "parcelle": p["parcelle"],
            "strate": p["strate"],
            "statut": "non_collecte",
            "motif": f"arret: {arret}" if arret else None,
            "pages_prevues": p["pages"],
            "pages_demandees": 0,
            "groupes": [],
        }
        resultats.append(res)
        if arret is not None:
            continue
        try:
            col.parcelle(p, reserve, res)
        except _Arret as exc:
            arret = exc.motif
            res.update(statut="non_collecte", motif=f"arret: {arret}")
        if arret is None and col.sous_la_reserve(reserve):
            arret = "reserve"
        if arret is None and col.pannes >= 3:
            arret = "pannes_consecutives"
    if arret is None and col.sous_la_reserve(reserve):
        arret = "reserve"
    return {
        "budget": budget,
        "reserve": reserve,
        "cout_calcule": cout,
        "requetes_depensees": col.requetes,
        "quota_premiere_reponse": col.quotas[0] if col.quotas else None,
        "quota_derniere_reponse": col.quotas[-1] if col.quotas else None,
        "arret": arret,
        "millesime_api": millesime["millesime"],
        "millesime_api_source": millesime["source"],
        "parcelles": resultats,
    }


# ---------------------------------------------------------------- compare --


def _ecarts_parcelle(
    parcelle: str, attendus: Sequence[Mapping[str, Any]], api: Sequence[Mapping]
) -> list[dict[str, Any]]:
    par_fichier = {g["batiment_groupe_id"]: g for g in attendus}
    compte = Counter(r["batiment_groupe_id"] for r in api)
    premier: dict[str, Mapping[str, Any]] = {}
    for r in api:
        premier.setdefault(r["batiment_groupe_id"], r)
    ecarts: list[dict[str, Any]] = []

    def ecart(groupe: str, champ: str, fichier: Any, valeur_api: Any) -> None:
        ecarts.append(
            {
                "parcelle": parcelle,
                "groupe": groupe,
                "champ": champ,
                "fichier": fichier,
                "api": valeur_api,
            }
        )

    for gid in sorted(set(par_fichier) | set(premier)):
        if gid not in premier:
            ecart(gid, "presence", "present", "absent")
            continue
        if gid not in par_fichier:
            ecart(gid, "presence", "absent", "present")
            continue
        if compte[gid] > 1:
            ecart(gid, "occurrences", 1, compte[gid])
        for champ in CHAMPS:
            if par_fichier[gid][champ] != premier[gid].get(champ):
                ecart(gid, champ, par_fichier[gid][champ], premier[gid].get(champ))
    return ecarts


def _explication(
    ecart: Mapping[str, Any], explications: Sequence[Mapping[str, Any]]
) -> int | None:
    for i, e in enumerate(explications):
        if (
            all(e.get(k) == ecart[k] for k in ("parcelle", "groupe", "champ"))
            and e.get("fichier") == ecart["fichier"]
            and e.get("api") == ecart["api"]
            and isinstance(e.get("preuve"), str)
            and e["preuve"].strip()
        ):
            return i
    return None


def _classer_ecarts(
    ecarts: list[dict[str, Any]],
    strate: str,
    explications: Sequence[Mapping[str, Any]],
    utilisees: set[int],
) -> str:
    """Class each gap in place; the class of the parcel is its worst gap."""
    for e in ecarts:
        e["strate"] = strate
        i = _explication(e, explications)
        if i is None:
            e["classe"] = "ecart_a_consigner"
        else:
            e.update(classe="ecart_explique", preuve=explications[i]["preuve"])
            utilisees.add(i)
    classes = {e["classe"] for e in ecarts}
    if "ecart_a_consigner" in classes:
        return "ecart_a_consigner"
    return "ecart_explique" if classes else "egal"


def _note_millesime(fichier: str, api: str | None) -> dict[str, Any]:
    if api is None:
        egal, note = None, "millésime de l'API inconnu: chaque écart reste classé"
    elif api == fichier:
        egal, note = True, "même millésime"
    else:
        egal, note = False, "millésime de l'API différent: chaque écart reste classé"
    return {"fichier": fichier, "api": api, "egal": egal, "note": note}


def comparer(
    echantillon: Mapping[str, Any],
    attendu_doc: Mapping[str, Any],
    collecte: Mapping[str, Any],
    explications: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Classify every parcel of the sample; never absorb a gap."""
    recueillies = {r["parcelle"]: r for r in collecte["parcelles"]}
    utilisees: set[int] = set()
    parcelles: list[dict[str, Any]] = []
    tous_ecarts: list[dict[str, Any]] = []
    for p in echantillon["parcelles"]:
        pid = p["parcelle"]
        reco = recueillies.get(pid)
        entree = {"parcelle": pid, "strate": p["strate"], "classe": "", "motif": None}
        parcelles.append(entree)
        if reco is None:
            entree.update(classe="non_collecte", motif="absente de la collecte")
        elif reco["statut"] in ("panne_api", "non_collecte"):
            entree.update(classe=reco["statut"], motif=reco.get("motif"))
        else:
            if pid not in attendu_doc["parcelles"]:
                raise EquivalenceError(f"{pid} absente de la sortie attendu")
            ecarts = _ecarts_parcelle(
                pid, attendu_doc["parcelles"][pid], reco["groupes"]
            )
            entree["classe"] = _classer_ecarts(
                ecarts, p["strate"], explications, utilisees
            )
            tous_ecarts.extend(ecarts)
    par_classe = Counter(e["classe"] for e in parcelles)
    par_strate: dict[str, Counter[str]] = {}
    for e in parcelles:
        par_strate.setdefault(e["strate"], Counter())[e["classe"]] += 1
    bloquantes = sum(par_classe.get(c, 0) for c in CLASSES_BLOQUANTES)
    return {
        "millesime": _note_millesime(
            attendu_doc["millesime_fichier"], collecte.get("millesime_api")
        ),
        "classes": {c: par_classe.get(c, 0) for c in CLASSES},
        "par_strate": {
            s: {c: n.get(c, 0) for c in CLASSES} for s, n in sorted(par_strate.items())
        },
        "ecarts": tous_ecarts,
        "explications_inutilisees": [
            i for i in range(len(explications)) if i not in utilisees
        ],
        "parcelles": parcelles,
        "bloquantes": bloquantes,
        "exit": EXIT_ECART if bloquantes else 0,
    }


# -------------------------------------------------------------------- cli --


def _ecrire(doc: Mapping[str, Any], sortie: Path | None) -> None:
    if sortie is not None:
        sortie.parent.mkdir(parents=True, exist_ok=True)
        sortie.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )


def _lire(chemin: Path) -> Any:
    return json.loads(chemin.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sous = p.add_subparsers(dest="mode", required=True)
    e = sous.add_parser("echantillon")
    e.add_argument("--zips", type=Path, required=True)
    e.add_argument("--graine", type=int, default=GRAINE_DEFAUT)
    e.add_argument("--budget", type=int, default=200)
    e.add_argument("--out", type=Path)
    a = sous.add_parser("attendu")
    a.add_argument("--zips", type=Path, required=True)
    a.add_argument("--echantillon", type=Path, required=True)
    a.add_argument("--out", type=Path)
    c = sous.add_parser("collecte")
    c.add_argument("--echantillon", type=Path, required=True)
    c.add_argument("--budget", type=int, default=200)
    c.add_argument("--reserve", type=int, default=7000)
    c.add_argument("--pause", type=float, default=1.0)
    c.add_argument("--out", type=Path)
    k = sous.add_parser("compare")
    k.add_argument("--echantillon", type=Path, required=True)
    k.add_argument("--attendu", type=Path, required=True)
    k.add_argument("--collecte", type=Path, required=True)
    k.add_argument("--explications", type=Path)
    k.add_argument("--out", type=Path)
    return p


def main(
    argv: Sequence[str] | None = None,
    *,
    fetch: Fetch | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "echantillon":
            zips = lister_zips(args.zips)
            regle = charger_regle(zips)
            doc = choisir_echantillon(
                regle,
                parcelles_sans_relation(zips, regle),
                args.graine,
                budget=args.budget,
            )
            resume = {k: doc[k] for k in ("retenues", "cout", "reductions")}
        elif args.mode == "attendu":
            regle = charger_regle(lister_zips(args.zips))
            doc = attendu(regle, _lire(args.echantillon))
            resume = {"compteurs": doc["compteurs"], "parcelles": len(doc["parcelles"])}
        elif args.mode == "collecte":
            echantillon = _lire(args.echantillon)
            debut = datetime.now(UTC).isoformat(timespec="seconds")
            doc = collecter(
                echantillon,
                fetch or fetch_http,
                budget=args.budget,
                reserve=args.reserve,
                pause=args.pause,
                sleep=sleep,
            )
            doc["debut"] = debut
            doc["fin"] = datetime.now(UTC).isoformat(timespec="seconds")
            statuts = Counter(r["statut"] for r in doc["parcelles"])
            resume = {
                k: doc[k]
                for k in (
                    "requetes_depensees",
                    "cout_calcule",
                    "quota_premiere_reponse",
                    "quota_derniere_reponse",
                    "arret",
                    "millesime_api",
                )
            }
            resume["statuts"] = dict(statuts)
        else:
            explications = _lire(args.explications) if args.explications else []
            doc = comparer(
                _lire(args.echantillon),
                _lire(args.attendu),
                _lire(args.collecte),
                explications,
            )
            resume = {k: doc[k] for k in ("millesime", "classes", "par_strate")}
            resume["ecarts"] = len(doc["ecarts"])
    except EquivalenceError as exc:
        print(json.dumps({"erreur": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return EXIT_USAGE
    _ecrire(doc, args.out)
    print(json.dumps(resume, ensure_ascii=False, indent=2))
    return int(doc.get("exit", 0)) if args.mode == "compare" else 0


if __name__ == "__main__":
    sys.exit(main())
