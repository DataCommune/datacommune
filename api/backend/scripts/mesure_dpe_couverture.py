"""Measure DPE coverage without mistaking documents or joins for dwellings.

Run with ``python -m scripts.mesure_dpe_couverture --help``. No application
settings, .env, database or HTTP client is loaded at import time. Credentials
are named environment variables in a private JSON config: dsn_env, api_url,
environment (fixture/local/production-readonly), optional api_key_env.

--source-manifest accepts {version: 1, partitions: [...]} pointing to the FOUR
raw ADEME CSV files (dpe03existant/dpe02neuf x 44/85), before ETL filtering.
Each partition requires source, department, path, sha256, rows, complete,
version_start, version_end, observed_at, units. Optional delimiter is ',' or
';'. Units name surface_habitable, conso_5_usages_ep, emission_ges_5_usages;
use null when unverified, never guess from numeric values. Version claims and
file completeness need independently collected upstream evidence at task 2.

before freezes canonical BAN text outside every git checkout. after takes
--baseline <private evidence JSON>, replays exactly that corpus and measures
new keys separately. compare takes --baseline and --candidate private evidence.
--sample-size bounds HTTP only; SQL/source remain censuses, never extrapolated.
Public --report is an immutable JSON outside --private-dir. Private evidence
uses content-addressed filenames; --check-report requires it to reconstruct
all counts, enforce the closed projection and refuse missing expected proof.
Unknown upstream freshness remains null and does not justify a reference
refresh; the measured import timestamps and generations are still required.
Observed resolution failures keep unknown coverage. Transport, HTTP, invalid
response and unread inventory evidence remain blocking. V2 reports separate
restored/lost resolution paths from documentary gains and losses. Historical
functional outcomes lacking structural-validation evidence stay unproven.
No mode downloads ADEME, imports data or changes the database.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEPARTMENTS = ("44", "85")
REGIMES = ("existant", "neuf")
BUILDINGS = ("maison", "appartement", "immeuble", "inconnu")
SOURCES = {"dpe03existant": "existant", "dpe02neuf": "neuf"}
FIELDS = ("surface_habitable", "conso_5_usages_ep", "emission_ges_5_usages")
UNITS = dict(zip(FIELDS, ("m2", "kWhEP/year", "kgCO2eq/year"), strict=True))
CAUSES = (
    "covered",
    "inventory_empty",
    "wrong_target",
    "wrong_grain",
    "missing_ban",
    "inventory_unavailable",
    "resolution_absent",
    "resolution_ambiguous",
    "http_error",
    "transport_error",
    "invalid_response",
)
FUNCTIONAL_FAILURES = frozenset(
    {
        "wrong_target",
        "wrong_grain",
        "missing_ban",
        "resolution_absent",
        "resolution_ambiguous",
    }
)
REJECTIONS = (
    "accepted",
    "schema_invalid",
    "identity_missing",
    "department_missing",
    "outside_scope",
    "collision_divergent",
    "duplicate_exact",
    "geometry_missing",
    "geometry_invalid",
)
RAW_MAPPING = {
    "surface_habitable_logement": "surface_habitable",
    "code_departement_ban": "code_departement",
    "coordonnee_cartographique_x_ban": "longitude",
    "coordonnee_cartographique_y_ban": "latitude",
}
RAW_REQUIRED = {
    "numero_dpe",
    "identifiant_ban",
    "type_batiment",
    "code_departement_ban",
    "coordonnee_cartographique_x_ban",
    "coordonnee_cartographique_y_ban",
    "surface_habitable_logement",
    "conso_5_usages_ep",
    "emission_ges_5_usages",
}
READ_ONLY_SQL = "SELECT current_setting('transaction_read_only') AS state"
DPE_SQL = """
SELECT numero_dpe, numero_dpe_remplace, identifiant_ban, code_departement,
       regime_dpe, type_batiment, longitude, latitude, surface_habitable,
       conso_5_usages_ep, emission_ges_5_usages
FROM diagnostics_dpe WHERE code_departement = ANY(%(departements)s)
"""
ADDRESS_SQL = """
SELECT DISTINCT ban_id, code_departement, numero, rep, nom_voie,
       code_postal, nom_commune
FROM adresses WHERE code_departement = ANY(%(departements)s)
  AND NULLIF(ban_id, '') IS NOT NULL
ORDER BY ban_id, code_departement, numero, rep, nom_voie, code_postal, nom_commune
"""
METADATA_SQL = """
SELECT dataset_name AS dataset, last_updated_at, source_updated_at
FROM dataset_metadata WHERE dataset_name IN ('ban', 'cadastre', 'dpe')
"""
GENERATIONS_SQL = """
SELECT 'adresses'::regclass::oid AS addresses,
       'diagnostics_dpe'::regclass::oid AS diagnostics
