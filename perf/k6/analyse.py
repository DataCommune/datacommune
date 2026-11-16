#!/usr/bin/env python3
"""Per-plateau reading of a k6 CSV output of parcours-agent.js (PROFILE=rampe).

    python perf/k6/analyse.py <run.csv.gz> [--p95-ms 1000] [--erreurs-max 0.01] [--palier-s 120]

Groups the samples by the ``palier`` tag the script puts on every sample of an iteration (the
target rate, in parcours/s, of the plateau the iteration started in; ramps are left out) and
prints, per plateau: achieved parcours/s and requests/s, p50/p95/p99 of http_req_duration
(nearest rank: every percentile is an observed value), failure rate (http_req_failed),
refusals by status, dropped iterations, then p95 per route. Rates are counted over the nominal
plateau (--palier-s, PALIER_S of the run) from its first sample, cut at the end of the test: the
samples of a plateau's slow iterations run past it and must not stretch the denominator.

A plateau is SUSTAINED when p95 <= --p95-ms, failures <= --erreurs-max, no iteration was
dropped and the achieved rate is at least 95 % of the target. The sustained rate is the highest
sustained plateau below the first one that is not; that first one is the break. Standard
library only; nothing is written.

With FICHE=fond, only the samples of the rampe scenario make the verdict and the achieved rate;
the background fiches (scenario fiche_fond) get their own table per plateau.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import math
import sys
from collections import Counter, defaultdict
from urllib.parse import parse_qsl

ROUTES = (
    "recherche",
    "fiche_adresse",
    "commune",
    "dvf_ventes",
    "dvf_ventes_declarees",
    "dpe_adresse",
    "recherche_fond",
)


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile: the smallest observed value covering p %."""
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[rank - 1]


def seconds(raw: str) -> float:
    value = float(raw)
    return value / 1000 if value > 1e11 else value  # unix_milli or unix seconds


def read(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    plateaux: dict[str, dict] = defaultdict(
        lambda: {
            "durations": [],
            "by_route": defaultdict(list),
            "failed": 0,
            "reqs": 0,
            "iterations": 0,
            "status": Counter(),
            "fond_fiche": [],
            "fond_failed": 0,
            "fond_reqs": 0,
            "fond_status": Counter(),
            "first": math.inf,
        }
    )
    dropped: list[float] = []
    end = -math.inf
    with opener(path, "rt", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            metric = row["metric_name"]
            end = max(end, seconds(row["timestamp"]))
            if metric == "dropped_iterations":
                dropped.append(seconds(row["timestamp"]))
                continue
            tags = dict(parse_qsl(row.get("extra_tags") or ""))
            palier = tags.get("palier", "")
            try:
                float(palier)
            except ValueError:
                continue  # setup, montee, fin, smoke, controle
            plateau = plateaux[palier]
            t = seconds(row["timestamp"])
            plateau["first"] = min(plateau["first"], t)
            verdict = (row.get("scenario") or "rampe") == "rampe"
            route = tags.get("route", "?")
            if metric == "http_req_duration":
                value = float(row["metric_value"])
                plateau["by_route"][route].append(value)
                status = row.get("status") or "0"
                if verdict:
                    plateau["durations"].append(value)
                    plateau["status"][status] += 1
                else:
                    plateau["fond_status"][status] += 1
                    if route == "fiche_adresse":
                        plateau["fond_fiche"].append(value)
            elif metric == "http_req_failed":
                failed = float(row["metric_value"]) > 0
                if verdict:
                    plateau["reqs"] += 1
                    plateau["failed"] += failed
                else:
                    plateau["fond_reqs"] += 1
                    plateau["fond_failed"] += failed
            elif metric == "iterations" and verdict:
                plateau["iterations"] += 1
    return plateaux, dropped, end


def refused(statuses: Counter) -> dict[str, int]:
    return {s: n for s, n in statuses.items() if s in ("0", "429") or s.startswith("5")}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv")
    parser.add_argument("--p95-ms", type=float, default=1000.0)
    parser.add_argument("--erreurs-max", type=float, default=0.01)
    parser.add_argument("--palier-s", type=float, default=120.0)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(
        encoding="utf-8"
    )  # Markdown with accents, Windows console included

    plateaux, dropped, end = read(args.csv)

    def span(p: dict) -> float:
        return min(args.palier_s, max(end - p["first"], 1.0))

    if not plateaux:
        print("no plateau sample (PROFILE=rampe output expected)", file=sys.stderr)
        return 1

    print(
        "| palier (parcours/s) | atteint parcours/s | req/s | p50 ms | p95 ms | p99 ms | échecs | "
        "refus par statut | itérations perdues | verdict |"
    )
    print("|---|---|---|---|---|---|---|---|---|---|")
    sustained, rupture = None, None
    for palier in sorted(plateaux, key=float):
        p = plateaux[palier]
        window = span(p)
        target = float(palier)
        achieved = p["iterations"] / window
        failures = p["failed"] / p["reqs"] if p["reqs"] else 0.0
        lost = sum(1 for t in dropped if p["first"] <= t < p["first"] + window)
        p95 = percentile(p["durations"], 95) if p["durations"] else math.inf
        refusals = refused(p["status"])
        ok = (
            p95 <= args.p95_ms
            and failures <= args.erreurs_max
            and lost == 0
            and achieved >= 0.95 * target
        )
        if rupture is None:
            if ok:
                sustained = palier
            else:
                rupture = palier
        print(
            f"| {palier} | {achieved:.2f} | {p['reqs'] / window:.1f} | "
            f"{percentile(p['durations'], 50):.0f} | {p95:.0f} | {percentile(p['durations'], 99):.0f} | "
            f"{failures:.2%} | {refusals or '-'} | {lost} | {'soutenu' if ok else 'dégradé'} |"
        )

    print()
    print("| palier | " + " | ".join(f"p95 {r}" for r in ROUTES) + " |")
    print("|---|" + "---|" * len(ROUTES))
    for palier in sorted(plateaux, key=float):
        by_route = plateaux[palier]["by_route"]
        cells = [
            f"{percentile(by_route[r], 95):.0f}" if by_route.get(r) else "-"
            for r in ROUTES
        ]
        print(f"| {palier} | " + " | ".join(cells) + " |")

    if any(p["fond_reqs"] for p in plateaux.values()):
        print()
        print("Fiches en fond (scénario fiche_fond, hors verdict) :")
        print()
        print(
            "| palier | fiches/s | p50 ms | p95 ms | p99 ms | échecs fond | refus fond |"
        )
        print("|---|---|---|---|---|---|---|")
        for palier in sorted(plateaux, key=float):
            p = plateaux[palier]
            fiches = p["fond_fiche"]
            window = span(p)
            cells = (
                [f"{percentile(fiches, q):.0f}" for q in (50, 95, 99)]
                if fiches
                else ["-"] * 3
            )
            failures = p["fond_failed"] / p["fond_reqs"] if p["fond_reqs"] else 0.0
            print(
                f"| {palier} | {len(fiches) / window:.2f} | "
                + " | ".join(cells)
                + " | "
                f"{failures:.2%} | {refused(p['fond_status']) or '-'} |"
            )

    print()
    print(
        f"SUSTAINED={sustained or 'none'} BREAK={rupture or 'none'} (p95<={args.p95_ms:g} ms, failures<={args.erreurs_max:.0%})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
