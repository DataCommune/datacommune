#!/usr/bin/env python3
"""National size estimate of a table from measured densities (plan 04.1-22).

Inputs, all versioned measurements:

- one or more size measurements (``scripts/measure_db_sizes.py`` output:
  ``mesures/apres/sizes-44-85.json`` after plan 20, ``mesures/temoin/
  sizes-temoin.json`` after plan 21). A partitioned table gives one sample
  per departement partition; a plain table gives one sample per file (its
  departements together);
- the producer counts (``app/scope/producer_counts.py``): national rows of
  the table in its stored unit, summed over the hostable departements.

Per table: heap (table + TOAST) and index bytes per row of each sample;
``centrale`` = row-weighted mean density x national rows (DÉDUIT);
``haute`` = maximum sample density x national rows, plus a documented
version gap (DÉDUIT, never below ``centrale``). Every component is written
apart with its basis. ``capacite_utilisable`` gives the usable capacity of a
disk: ext4 keeps 5 % reserved by default (1 % after ``tune2fs -m 1``), ZFS
is held under 80 % of the pool.

Workstation only (reads files, writes JSON to ``--out``)::

    python api/backend/scripts/estimation_nationale.py \\
        --tailles mesures/apres/sizes-44-85.json --departements 44,85 \\
        --tailles mesures/temoin/sizes-temoin.json --departements 75,67,971 \\
        --table eligibilite_fixe --out mesures/dimensionnement/tables.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Table -> (producer dataset, unit) of its stored rows (app.scope.producer_counts).
TABLES_PRODUCTEUR: dict[str, tuple[str, str]] = {
    "eligibilite_fixe": ("connectivite", "lignes_stockees"),
    "adresses": ("ban", "lignes_stockees"),
    "parcelles": ("cadastre", "lignes_stockees"),
    "diagnostics_dpe": ("dpe", "lignes_stockees"),
    "mutations": ("dvf", "lignes_stockees"),
    "rnb_liens": ("rnb_liens", "lignes_stockees"),
    "bdnb_batiment_parcelle": ("bdnb_batiment_parcelle", "lignes_stockees"),
}
GO = 1_000_000_000


@dataclass(frozen=True)
class Echantillon:
    """One measured density sample: a departement partition or a whole table."""

    source: str
    departements: tuple[str, ...]
    lignes: int
    tas_octets: int
    index_octets: int

    @property
    def tas_par_ligne(self) -> float:
        return self.tas_octets / self.lignes

    @property
    def index_par_ligne(self) -> float:
        return self.index_octets / self.lignes


def echantillons(
    tailles: Mapping[str, Any],
    table: str,
    source: str,
    departements: Sequence[str],
    min_lignes: int = 1,
) -> list[Echantillon]:
    """Samples of *table* in one size measurement.

    Partitions under *min_lignes* rows are skipped: a fresh witness base holds
    2 or 3 seed rows per partition, whose bytes per row mean nothing (04.1-22).
    """
    enfants = [
        r
        for r in tailles["relations"]
        if r.get("parent") == table
        and r.get("departement")
        and (r.get("exact_count") or 0) >= min_lignes
    ]
    if enfants:
        return [
            Echantillon(
                source=source,
                departements=(r["departement"],),
                lignes=int(r["exact_count"]),
                tas_octets=int(r["pg_relation_size"]) + int(r["toast_bytes"]),
                index_octets=int(r["pg_indexes_size"]),
            )
            for r in enfants
        ]
    entree = tailles["tables"].get(table)
    if not entree or (entree.get("exact_count") or 0) < min_lignes:
        return []
    return [
        Echantillon(
            source=source,
            departements=tuple(departements),
            lignes=int(entree["exact_count"]),
            tas_octets=int(entree["table_bytes"]) + int(entree["toast_bytes"]),
            index_octets=int(entree["indexes_bytes"]),
        )
    ]


def estimer(
    table: str,
    lignes_nationales: int,
    mesures: Iterable[Echantillon],
    ecart_version: float = 0.0,
    lignes_etiquette: str = "ÉTABLI",
    lignes_base: str = "comptes producteurs, unité stockée, départements hébergeables",
) -> dict[str, Any]:
    """Central and high estimates of *table*, each component apart."""
    mesures = list(mesures)
    if not mesures:
        raise ValueError(f"no measured sample for {table}")
    lignes = sum(e.lignes for e in mesures)
    tas_moyen = sum(e.tas_octets for e in mesures) / lignes
    index_moyen = sum(e.index_octets for e in mesures) / lignes
    tas_max = max(e.tas_par_ligne for e in mesures)
    index_max = max(e.index_par_ligne for e in mesures)
    composantes = [
        {
            "nom": "lignes_nationales",
            "valeur": lignes_nationales,
            "etiquette": lignes_etiquette,
            "base": lignes_base,
        },
        {
            "nom": "tas_centrale",
            "valeur": round(tas_moyen * lignes_nationales),
            "etiquette": "DÉDUIT",
            "base": (
                f"{tas_moyen:.1f} o/ligne, moyenne pondérée de "
                f"{len(mesures)} échantillon(s)"
            ),
        },
        {
            "nom": "index_centrale",
            "valeur": round(index_moyen * lignes_nationales),
            "etiquette": "DÉDUIT",
            "base": f"{index_moyen:.1f} o/ligne, moyenne pondérée",
        },
        {
            "nom": "tas_haute",
            "valeur": round(tas_max * lignes_nationales),
            "etiquette": "DÉDUIT",
            "base": f"{tas_max:.1f} o/ligne, densité maximale mesurée",
        },
        {
            "nom": "index_haute",
            "valeur": round(index_max * lignes_nationales),
            "etiquette": "DÉDUIT",
            "base": f"{index_max:.1f} o/ligne, densité maximale mesurée",
        },
        {
            "nom": "ecart_version",
            "valeur": ecart_version,
            "etiquette": "HYPOTHÈSE",
            "base": "part ajoutée au scénario haut pour l'écart entre éditions",
        },
    ]
    centrale = round((tas_moyen + index_moyen) * lignes_nationales)
    haute = round((tas_max + index_max) * lignes_nationales * (1 + ecart_version))
    return {
        "table": table,
        "centrale_octets": centrale,
        "haute_octets": max(haute, centrale),
        "composantes": composantes,
        "echantillons": [asdict(e) for e in mesures],
    }


def capacite_utilisable(disque_octets: int, systeme: str) -> int:
    """Bytes a database may fill: ext4 reserve, or ZFS held under 80 %."""
    parts = {"ext4": 0.95, "ext4-m1": 0.99, "zfs": 0.80}
    if systeme not in parts:
        raise ValueError(f"unknown file system {systeme}")
    return int(disque_octets * parts[systeme])


def reste(
    tailles: Mapping[str, Any], exclues: Iterable[str], facteur: float, base: str
) -> dict[str, Any]:
    """Tables not estimated one by one: measured bytes x one scale factor."""
    exclues = set(exclues)
    octets = {
        nom: int(t.get("total_bytes") or 0)
        for nom, t in tailles["tables"].items()
        if nom not in exclues and not nom.endswith("_old") and "staging" not in nom
    }
    mesure = sum(octets.values())
    return {
        "table": "reste",
        "mesure_octets": mesure,
        "facteur": facteur,
        "centrale_octets": round(mesure * facteur),
        "haute_octets": round(mesure * facteur * 1.5),
        "composantes": [
            {"nom": "mesure", "valeur": mesure, "etiquette": "ÉTABLI", "base": base},
            {
                "nom": "facteur",
                "valeur": facteur,
                "etiquette": "HYPOTHÈSE FRAGILE",
                "base": "facteur national unique ; haute = +50 %",
            },
        ],
        "tables_comptees": len(octets),
    }


def pic_premier_import(
    mesure: Mapping[str, Any], lignes_mesurees: int, lignes_nationales: int
) -> dict[str, Any]:
    """National first-import peak of one dataset, scaled by its rows (DÉDUIT).

    Disk above the base, WAL generated and duration scale linearly with the
    rows written; ``anon`` is given measured and scaled (two readings).
    """
    f = lignes_nationales / lignes_mesurees
    return {
        "facteur": f,
        "disque_au_dessus_base_octets": round(mesure["disque_octets"] * f),
        "wal_genere_octets": round(mesure["wal_genere_octets"] * f),
        "duree_s": round(mesure["duree_s"] * f),
        "anon_mesure_octets": mesure["anon_octets"],
        "anon_lineaire_octets": round(mesure["anon_octets"] * f),
    }


def enveloppe_sequentielle(
    ordre: Sequence[str],
    finales: Mapping[str, int],
    pics: Mapping[str, int],
    socle: int = 0,
) -> dict[str, Any]:
    """Peak of a sequential first import: what is loaded, plus the transient.

    Datasets are imported one at a time in *ordre*; while one runs, the disk
    holds *socle*, every dataset already published, and the running one's
    transient peak above the base (which includes its own final table).
    """
    charge = socle
    etapes = []
    for dataset in ordre:
        pic = charge + pics[dataset]
        etapes.append({"dataset": dataset, "avant": charge, "pic": pic})
        charge += finales[dataset]
    maximum = max(etapes, key=lambda e: e["pic"])
    return {
        "etapes": etapes,
        "pic_octets": maximum["pic"],
        "pic_pendant": maximum["dataset"],
        "fin": charge,
    }


def rapport_capacite(
    disque_go: int, systeme: str, besoins: Mapping[str, int]
) -> dict[str, Any]:
    """Share of the usable capacity each need fills, and the margin left."""
    utilisable = capacite_utilisable(disque_go * GO, systeme)
    return {
        "disque_go": disque_go,
        "systeme": systeme,
        "capacite_utilisable_octets": utilisable,
        "besoins": {
            nom: {
                "octets": octets,
                "part_utilisable": round(octets / utilisable, 4),
                "marge_octets": utilisable - octets,
            }
            for nom, octets in besoins.items()
        },
    }


def archive_nationale(
    editions: Mapping[str, int], retention: int = 2
) -> dict[str, Any]:
    """Archive kept under retention: *retention* editions of each dataset.

    Objects are content-addressed: an unchanged file is stored once, so
    ``retention`` x edition is the high bound (every file changed).
    """
    par_dataset = {
        d: {"edition_octets": o, "retenu_octets": o * retention}
        for d, o in editions.items()
    }
    return {
        "retention_editions": retention,
        "datasets": par_dataset,
        "une_edition_octets": sum(editions.values()),
        "retenue_octets": sum(o * retention for o in editions.values()),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _cle_valeur(paires: Sequence[str]) -> dict[str, str]:
    return dict(p.split("=", 1) for p in paires)


def _ecrire(rapport: Mapping[str, Any], sortie: str) -> None:
    texte = json.dumps(rapport, ensure_ascii=False, indent=2, sort_keys=True)
    if sortie == "-":
        print(texte)
    else:
        Path(sortie).write_text(texte + "\n", encoding="utf-8")


def dimensionner(config_path: Path, dossier: Path) -> None:
    """Peaks, capacity and archive from one versioned input file.

    The input names every measurement it uses (import records, tables.json)
    and each is fingerprinted; outputs ``pics.json``,
    ``capacite-<disque>-<fs>.json`` and ``archive.json`` in *dossier*.
    """
    base = config_path.parent
    config = json.loads(config_path.read_text(encoding="utf-8"))
    tables = json.loads((base / config["tables"]).read_text(encoding="utf-8"))
    entrees = {
        config_path.name: _sha(config_path),
        config["tables"]: _sha(base / config["tables"]),
    }
    finales: dict[str, dict[str, int]] = {"centrale": {}, "haute": {}}
    for t in tables["tables"]:
        dataset = config["table_dataset"].get(t["table"], "autres")
        for scenario in finales:
            octets = t[f"{scenario}_octets"]
            finales[scenario][dataset] = finales[scenario].get(dataset, 0) + octets
    pics: dict[str, Any] = {}
    for dataset, m in config["imports"].items():
        chemin = base / m["releve"]
        entrees[m["releve"]] = _sha(chemin)
        releve = json.loads(chemin.read_text(encoding="utf-8"))
        mesure = {
            "disque_octets": releve["disk_peak_bytes"] - releve["disk_baseline_bytes"],
            "wal_genere_octets": releve["wal_generated_bytes"],
            "anon_octets": releve["anon_peak_bytes"],
            "duree_s": m["duree_s"],
        }
        pics[dataset] = {
            "releve": m["releve"],
            "mesure": mesure,
            "lignes_mesurees": m["lignes_mesurees"],
            "lignes_nationales": m["lignes_nationales"],
            **pic_premier_import(mesure, m["lignes_mesurees"], m["lignes_nationales"]),
        }
    socle = config["socle_octets"]
    transitoires = {d: p["disque_au_dessus_base_octets"] for d, p in pics.items()}
    enveloppes = {
        scenario: enveloppe_sequentielle(
            config["ordre"],
            valeurs,
            transitoires,
            socle + valeurs.get("autres", 0),
        )
        for scenario, valeurs in finales.items()
    }
    calendrier = {
        "ordre": config["ordre"],
        "durees_s": {d: pics[d]["duree_s"] for d in config["ordre"]},
        "total_sequentiel_s": sum(pics[d]["duree_s"] for d in config["ordre"]),
        "base": "durée mesurée du premier import x facteur de lignes (DÉDUIT)",
    }
    wal = {
        d: {
            "wal_genere_national_octets": p["wal_genere_octets"],
            "base": "WAL généré mesuré (pg_wal_lsn_diff) x facteur de lignes",
        }
        for d, p in pics.items()
    }
    _ecrire(
        {
            "pics": pics,
            "enveloppes": enveloppes,
            "wal": wal,
            "calendrier": calendrier,
            "socle_octets": socle,
            "finales_par_dataset": finales,
            "entrees_sha256": entrees,
        },
        str(dossier / "pics.json"),
    )
    archive = archive_nationale(config["archive"]["editions"])
    besoins = {
        "regime_centrale": sum(finales["centrale"].values()) + socle,
        "regime_haute": sum(finales["haute"].values()) + socle,
        "premier_import_centrale": enveloppes["centrale"]["pic_octets"],
        "premier_import_haute": enveloppes["haute"]["pic_octets"],
        "premier_import_haute_avec_archive_locale": (
            enveloppes["haute"]["pic_octets"] + archive["retenue_octets"]
        ),
    }
    for disque in config["disques_go"]:
        for systeme in config["systemes"]:
            _ecrire(
                {
                    **rapport_capacite(disque, systeme, besoins),
                    "entrees_sha256": entrees,
                },
                str(dossier / f"capacite-{disque}-{systeme}.json"),
            )
    _ecrire(
        {
            **archive,
            "base": config["archive"]["base"],
            "seconde_copie": config["archive"]["seconde_copie"],
            "entrees_sha256": entrees,
        },
        str(dossier / "archive.json"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "dimensionner":
        sous = argparse.ArgumentParser(prog="estimation_nationale dimensionner")
        sous.add_argument("--entrees", required=True)
        sous.add_argument("--dossier", required=True)
        args = sous.parse_args(argv[1:])
        dimensionner(Path(args.entrees), Path(args.dossier))
        return 0

    from app.scope.model import DEPARTEMENTS_HEBERGEABLES
    from app.scope.producer_counts import PRODUCER_COUNTS

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tailles", action="append", required=True)
    parser.add_argument("--departements", action="append", default=[])
    parser.add_argument("--table", action="append", required=True)
    parser.add_argument("--ecart-version", type=float, default=0.0)
    parser.add_argument("--min-lignes", type=int, default=1)
    parser.add_argument("--lignes", action="append", default=[], help="TABLE=N")
    parser.add_argument(
        "--lignes-base", action="append", default=[], help="TABLE=source de N"
    )
    parser.add_argument(
        "--lignes-etiquette",
        action="append",
        default=[],
        help="TABLE=étiquette de N (défaut HYPOTHÈSE)",
    )
    parser.add_argument("--reste", help="tailles dont compter les autres tables")
    parser.add_argument("--reste-facteur", type=float, default=0.0)
    parser.add_argument("--out", default="-")
    args = parser.parse_args(argv)
    fichiers = [Path(p) for p in args.tailles]
    departements = args.departements or [""] * len(fichiers)
    lignes = {k: int(v) for k, v in _cle_valeur(args.lignes).items()}
    lignes_base = _cle_valeur(args.lignes_base)
    lignes_etiquette = _cle_valeur(args.lignes_etiquette)
    resultats = []
    for table in args.table:
        if table in lignes:
            nationales = lignes[table]
            base = lignes_base.get(table, "")
            # A count given by hand is a hypothesis unless its basis says
            # otherwise (04.1-REVIEW WR-02).
            etiquette = lignes_etiquette.get(table, "HYPOTHÈSE")
        else:
            dataset, unite = TABLES_PRODUCTEUR[table]
            comptes = PRODUCER_COUNTS[dataset][unite]
            nationales = sum(
                v for k, v in comptes.items() if k in DEPARTEMENTS_HEBERGEABLES
            )
            base = "comptes producteurs, unité stockée, départements hébergeables"
            etiquette = "ÉTABLI"
        mesures = [
            e
            for chemin, deps in zip(fichiers, departements, strict=True)
            for e in echantillons(
                json.loads(chemin.read_text(encoding="utf-8")),
                table,
                chemin.name,
                [d for d in deps.split(",") if d],
                args.min_lignes,
            )
        ]
        resultats.append(
            estimer(table, nationales, mesures, args.ecart_version, etiquette, base)
        )
    if args.reste:
        chemin = Path(args.reste)
        fichiers.append(chemin)
        resultats.append(
            reste(
                json.loads(chemin.read_text(encoding="utf-8")),
                args.table,
                args.reste_facteur,
                f"autres tables de {chemin.name}",
            )
        )
    rapport = {
        "tables": resultats,
        "centrale_octets": sum(r["centrale_octets"] for r in resultats),
        "haute_octets": sum(r["haute_octets"] for r in resultats),
        "entrees_sha256": {p.name: _sha(p) for p in fichiers},
    }
    _ecrire(rapport, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
