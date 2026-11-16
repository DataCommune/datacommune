"""BDNB fidelity count and equivalence rule against the base (plan 04.7-19).

Disposable wrapper database only. The mini-export of ``tests/etl/bdnb_fixture``
is loaded by the real runner and archived, then:

- ``scripts/fidelite_nationale.py``: ``count --archive-run`` on the archived
  zips, ``loaded`` on the base, ``diff``: every metric of the closed list
  ``egal`` for 2A, 44 and 85, none missing, the ``millesime`` block equal; the
  raw metrics equal the ``count:*`` counters of the run (T4a);
- ``scripts/bdnb_equivalence.py``: its rule (``attendu``) gives, parcel by
  parcel, exactly the groups, attributes and principal flags the swap
  published (drift of the gate against the pipeline, T5).

Mutations these tests catch: ``LEFT JOIN ffo`` turned into ``JOIN ffo`` in the
swap (the group without ffo row disappears from the base, not from the
count, nor from the rule); a compared metric removed from ``loaded`` (``diff``
fails).

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_bdnb_fidelite.py
"""

import json

import pytest

from scripts import fidelite_nationale as fn
from tests.etl.bdnb_fixture import LABEL, mini_exports
from tests.integration import test_bdnb_pipeline as base
from tests.test_bdnb_equivalence import mini_zips, rows_by_parcel, rule_rows

pytestmark = pytest.mark.destructive

# Fixtures of the pipeline tests: migrated child database, bdnb hosted on
# 2A/44/85 without the producer floors, temporary source archive.
migrated = base.migrated
env = base.env
archive = base.archive


def _cli(capsys, argv: list[str]) -> tuple[int, dict]:
    code = fn.main(argv)
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture
def loaded_run(env, archive, monkeypatch, tmp_path):
    """The mini-export loaded and archived; the run result."""
    base.serve_exports(monkeypatch, tmp_path / "producteur", mini_exports())
    _pipeline, result = base.run()
    assert result.success, result.error
    return result


def test_the_archive_count_equals_the_base_and_the_receipt(
    env, archive, loaded_run, capsys, tmp_path
):
    (record,) = base._records(archive)
    code, producer = _cli(
        capsys,
        [
            "count",
            "--source",
            "bdnb",
            "--archive-run",
            f"{base.TABLE}:{record.stem}",
            "--archive-root",
            str(archive),
        ],
    )
    assert code == 0 and producer["pannes"] == {}
    code, charge = _cli(capsys, ["loaded", "--dataset", base.TABLE])
    assert code == 0 and charge["source_period_label"] == LABEL
    p_path, l_path = tmp_path / "producer.json", tmp_path / "loaded.json"
    p_path.write_text(json.dumps(producer), encoding="utf-8")
    l_path.write_text(json.dumps(charge), encoding="utf-8")

    code, result = _cli(
        capsys, ["diff", "--producer", str(p_path), "--loaded", str(l_path)]
    )

    assert code == 0, result["erreurs"]
    assert set(result["metriques"]) == set(fn.METRIQUES_COMPAREES["bdnb"])
    for metric, cells in result["metriques"].items():
        assert set(cells) == set(base.DEPARTEMENTS), metric
        assert {cell["classe"] for cell in cells.values()} == {"egal"}, metric
    assert result["millesime"] == {
        "etiquettes_zips": dict.fromkeys(base.DEPARTEMENTS, LABEL),
        "enregistrement": LABEL,
        "base": LABEL,
        "classe": "egal",
    }
    # Raw metrics: exactly the count:* counters of the receipt of the run.
    assert fn.bdnb_receipt_counters(producer) == base._counts(loaded_run)


def test_the_equivalence_rule_is_the_published_rows(env, loaded_run, tmp_path):
    assert rule_rows(mini_zips(tmp_path)) == rows_by_parcel(base.published(env))
