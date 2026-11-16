"""Drift guard for the frontend mirror of the commune publication names (RW-06).

``frontend/src/lib/data/explications.ts`` carries ``COMMUNE_META_SOURCE_NOMS``, a
hand-copied mirror of ``DATASET_CATALOG[<dataset>].nom`` for the thirteen sources
served by ``GET /v1/commune``. The frontend attributes each ``SourceAttribution``
of ``meta.source`` BY NAME against that mirror and fails closed: any drift makes
``sourcesCommuneParDataset`` return ``{}``, which silently erases every "Source:"
line of the Fiche Commune and of the printed table.

Nothing detected that drift before: the frontend suite builds its own fixtures
FROM the mirror, so a backend rename (e.g. fixing "Foncieres" into the accented
spelling) stayed green on both sides while the public surface lost its
attribution. This module is the missing gate: it parses the TypeScript literal
and compares it, key by key and value by value in both directions, with the
canonical catalog.

Pure test: no DB, no network, file read only. ``ci-api.yml`` also watches
``frontend/src/lib/data/explications.ts`` in its ``paths:`` filters so a commit
touching only the mirror still runs this job.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.common.source_catalog import DATASET_CATALOG, RESOLVER_SOURCE_MAP

pytestmark = pytest.mark.unit

# tests/test_commune_source_mirror_contract.py -> parents[1] is api/backend,
# parents[2] is api/, parents[3] is the repo root. Relative to the repo, never
# to the CWD: the CI job runs from api/backend, local runs may not.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIRROR_FILE = _REPO_ROOT / "frontend" / "src" / "lib" / "data" / "explications.ts"

_MIRROR_CONST = "COMMUNE_META_SOURCE_NOMS"

# The object literal assigned to the mirror. The type annotation between the name
# and the "=" is skipped without assuming its shape; the body is brace-free, so a
# character class is enough to stop at the closing brace.
_BLOCK_RE = re.compile(
    r"export\s+const\s+" + _MIRROR_CONST + r"\b[^=]*=\s*\{(?P<body>[^{}]*)\}\s*;",
    re.DOTALL,
)

# One "key: 'value'" entry. The key may be a bare identifier or quoted; the value
# must be a single-quoted or double-quoted string (backslash escapes tolerated).
# Anything else (template literal, spread, computed value) is NOT captured, and
# the resulting missing key turns the key comparison below red: fail closed.
_ENTRY_RE = re.compile(
    r"""(?:(?P<key>[A-Za-z_$][\w$]*)|['"](?P<quoted_key>[^'"]+)['"])\s*:\s*"""
    r"""(?P<q>['"])(?P<value>(?:\\.|(?!(?P=q)).)*)(?P=q)""",
    re.DOTALL,
)

# Line and block comments are stripped before entry scanning so a commented-out
# entry is never mistaken for a live one.
_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)

# Backend truth: the ordered commune aggregate (thirteen canonical sources, D-01),
# which is COMMUNE_SOURCE_KEYS, the list GET /v1/commune serves in meta.source.
_COMMUNE_KEYS = RESOLVER_SOURCE_MAP["commune"]


def _read_mirror_source() -> str:
    """Read the TypeScript file as UTF-8 (it carries accents; never rely on the
    platform default encoding, which is cp1252 on Windows)."""
    assert _MIRROR_FILE.is_file(), (
        f"mirror file not found: {_MIRROR_FILE} — if it moved, this guard must "
        "be repointed, not deleted"
    )
    return _MIRROR_FILE.read_text(encoding="utf-8")


def _parse_mirror() -> dict[str, str]:
    """Parse COMMUNE_META_SOURCE_NOMS into a plain dict, in declaration order."""
    source = _read_mirror_source()
    match = _BLOCK_RE.search(source)
    assert match is not None, (
        f"could not locate the `export const {_MIRROR_CONST} = {{...}}` literal in "
        f"{_MIRROR_FILE.name}: the mirror was renamed, moved or reshaped, and this "
        "guard no longer covers it"
    )
    body = _COMMENT_RE.sub("", match.group("body"))
    entries: dict[str, str] = {}
    for entry in _ENTRY_RE.finditer(body):
        key = entry.group("key") or entry.group("quoted_key")
        assert key not in entries, f"duplicate key in {_MIRROR_CONST}: {key}"
        entries[key] = entry.group("value")
    assert entries, f"{_MIRROR_CONST} parsed as empty — the literal shape changed"
    return entries


@pytest.fixture(scope="module")
def mirror() -> dict[str, str]:
    return _parse_mirror()


def test_mirror_file_is_reachable_and_parsable(mirror: dict[str, str]) -> None:
    """The guard is only worth its green bar if it really read the literal."""
    assert len(mirror) == len(_COMMUNE_KEYS), (
        f"{_MIRROR_CONST} declares {len(mirror)} entries, the commune aggregate "
        f"has {len(_COMMUNE_KEYS)}"
    )


def test_mirror_keys_match_the_commune_aggregate(mirror: dict[str, str]) -> None:
    """Both directions: no entry missing from the mirror, none in excess."""
    expected = set(_COMMUNE_KEYS)
    actual = set(mirror)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    assert not missing, (
        f"{_MIRROR_CONST} is missing the backend dataset(s) {missing}: the "
        "attribution of the Fiche Commune would fall back to an empty map"
    )
    assert not extra, (
        f"{_MIRROR_CONST} declares the unknown dataset(s) {extra}: they are not "
        "part of the commune aggregate served by GET /v1/commune"
    )


def test_mirror_values_match_the_catalog_publication_names(
    mirror: dict[str, str],
) -> None:
    """Value by value against DATASET_CATALOG[<dataset>].nom, character exact."""
    divergences = [
        (
            f"{key}: mirror has {mirror[key]!r}, "
            f"DATASET_CATALOG[{key!r}].nom has {DATASET_CATALOG[key].nom!r}"
        )
        for key in _COMMUNE_KEYS
        if key in mirror and mirror[key] != DATASET_CATALOG[key].nom
    ]
    assert not divergences, (
        f"{_MIRROR_CONST} has drifted from the canonical catalog "
        f"(api/backend/app/common/source_catalog.py): every 'Source:' line of the "
        f"Fiche Commune and of the printed table would disappear:\n  "
        + "\n  ".join(divergences)
    )


def test_every_mirrored_dataset_exists_in_the_catalog(mirror: dict[str, str]) -> None:
    """A mirrored key with no catalog entry cannot be attributed at all."""
    orphans = sorted(key for key in mirror if key not in DATASET_CATALOG)
    assert not orphans, (
        f"{_MIRROR_CONST} names dataset(s) absent from the catalog: {orphans}"
    )