"""
ALLOWED_SQL = (READ_ONLY_SQL, DPE_SQL, ADDRESS_SQL, METADATA_SQL, GENERATIONS_SQL)
Json = dict[str, Any]
Get = Callable[..., Any]
LINK_COUNTS = (
    "declared",
    "missing_target",
    "ambiguous_target",
    "cross_ban",
    "cross_regime",
    "cross_department",
)

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class CoverageError(ValueError):
    """Only bounded codes may leave the harness."""


def require(condition: Any) -> None:
    if not condition:
        raise CoverageError("invalid_evidence")


def canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def count(value: Any) -> bool:
    return type(value) is int and value >= 0


def timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        value = value.isoformat()
    require(isinstance(value, str) and len(value) <= 40)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None)
    return parsed.isoformat()


def key(row: Json) -> tuple[str, str]:
    return row.get("code_departement", ""), row.get("identifiant_ban") or ""


def building(row: Json) -> str:
    value = row.get("type_batiment")
    return value if value in BUILDINGS else "inconnu"


def grain(ban: Any) -> str:
    if not ban:
        return "missing"
    if not isinstance(ban, str) or not re.fullmatch(
        r"[0-9AB]{5}(?:_[A-Za-z0-9]+){0,3}", ban
    ):
        return "invalid"
    return {0: "commune", 1: "street", 2: "address", 3: "address"}[ban.count("_")]


def geometry(row: Json) -> str:
    x, y = row.get("longitude"), row.get("latitude")
    if x in (None, "") or y in (None, ""):
        return "missing"
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return "invalid"
    if not (math.isfinite(x) and math.isfinite(y)) or (x == 0 and y == 0):
        return "invalid"
    if row.get("_crs") == "EPSG:2154":
        return "valid" if 0 <= x <= 1300000 and 6000000 <= y <= 7200000 else "invalid"
    return "valid" if -180 <= x <= 180 and -90 <= y <= 90 else "invalid"


def totals(rows: Sequence[Json], references: set | None) -> Json:
    keys = {key(row) for row in rows if row.get("identifiant_ban")}
    return {
        "rows": len(rows),
        "distinct_dpe": len({r["numero_dpe"] for r in rows if r.get("numero_dpe")}),
        "distinct_ban": len(keys),
        "joinable_ban": len(keys & references) if references is not None else None,
    }


def strata(rows: Sequence[Json], references: set | None) -> list[Json]:
    groups: dict[tuple, list] = defaultdict(list)
    for row in rows:
        groups[
            (row.get("code_departement"), row.get("regime_dpe"), building(row))
        ].append(row)
    return [
        {
            "department": dept,
            "regime": regime,
            "building": kind,
            **totals(groups[(dept, regime, kind)], references),
        }
        for dept, regime, kind in itertools.product(DEPARTMENTS, REGIMES, BUILDINGS)
    ]


def links(rows: Sequence[Json]) -> Json:
    numbers: dict[str, list] = defaultdict(list)
    for row in rows:
        if row.get("numero_dpe"):
            numbers[row["numero_dpe"]].append(row)
    counts = dict.fromkeys(LINK_COUNTS, 0)
    for row in rows:
        target = row.get("numero_dpe_remplace")
        if not target:
            continue
        counts["declared"] += 1
        matches = numbers.get(target, [])
        if len(matches) != 1:
            counts["missing_target" if not matches else "ambiguous_target"] += 1
            continue
        for label, field in (
            ("ban", "identifiant_ban"),
            ("regime", "regime_dpe"),
            ("department", "code_departement"),
        ):
            counts[f"cross_{label}"] += int(row.get(field) != matches[0].get(field))
    return counts


def census(rows: Sequence[Json], addresses: Sequence[Json] | None) -> Json:
    references = (
        {(r["code_departement"], r["ban_id"]) for r in addresses}
        if addresses is not None
        else None
    )
    keys = {key(r) for r in rows if r.get("identifiant_ban")}
    regimes = [
        {
            key(r)
            for r in rows
            if r.get("regime_dpe") == regime and r.get("identifiant_ban")
        }
        for regime in REGIMES
    ]
    return {
        "status": "available",
        "totals": totals(rows, references),
        "reference_ban": len(references) if references is not None else None,
        "strata": strata(rows, references),
        "unstratified_rows": sum(
            r.get("code_departement") not in DEPARTMENTS
            or r.get("regime_dpe") not in REGIMES
            for r in rows
        ),
        "regime_overlap_ban": len(regimes[0] & regimes[1]),
        "orphan_ban": len(keys - references) if references is not None else None,
        **quality_counts(rows),
    }


def quality_counts(rows: Sequence[Json]) -> Json:
    return {
        "grains": {
            g: sum(grain(r.get("identifiant_ban")) == g for r in rows)
            for g in ("address", "street", "commune", "missing", "invalid")
        },
        "geometry": {
            g: sum(geometry(r) == g for r in rows)
            for g in ("valid", "missing", "invalid")
        },
        "links": links(rows),
        "fill": {
            f: {
                "filled": sum(r.get(f) not in (None, "") for r in rows),
                "denominator": len(rows),
            }
            for f in FIELDS
        },
    }


def payload_hash(row: Json) -> str:
    if "_payload_sha256" in row:
        return row["_payload_sha256"]
    payload = {
        k: str(v) for k, v in row.items() if not k.startswith("_") and k != "regime_dpe"
    }
    return digest(payload)


def base_rejection(row: Json) -> str | None:
    if row.get("_schema_invalid"):
        return "schema_invalid"
    if not row.get("numero_dpe"):
        return "identity_missing"
    if not row.get("code_departement"):
        return "department_missing"
    if row["code_departement"] not in DEPARTMENTS:
        return "outside_scope"
    return None


def source_census(rows: Sequence[Json], addresses: Sequence[Json] | None) -> Json:
    hashes: dict[str, set] = defaultdict(set)
    for row in rows:
        if not base_rejection(row):
            hashes[row["numero_dpe"]].add(payload_hash(row))
    seen: set = set()
    counts = dict.fromkeys(REJECTIONS, 0)
    for row in rows:
        reason = base_rejection(row)
        number = row.get("numero_dpe")
        if not reason and len(hashes[number]) > 1:
            reason = "collision_divergent"
        if not reason and number in seen:
            reason = "duplicate_exact"
        seen.add(number)
        if not reason and geometry(row) != "valid":
            reason = f"geometry_{geometry(row)}"
        counts[reason or "accepted"] += 1
    return {**census(rows, addresses), "rejections": counts}


def validate_private_dir(path: Path) -> Path:
    path = path.resolve(strict=True)
    require(path.is_dir())
    require(not any((parent / ".git").exists() for parent in (path, *path.parents)))
    return path


def private_file(path: Path, root: Path) -> Path:
    resolved = path.resolve(strict=True)
    require(resolved.is_file() and resolved.is_relative_to(root))
    return resolved


def read_json(path: Path) -> Json:
    def pairs(items: list) -> Json:
        result: Json = {}
        for name, value in items:
            require(name not in result)
            result[name] = value
        return result

    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)
    require(isinstance(value, dict))
    return value


def validate_partition(part: Json) -> None:
    required = {
        "source",
        "department",
        "path",
        "sha256",
        "rows",
        "complete",
        "version_start",
        "version_end",
        "observed_at",
        "units",
    }
    require(required <= part.keys() <= required | {"delimiter"})
    require(part["complete"] is True and count(part["rows"]))
    require(isinstance(part["version_start"], str) and part["version_start"])
    require(part["version_start"] == part["version_end"])
    require(timestamp(part["observed_at"]) is not None)
    require(set(part["units"]) == set(FIELDS))
    require(all(v is None or v == UNITS[k] for k, v in part["units"].items()))
    require(part.get("delimiter", ",") in (",", ";"))


def normalize_raw(raw: Json, part: Json, valid_header: bool) -> Json:
    values = {k: v.strip() if isinstance(v, str) else v for k, v in raw.items()}
    row = {RAW_MAPPING.get(k, k): v for k, v in values.items() if k is not None}
    if not row.get("code_departement"):
        row["code_departement"] = str(row.get("code_insee_ban") or "")[:2]
    row["regime_dpe"] = SOURCES[part["source"]]
    row["_crs"] = "EPSG:2154"
    row["_schema_invalid"] = (
        not valid_header or None in raw or any(v is None for v in raw.values())
    )
    if (
        row.get("code_departement") in DEPARTMENTS
        and row["code_departement"] != part["department"]
    ):
        row["_schema_invalid"] = True
    row["_payload_sha256"] = digest({str(k): v for k, v in values.items()})
    kept = {
        "numero_dpe",
        "numero_dpe_remplace",
        "identifiant_ban",
        "code_departement",
        "regime_dpe",
        "type_batiment",
        "longitude",
        "latitude",
        *FIELDS,
    }
    return {k: v for k, v in row.items() if k in kept or k.startswith("_")}


def read_partition(part: Json, root: Path) -> tuple[list[Json], Json]:
    validate_partition(part)
    path = private_file(Path(part["path"]), root)
    file_hash = file_digest(path)
    require(file_hash == part["sha256"])
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=part.get("delimiter", ","))
        header = reader.fieldnames or []
        valid = set(header) >= RAW_REQUIRED and len(header) == len(set(header))
        rows = [normalize_raw(raw, part, valid) for raw in reader]
    require(len(rows) == part["rows"] and valid)
    require(file_digest(path) == file_hash)
    metadata = {
        "source": part["source"],
        "department": part["department"],
        "rows": len(rows),
        "sha256": file_hash,
        "units": part["units"],
        "version_sha256": digest(part["version_start"]),
        "observed_at": timestamp(part["observed_at"]),
    }
    return rows, metadata


def read_sources(path: Path, root: Path) -> Json:
    try:
        manifest = read_json(private_file(path, root))
        require(
            set(manifest) == {"version", "partitions"}
            and type(manifest["version"]) is int
        )
        require(manifest["version"] == 1 and len(manifest["partitions"]) == 4)
        pairs = {(p["source"], p["department"]) for p in manifest["partitions"]}
        require(pairs == set(itertools.product(SOURCES, DEPARTMENTS)))
        rows, metadata = [], []
        for part in manifest["partitions"]:
            batch, meta = read_partition(part, root)
            rows.extend(batch)
            metadata.append(meta)
        return {"status": "available", "rows": rows, "partitions": metadata}
    except Exception:  # noqa: BLE001
        return {"status": "unavailable", "rows": None, "partitions": None}


def _sql_rows(connection: Any, sql: str) -> list[Json]:
    from scripts.verify_golden_cases import assert_select_only

    assert_select_only(sql, ALLOWED_SQL)
    rows = connection.execute(sql, {"departements": list(DEPARTMENTS)}).fetchall()
    return json.loads(json.dumps(rows, default=str), parse_constant=str)


def connect_sql(config: Json, connect: Callable | None) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    return (connect or psycopg.connect)(
        os.environ[config["dsn_env"]],
        connect_timeout=5,
        row_factory=dict_row,
        options="-c statement_timeout=60000",
    )


def read_sql(config: Json, connect: Callable | None) -> Json:
    import psycopg

    connection = None
    try:
        connection = connect_sql(config, connect)
        connection.read_only = True
        connection.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
        require(_sql_rows(connection, READ_ONLY_SQL) == [{"state": "on"}])
        rows = _sql_rows(connection, DPE_SQL)
        addresses = _sql_rows(connection, ADDRESS_SQL)
        require(all(r["code_departement"] in DEPARTMENTS for r in rows + addresses))
        return {
            "status": "available",
            "rows": rows,
            "addresses": addresses,
            "metadata": _sql_rows(connection, METADATA_SQL),
            "generations": _sql_rows(connection, GENERATIONS_SQL),
        }
    except Exception:  # noqa: BLE001
        return {
            "status": "unavailable",
            "rows": None,
            "addresses": None,
            "metadata": None,
            "generations": None,
        }
    finally:
        if connection is not None:
            connection.close()


def address_case(row: Json) -> Json:
    ban, dept = row.get("ban_id"), row.get("code_departement")
    require(isinstance(ban, str) and dept in DEPARTMENTS and ban[:2] == dept)
    street = " ".join(
        str(v)
        for v in (
            f"{row.get('numero') or ''}{row.get('rep') or ''}",
            row.get("nom_voie") or "",
        )
        if v
    )
    city = " ".join(
        str(v) for v in (row.get("code_postal"), row.get("nom_commune")) if v
    )
    query = f"{street}, {city}" if street else city
    require(query and len(query) <= 1000)
    return {
        "id": digest([dept, ban]),
        "department": dept,
        "ban_id": ban,
        "query": query,
        "grain": grain(ban),
    }


def freeze_corpus(addresses: Sequence[Json], sample_size: int | None) -> list[Json]:
    require(sample_size is None or (count(sample_size) and sample_size > 0))
    cases: dict[str, Json] = {}
    for row in addresses:
        case = address_case(row)
        require(case["id"] not in cases or cases[case["id"]] == case)
        cases[case["id"]] = case
    return [cases[k] for k in sorted(cases)][:sample_size]


def inventory_state(page: Any) -> str:
    if (
        not isinstance(page, dict)
        or not inventory_shape(page)
        or not valid_inventory(page)
    ):
        return "inventory_unavailable"
    if page["state"] == "absent":
        return "inventory_empty"
    if page["state"] == "ready" and page["context_ref"]:
        return "covered"
    return "inventory_unavailable"


def valid_inventory(page: Json) -> bool:
    from app.dpe.schemas import DpeInventoryPage

    try:
        DpeInventoryPage.model_validate(page)
    except Exception:  # noqa: BLE001
        return False
    total = page.get("total")
    if (
        page.get("version") != 2
        or not count(total)
        or page.get("read_state") != "complete"
        or not isinstance(page.get("items"), list)
    ):
        return False
    distribution = page.get("distribution", {})
    if (
        set(distribution) != set("ABCDEFG") | {"unknown"}
        or not all(count(v) for v in distribution.values())
        or sum(distribution.values()) != total
    ):
        return False
    return page["state"] != "ready" or (total > 0 and bool(page["items"]))


def inventory_shape(page: Json) -> bool:
    size, more, cursor = (
        page.get("page_size"),
        page.get("has_more"),
        page.get("next_cursor"),
    )
    items = page.get("items")
    if not count(size) or not 1 <= size <= 50 or type(more) is not bool:
        return False
    if not isinstance(items, list) or len(items) > size:
        return False
    if more != (isinstance(cursor, str) and bool(cursor)):
        return False
    if not more and cursor is not None:
        return False
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("document_ref"), str)
        and item["document_ref"]
        for item in items
    ):
        return False
    total = page.get("total")
    if count(total) and not more and total != len(items):
        return False
    return not more or (count(total) and total > len(items) and len(items) == size)


def valid_resolution_row(row: Any) -> bool:
    from app.resolve.schemas import ResultatResolution

    if not isinstance(row, dict) or "ban_id" not in row:
        return False
    if row.get("type_detecte") not in ("adresse", "gps", "cadastre"):
        return False
    try:
        parsed = ResultatResolution.model_validate(row, strict=True)
    except Exception:  # noqa: BLE001
        return False
    return (
        math.isfinite(parsed.latitude)
        and math.isfinite(parsed.longitude)
        and -90 <= parsed.latitude <= 90
        and -180 <= parsed.longitude <= 180
        and grain(parsed.ban_id) != "invalid"
    )


def resolve_outcome(case: Json, response: Any) -> str:
    if response.status_code != 200:
        return "http_error"
    payload = response.json()
    results = payload.get("donnees") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not all(map(valid_resolution_row, results)):
        return "invalid_response"
    if len(results) != 1:
        return "resolution_ambiguous" if results else "resolution_absent"
    row = results[0]
    if row.get("type_detecte") != "adresse" or case["grain"] != "address":
        return "wrong_grain"
    if not row.get("ban_id"):
        return "missing_ban"
    if row["ban_id"] != case["ban_id"]:
        return "wrong_target"
    return inventory_state(row.get("dpe_inventory"))


def replay(corpus: Sequence[Json], get: Get) -> list[Json]:
    results = []
    for case in corpus:
        try:
            response = get(
                "/v1/resolve",
                params={"q": case["query"], "type": "adresse", "datasets": "dpe"},
            )
        except Exception:  # noqa: BLE001
            cause = "transport_error"
        else:
            try:
                cause = resolve_outcome(case, response)
            except Exception:  # noqa: BLE001
                cause = "invalid_response"
        results.append(
            {
                "id": case["id"],
                "cause": cause,
                "functional_observation": cause in FUNCTIONAL_FAILURES,
                "covered": True
                if cause == "covered"
                else False
                if cause == "inventory_empty"
                else None,
            }
        )
    return results


def replay_summary(outcomes: list[Json], population: int) -> Json:
    require(count(population) and len(outcomes) <= population)
    require(len({r["id"] for r in outcomes}) == len(outcomes))
    for row in outcomes:
        require(row["cause"] in CAUSES)
        require(type(row.get("functional_observation", False)) is bool)
        require(
            not row.get("functional_observation", False)
            or row["cause"] in FUNCTIONAL_FAILURES
        )
        expected = (
            True
            if row["cause"] == "covered"
            else False
            if row["cause"] == "inventory_empty"
            else None
        )
        require(row["covered"] is expected)
    counts = Counter(r["cause"] for r in outcomes)
    functional = sum(observed_resolution_failure(row) for row in outcomes)
    unavailable = len(outcomes) - counts["covered"] - counts["inventory_empty"]
    return {
        "population": population,
        "attempted": len(outcomes),
        "covered": counts["covered"],
        "confirmed_empty": counts["inventory_empty"],
        "unavailable": unavailable,
        "observed_resolution_failures": functional,
        "unusable_evidence": unavailable - functional,
        "sampling": "census" if len(outcomes) == population else "sample",
        "extrapolated": False,
        "causes": {c: counts[c] for c in CAUSES},
    }


def memberships(sql: Json) -> list[Json]:
    reference_ids = {r["id"] for r in freeze_corpus(sql["addresses"] or [], None)}
    groups: dict[tuple, set] = defaultdict(set)
    for row in sql["rows"] or []:
        groups[(row.get("code_departement"), row.get("regime_dpe"), building(row))].add(
            digest(list(key(row)))
        )
    result = []
    for dept, regime, kind in itertools.product(DEPARTMENTS, REGIMES, BUILDINGS):
        result.append(
            {
                "department": dept,
                "regime": regime,
                "building": kind,
                "ids": sorted(groups[(dept, regime, kind)] & reference_ids),
            }
        )
    assigned = set().union(*(set(group["ids"]) for group in result))
    result.append(
        {
            "department": "44/85",
            "regime": "unclassified",
            "building": "inconnu",
            "ids": sorted(reference_ids - assigned),
        }
    )
    return result


def stratified_replay(evidence: Json) -> list[Json]:
    result = []
    for group in evidence["memberships"]:
        ids = set(group["ids"])
        result.append(
            {
                "department": group["department"],
                "regime": group["regime"],
                "building": group["building"],
                **replay_summary(
                    [r for r in evidence["outcomes"] if r["id"] in ids],
                    len(group["ids"]),
                ),
            }
        )
    return result


def comparison_strata(before: Json, pairs: list[tuple]) -> list[Json]:
    result = []
    for group in before["memberships"]:
        ids = set(group["ids"])
        selected = [(a, b) for a, b in pairs if a["id"] in ids]
        result.append(
            {
                "department": group["department"],
                "regime": group["regime"],
                "building": group["building"],
                "denominator": len(selected),
                "gains": sum(
                    a["covered"] is False and b["covered"] is True for a, b in selected
                ),
                "losses": sum(
                    a["covered"] is True and b["covered"] is False for a, b in selected
                ),
                **resolution_path_counts(selected),
            }
        )
    return result


def fresh_metadata(rows: list[Json] | None) -> Json:
    by_name = {r["dataset"]: r for r in rows or []}
    return {
        name: {
            field: timestamp(by_name.get(name, {}).get(field))
            for field in ("last_updated_at", "source_updated_at")
        }
        for name in ("ban", "cadastre", "dpe")
    }


def source_projection(source: Json, addresses: list[Json] | None) -> Json:
    if source["status"] != "available":
        return {"status": "unavailable", "totals": None, "partitions": None}
    validate_source_metadata(source)
    return {
        **source_census(source["rows"], addresses),
        "partitions": source["partitions"],
    }


def validate_source_metadata(source: Json) -> None:
    parts = source["partitions"]
    require(len(parts) == 4)
    require(
        {(p["source"], p["department"]) for p in parts}
        == set(itertools.product(SOURCES, DEPARTMENTS))
    )
    for part in parts:
        require(
            set(part)
            == {
                "source",
                "department",
                "rows",
                "sha256",
                "units",
                "version_sha256",
                "observed_at",
            }
        )
        require(count(part["rows"]) and timestamp(part["observed_at"]) is not None)
        for field in ("sha256", "version_sha256"):
            require(
                isinstance(part[field], str)
                and re.fullmatch(r"[0-9a-f]{64}", part[field])
            )
        require(set(part["units"]) == set(FIELDS))
        require(all(v is None or v == UNITS[k] for k, v in part["units"].items()))
    require(sum(p["rows"] for p in parts) == len(source["rows"]))


def report_provenance(evidence: Json) -> Json:
    require(evidence["environment"] in ("fixture", "local", "production-readonly"))
    require(re.fullmatch(r"[0-9a-f]{40}", evidence["code_revision"]))
    require(
        [c["id"] for c in evidence["corpus"]] == [r["id"] for r in evidence["outcomes"]]
    )
    return {
        "schema": "58.2-coverage-v2",
        "mode": evidence["mode"],
        "created_at": timestamp(evidence["created_at"]),
        "environment": evidence["environment"],
        "code_revision": evidence["code_revision"],
        "harness_sha256": evidence["harness_sha256"],
        "corpus_sha256": digest(evidence["corpus"]),
    }


def measurement_projection(evidence: Json) -> Json:
    validate_population(evidence)
    sql = evidence["sql"]
    census_sql = (
        census(sql["rows"], sql["addresses"])
        if sql["status"] == "available"
        else {"status": "unavailable", "totals": None}
    )
    census_sql["freshness"] = fresh_metadata(sql["metadata"])
    return {
        **report_provenance(evidence),
        "source": source_projection(evidence["source"], sql["addresses"]),
        "sql": census_sql,
        "source_manifest_sha256": digest(evidence["source"]["partitions"]),
        "import_sha256": digest([sql["generations"], sql["metadata"]]),
        "replay": replay_summary(evidence["outcomes"], evidence["population"]),
        "replay_strata": stratified_replay(evidence),
        "entrants_replay": replay_summary(
            evidence["entrant_outcomes"], len(evidence["entrants"])
        ),
        "baseline_sha256": evidence.get("baseline_sha256"),
        "candidate_sha256": None,
        "comparison": None,
    }


def load_evidence(path: Path, root: Path) -> Json:
    path = private_file(path, root)
    value = read_json(path)
    require(path.name == f"{digest(value)}.json")
    return value


def validate_population(evidence: Json) -> None:
    require(timestamp(evidence["created_at"]) is not None)
    require(re.fullmatch(r"[0-9a-f]{64}", evidence["harness_sha256"]))
    if evidence["sql"]["status"] == "available":
        generations = evidence["sql"]["generations"]
        require(len(generations) == 1)
        require(set(generations[0]) == {"addresses", "diagnostics"})
        require(all(count(v) and v > 0 for v in generations[0].values()))
    references = freeze_corpus(evidence["sql"]["addresses"] or [], sample_size=None)
    require(evidence["reference_ids"] == [r["id"] for r in references])
    require(count(evidence["population"]))
    if evidence["mode"] == "before":
        require(evidence["population"] == len(references))
        require(evidence["corpus"] == references[: len(evidence["corpus"])])
        require(evidence["entrants"] == evidence["entrant_outcomes"] == [])
        require(evidence["memberships"] == memberships(evidence["sql"]))
    require(
        [r["id"] for r in evidence["entrants"]]
        == [r["id"] for r in evidence["entrant_outcomes"]]
    )
    require(len({r["id"] for r in evidence["corpus"]}) == len(evidence["corpus"]))


def compare_evidence(before: Json, after: Json) -> Json:
    validate_population(before)
    validate_population(after)
    require(before["mode"] == "before" and after["mode"] == "after")
    require(after["baseline_sha256"] == digest(before))
    require(canonical(before["corpus"]) == canonical(after["corpus"]))
    require(before["environment"] == after["environment"])
    require(before["population"] == after["population"])
    require(before["memberships"] == after["memberships"])
    require(before["sql"]["status"] == after["sql"]["status"] == "available")
    pairs = list(zip(before["outcomes"], after["outcomes"], strict=True))
    require(all(a["id"] == b["id"] for a, b in pairs))
    old, new = set(before["reference_ids"]), set(after["reference_ids"])
    require({c["id"] for c in after["entrants"]} == new - old)
    return {
        "entrants": len(new - old),
        "exits": len(old - new),
        "strata": comparison_strata(before, pairs),
        **comparison_counts(pairs),
    }


def observed_resolution_failure(row: Json) -> bool:
    return (
        row["cause"] in FUNCTIONAL_FAILURES
        and row.get("functional_observation") is True
    )


def resolution_path_counts(pairs: list[tuple]) -> Json:
    return {
        "resolution_paths_restored": sum(
            observed_resolution_failure(a) and b["covered"] is True for a, b in pairs
        ),
        "resolution_paths_lost": sum(
            a["covered"] is True and observed_resolution_failure(b) for a, b in pairs
        ),
    }


def comparison_counts(pairs: list[tuple]) -> Json:
    transitions = Counter((a["cause"], b["cause"]) for a, b in pairs)
    return {
        "common_denominator": len(pairs),
        "gains": sum(a["covered"] is False and b["covered"] is True for a, b in pairs),
        "losses": sum(a["covered"] is True and b["covered"] is False for a, b in pairs),
        "unresolved_pairs": sum(
            a["covered"] is None or b["covered"] is None for a, b in pairs
        ),
        **resolution_path_counts(pairs),
        "transitions": [
            {"before": a, "after": b, "count": transitions[(a, b)]}
            for a, b in itertools.product(CAUSES, repeat=2)
        ],
    }


def project(evidence: Json, root: Path) -> Json:
    if evidence["mode"] == "compare":
        before = load_evidence(root / f"{evidence['baseline_sha256']}.json", root)
        after = load_evidence(root / f"{evidence['candidate_sha256']}.json", root)
        public = measurement_projection(after)
        public.update(
            mode="compare",
            created_at=timestamp(evidence["created_at"]),
            comparison=compare_evidence(before, after),
            candidate_sha256=evidence["candidate_sha256"],
        )
    else:
        require(evidence["mode"] in ("before", "after"))
        public = measurement_projection(evidence)
        if evidence["mode"] == "after":
            before = load_evidence(root / f"{evidence['baseline_sha256']}.json", root)
            public["comparison"] = compare_evidence(before, evidence)
    return {**public, "evidence_sha256": digest(evidence)}


def check_report(public: Json, root: Path) -> bool:
    try:
        root = validate_private_dir(root)
        sha = public["evidence_sha256"]
        require(re.fullmatch(r"[0-9a-f]{64}", sha))
        expected = project(load_evidence(root / f"{sha}.json", root), root)
        require(canonical(public) == canonical(expected))
        assert_complete_proof(public)
        if public["baseline_sha256"] is not None:
            baseline = load_evidence(root / f"{public['baseline_sha256']}.json", root)
            require(baseline["mode"] == "before")
            assert_complete_proof(project(baseline, root))
        return True
    except Exception:  # noqa: BLE001
        return False


def assert_complete_proof(public: Json) -> None:
    require(public["source"]["status"] == public["sql"]["status"] == "available")
    require(public["replay"]["attempted"] > 0)
    require(public["replay"]["unusable_evidence"] == 0)
    require(public["entrants_replay"]["unusable_evidence"] == 0)
    # D-09 inventories upstream freshness, including unknown dates. Import
    # provenance is required; its timestamp must never stand in for the source.
    require(
        all(
            dates["last_updated_at"] is not None
            for dates in public["sql"]["freshness"].values()
        )
    )
    require(
        all(
            all(v is not None for v in p["units"].values())
            for p in public["source"]["partitions"]
        )
    )
    require(public["source"]["rejections"]["collision_divergent"] == 0)


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CoverageError("invalid_arguments")


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = Parser(description=__doc__)
    parser.add_argument("--mode", choices=("before", "after", "compare"))
    parser.add_argument("--check-report", action="store_true")
    for name in (
        "private-dir",
        "report",
        "baseline",
        "candidate",
        "config",
        "source-manifest",
    ):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("--sample-size", type=int)
    args = parser.parse_args(argv)
    require(args.private_dir is not None and args.report is not None)
    require(args.check_report != (args.mode is not None))
    if args.mode in ("after", "compare"):
        require(args.baseline is not None)
    if args.mode == "compare":
        require(args.candidate is not None)
    if args.mode in ("before", "after"):
        require(args.config is not None and args.source_manifest is not None)
    require(
        args.sample_size is None or (args.mode == "before" and args.sample_size > 0)
    )
    return args


def read_config(path: Path, root: Path) -> Json:
    config = read_json(private_file(path, root))
    require(
        {"dsn_env", "api_url", "environment"}
        <= config.keys()
        <= {"dsn_env", "api_url", "environment", "api_key_env"}
    )
    require(config["environment"] in ("fixture", "local", "production-readonly"))
    for field in ("dsn_env", "api_key_env"):
        require(
            field not in config or re.fullmatch(r"[A-Z][A-Z0-9_]{0,99}", config[field])
        )
    url = urlsplit(config["api_url"])
    require(
        url.scheme in ("http", "https")
        and url.hostname
        and not url.username
        and not url.password
        and not url.query
        and not url.fragment
        and url.path in ("", "/")
    )
    return config


def code_revision() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=Path(__file__).resolve().parent,
        capture_output=True,
        text=True,
        check=True,
    )
    revision = result.stdout.strip()
    require(re.fullmatch(r"[0-9a-f]{40}", revision))
    return revision


def http_replay(
    config: Json, corpus: list, entrants: list, get: Get | None
) -> tuple[list, list]:
    if get is not None:
        return replay(corpus, get), replay(entrants, get)
    import httpx

    headers = (
        {"X-API-Key": os.environ[config["api_key_env"]]}
        if config.get("api_key_env")
        else {}
    )
    with httpx.Client(
        base_url=config["api_url"],
        headers=headers,
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        return replay(corpus, client.get), replay(entrants, client.get)


def measure(
    args: argparse.Namespace, root: Path, connect: Callable | None, get: Get | None
) -> Json:
    config = read_config(args.config, root)
    source = read_sources(args.source_manifest, root)
    sql = read_sql(config, connect)
    references = freeze_corpus(sql["addresses"] or [], sample_size=None)
    baseline = load_evidence(args.baseline, root) if args.baseline else None
    corpus = baseline["corpus"] if baseline else references[: args.sample_size]
    old_ids = set(baseline["reference_ids"]) if baseline else set()
    entrants = [c for c in references if c["id"] not in old_ids] if baseline else []
    outcomes, new_outcomes = http_replay(config, corpus, entrants, get)
    return {
        "mode": args.mode,
        "created_at": datetime.now(UTC).isoformat(),
        "environment": config["environment"],
        "code_revision": code_revision(),
        "harness_sha256": file_digest(Path(__file__)),
        "source": source,
        "sql": sql,
        "corpus": corpus,
        "outcomes": outcomes,
        "entrants": entrants,
        "entrant_outcomes": new_outcomes,
        "reference_ids": [c["id"] for c in references],
        "population": baseline["population"] if baseline else len(references),
        "memberships": baseline["memberships"] if baseline else memberships(sql),
        "baseline_sha256": digest(baseline) if baseline else None,
    }


def write_immutable(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(data)
    # Windows uses directory ACL inheritance; chmod protects POSIX artifacts.
    if os.name != "nt":
        path.chmod(0o600)


def execute(args: argparse.Namespace, connect: Callable | None, get: Get | None) -> int:
    root = validate_private_dir(args.private_dir)
    report = args.report.resolve()
    require(not report.is_relative_to(root))
    if args.check_report:
        return 0 if check_report(read_json(report), root) else 2
    require(not report.exists() and report.parent.is_dir())
    if args.mode == "compare":
        before, after = (
            load_evidence(args.baseline, root),
            load_evidence(args.candidate, root),
        )
        compare_evidence(before, after)
        evidence = {
            "mode": "compare",
            "created_at": datetime.now(UTC).isoformat(),
            "baseline_sha256": digest(before),
            "candidate_sha256": digest(after),
        }
    else:
        evidence = measure(args, root, connect, get)
    public = project(evidence, root)
    private_path = root / f"{digest(evidence)}.json"
    write_immutable(private_path, canonical(evidence))
    write_immutable(report, canonical(public))
    return 0


def main(
    argv: Sequence[str] | None = None,
    *,
    connect: Callable | None = None,
    get: Get | None = None,
) -> int:
    try:
        return execute(parse_args(argv), connect, get)
    except Exception:  # noqa: BLE001
        print("coverage_measurement_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
