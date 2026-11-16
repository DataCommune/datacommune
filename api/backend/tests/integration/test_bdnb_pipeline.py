"""The BDNB parcel table loaded end to end from the mini-export (plan 04.7-19).

Disposable wrapper database only. The real runner, the real pipeline and the
real SQL of the swap load the synthetic exports of ``tests/etl/bdnb_fixture``;
nothing is downloaded (the HTTP download is replaced by a copy of the built
zip under its producer name).

Mutations the published rows must catch (each one fails
``test_the_published_rows_are_the_written_expectation``):
  - ``LEFT JOIN ffo`` turned into ``JOIN ffo`` in ``_TRANSFER_SQL``: the group
    without ffo row (``bdnb-bg-85B0-0000-0002``) disappears;
  - ``AND parcelle_principale`` added to the ``relations`` filter: the
    non-principal relation of B disappears.

Refusals before publication (T2), production unchanged each time: another
millesime, an absent member, a missing column, a member above its cap, a
departement not delivered, a departement under its producer floor, and a
group whose ffo rows diverge between two files (D13). The archive record of
a run carries ``2026-02.b``; a replay from it publishes the same table and a
record with the same label, and a replayed zip of another millesime is
refused before anything is archived.

Run via the wrapper script, never directly:
    pwsh -NoProfile -File scripts/test-postgis.ps1 \
        -Paths tests/integration/test_bdnb_pipeline.py
"""

import json
import shutil
import zipfile
from dataclasses import replace

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from app.config import settings
from app.etl import source_archive as sa
from app.etl.checkpoint import CheckpointManager
from app.etl.hooks import DependentMatviewHook, ValidationHook
from app.etl.runner import PipelineRunner
from app.etl_bdnb import pipeline as bdnb_pipeline
from app.etl_bdnb import transform as bdnb_transform
from app.etl_bdnb.config import MEMBERS_READ
from app.etl_bdnb.pipeline import BdnbBatimentParcellePipeline
from app.scope import floors, load_scope_sync
from scripts.table_digest import table_digest
from tests.etl.bdnb_fixture import (
    FFO,
    LABEL,
    MILLESIME_URL,
    RELATIONS,
    attendu,
    build_export,
    mini_exports,
    with_label,
)
from tests.integration.test_instance_scope import BACKEND

pytestmark = pytest.mark.destructive

TABLE = "bdnb_batiment_parcelle"
DEPARTEMENTS = ("2A", "44", "85")


def _db_scope_loader(dsn):
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        return load_scope_sync(conn)


def _alembic(dsn: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", dsn)
    return cfg


@pytest.fixture(scope="module")
def migrated(isolated_pg_dsn):
    with pytest.MonkeyPatch.context() as context:
        context.setenv("DATABASE_URL", isolated_pg_dsn)
        context.setattr(settings, "database_url", isolated_pg_dsn)
        command.upgrade(_alembic(isolated_pg_dsn), "head")
    return isolated_pg_dsn


def serve_exports(monkeypatch, directory, exports) -> dict[str, list[str]]:
    """Replace the HTTP download by a copy of the built zips; return the URLs
    asked per departement."""
    built = {
        dept.lower(): build_export(directory, dept, export)
        for dept, export in exports.items()
    }
    asked: dict[str, list[str]] = {}

    async def copy(_client, url, output_path, **_kwargs):
        dept = url.rsplit("_dep", 1)[1].split("_", 1)[0]
        asked.setdefault(dept, []).append(url)
        if dept not in built:
            return False
        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built[dept], output_path)
        return True

    monkeypatch.setattr(bdnb_pipeline, "verified_download", copy)
    return asked


def without_producer_floors(monkeypatch) -> None:
    """The mini-export holds a few rows per departement: the producer floors
    (80 % of the national counts, plan 04.7-19 T4a) would refuse every run.
    The floor itself is tested with its own counts
    (``test_a_departement_under_its_producer_floor_is_refused``)."""
    monkeypatch.delitem(floors.PRODUCER_COUNTS, TABLE, raising=False)


@pytest.fixture
def env(migrated, monkeypatch, tmp_path, instance_scope):
    without_producer_floors(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", migrated)
    monkeypatch.setattr(settings, "database_url", migrated)
    monkeypatch.setattr(settings, "etl_temp_dir", str(tmp_path / "etl"))
    monkeypatch.setattr(settings, "source_archive_dir", "")
    instance_scope(migrated, ["bdnb"], DEPARTEMENTS)
    return migrated


def run(pipeline: BdnbBatimentParcellePipeline | None = None):
    pipeline = pipeline or BdnbBatimentParcellePipeline()
    pipeline.pin_millesime(MILLESIME_URL)
    runner = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=_db_scope_loader
    )
    return pipeline, runner.run(pipeline)


def published(dsn) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            f"SELECT code_departement, parcelle_id, batiment_groupe_id, "
            f"parcelle_principale, annee_construction, mat_mur_txt, mat_toit_txt "
            f'FROM {TABLE} ORDER BY code_departement COLLATE "C", '
            f'parcelle_id COLLATE "C", batiment_groupe_id COLLATE "C"'
        ).fetchall()


def test_the_published_rows_are_the_written_expectation(env, monkeypatch, tmp_path):
    serve_exports(monkeypatch, tmp_path / "producteur", mini_exports())
    opened: list[str] = []
    real_open = zipfile.ZipFile.open

    def spy(self, name, *args, **kwargs):
        opened.append(name if isinstance(name, str) else name.filename)
        return real_open(self, name, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", spy)

    _pipeline, result = run()

    assert result.success, result.error
    assert published(env) == attendu()
    # Only the three named members, never batiment_groupe.csv nor the others.
    assert opened
    assert set(opened) <= set(MEMBERS_READ)
    with psycopg.connect(env) as conn:
        label, departements = conn.execute(
            "SELECT source_period_label, effective_departments "
            "FROM dataset_metadata WHERE dataset_name = %s",
            (TABLE,),
        ).fetchone()
        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = %s", (TABLE,)
            )
        }
    assert label == LABEL
    assert sorted(departements) == sorted(DEPARTEMENTS)
    assert indexes == {"bdnb_batiment_parcelle_pkey", "idx_bdnb_bp_parcelle_groupe"}


def test_migration_113_goes_down_to_112_and_back(env):
    cfg = _alembic(env)
    command.downgrade(cfg, "112_departement_synthese")
    with psycopg.connect(env) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] is None
    command.upgrade(cfg, "head")
    with psycopg.connect(env) as conn:
        assert conn.execute("SELECT to_regclass(%s)", (TABLE,)).fetchone()[0] == TABLE


# --- T2: receipt, archive, replay, refusals -----------------------------------


@pytest.fixture
def archive(env, monkeypatch, tmp_path):
    root = tmp_path / "archive"
    root.mkdir()
    (root / sa.SENTINEL_NAME).write_text(sa.SENTINEL_CONTENT + "\n", "utf-8")
    monkeypatch.setattr(settings, "source_archive_dir", str(root))
    return root


def _records(root) -> list:
    return sorted((root / TABLE / "runs").glob("*.json"))


def _state(dsn) -> tuple:
    """Digest of the published table (``id`` excluded) and its metadata."""
    with psycopg.connect(dsn) as conn:
        digest = table_digest(
            conn, TABLE, ["parcelle_id", "batiment_groupe_id"], ["id"]
        )
        metadata = conn.execute(
            "SELECT source_period_label, row_count FROM dataset_metadata "
            "WHERE dataset_name = %s",
            (TABLE,),
        ).fetchone()
    return digest, metadata


def _counts(result) -> dict[str, int]:
    return {
        stage.stage_name.removeprefix("count:"): stage.rows_affected
        for stage in result.stages
        if stage.stage_name.startswith("count:")
    }


def test_the_receipt_counts_and_the_archived_label(env, archive, monkeypatch, tmp_path):
    serve_exports(monkeypatch, tmp_path / "producteur", mini_exports())

    _pipeline, result = run()

    assert result.success, result.error
    assert _counts(result) == {
        "producteur_relations_2A": 1,
        "producteur_ffo_2A": 1,
        "relations_ecartees_2A": 0,
        "producteur_relations_44": 2,
        "producteur_ffo_44": 2,
        "relations_ecartees_44": 0,
        "producteur_relations_85": 19,
        "producteur_ffo_85": 17,
        "relations_ecartees_85": 1,
        "publiees_2A": 1,
        "publiees_44": 2,
        "publiees_85": 17,
        "doublons_inter_fichiers": 1,
        "ffo_divergents": 0,
    }
    (record,) = _records(archive)
    contenu = json.loads(record.read_text("utf-8"))
    assert contenu["source_period_label"] == LABEL
    assert len(contenu["files"]) == 3


def test_a_replay_publishes_the_same_table_with_the_same_label(
    env, archive, monkeypatch, tmp_path
):
    serve_exports(monkeypatch, tmp_path / "producteur", mini_exports())
    _pipeline, result = run()
    assert result.success, result.error
    before = _state(env)
    (record,) = _records(archive)

    async def no_download(*_args, **_kwargs):
        pytest.fail("a replay downloads nothing")

    monkeypatch.setattr(bdnb_pipeline, "verified_download", no_download)
    replay = sa.load_replay(archive, TABLE, record.stem)
    replayed = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=_db_scope_loader
    ).run(BdnbBatimentParcellePipeline(), source_replay=replay)

    assert replayed.success, replayed.error
    assert _state(env) == before
    nouveau = [r for r in _records(archive) if r != record]
    assert len(nouveau) == 1
    contenu = json.loads(nouveau[0].read_text("utf-8"))
    assert contenu["replayed_from"] == record.stem
    # Read back from the restored zips by restore_source_inputs: null without.
    assert contenu["source_period_label"] == LABEL
    assert replay.record["source_period_label"] == LABEL


def test_a_replayed_zip_of_another_millesime_is_refused_before_archiving(
    env, archive, monkeypatch, tmp_path
):
    serve_exports(monkeypatch, tmp_path / "producteur", mini_exports())
    assert run()[1].success
    before = _state(env)
    exports = mini_exports()
    sources = tmp_path / "ancien"
    files = [
        build_export(sources / "raw", "85", with_label(exports["85"], "2026-02.a")),
        build_export(sources / "raw", "44", exports["44"]),
        build_export(sources / "raw", "2A", exports["2A"]),
    ]
    sa.archive_sources(
        archive,
        TABLE,
        "999-20261102T120000Z",
        files,
        sources,
        origin="telechargement",
        source_period_label=LABEL,
    )
    records = _records(archive)
    replay = sa.load_replay(archive, TABLE, "999-20261102T120000Z")

    result = PipelineRunner(
        [ValidationHook(), DependentMatviewHook()], scope_loader=_db_scope_loader
    ).run(BdnbBatimentParcellePipeline(), source_replay=replay)

    assert not result.success
    assert "2026-02.a" in result.error
    # A failed stage is never appended to ``stages`` (``_run_stage`` raises):
    # no stage ran before the refusal, and the refusal is the restore's.
    assert result.stages == []
    assert result.error.startswith("Stage 'restore_sources' failed:")
    assert _records(archive) == records
    assert _state(env) == before


class SimulatedCrash(BaseException):
    """A process killed after a checkpoint: no handler of the runner runs."""


RECU_COMPLET = {
    "producteur_relations_2A": 1,
    "producteur_ffo_2A": 1,
    "relations_ecartees_2A": 0,
    "producteur_relations_44": 2,
    "producteur_ffo_44": 2,
    "relations_ecartees_44": 0,
    "producteur_relations_85": 19,
    "producteur_ffo_85": 17,
    "relations_ecartees_85": 1,
    "publiees_2A": 1,
    "publiees_44": 2,
    "publiees_85": 17,
    "doublons_inter_fichiers": 1,
    "ffo_divergents": 0,
}


def _receipt(dsn) -> dict[str, int]:
    """``count:*`` steps of the last etl_runs row of the import (the receipt)."""
    with psycopg.connect(dsn) as conn:
        (stages,) = conn.execute(
            "SELECT stages FROM etl_runs WHERE dataset_name = %s "
            "ORDER BY id DESC LIMIT 1",
            (TABLE,),
        ).fetchone()
    return {
        s["stage_name"].removeprefix("count:"): s["rows_affected"]
        for s in stages
        if s["stage_name"].startswith("count:")
    }


def test_a_resume_after_a_checkpoint_journals_every_departement(
    env, monkeypatch, tmp_path
):
    """Interrupted after the checkpoint of its first file, the import resumes
    with a new instance that skips that file: the receipt still names all."""
    serve_exports(monkeypatch, tmp_path / "producteur", mini_exports())
    with monkeypatch.context() as interrupt:
        original = CheckpointManager.save

        def save_then_crash(self, loaded_files, **kwargs):
            original(self, loaded_files, **kwargs)
            if kwargs.get("pending") is None and len(loaded_files) == 1:
                raise SimulatedCrash

        interrupt.setattr(CheckpointManager, "save", save_then_crash)
        with pytest.raises(SimulatedCrash):
            run()

    _pipeline, result = run()

    assert result.success, result.error
    assert "resume_stock" in [s.stage_name for s in result.stages]
    assert _counts(result) == RECU_COMPLET
    assert _receipt(env) == RECU_COMPLET
    assert published(env) == attendu()


def _diverge(exports):
    dep44 = exports["44"]
    ffo = tuple(
        (g, "1931" if g == "bdnb-bg-DUP0-0000-0001" else a, mur, toit)
        for g, a, mur, toit in dep44.ffo
    )
    return {**exports, "44": replace(dep44, ffo=ffo)}


REFUS = {
    "autre_millesime": (
        lambda e: {**e, "44": with_label(e["44"], "2026-02.a")},
        "millésime 2026-02.a",
    ),
    "membre_absent": (
        lambda e: {**e, "85": replace(e["85"], omit=(FFO,))},
        "membre absent",
    ),
    "colonne_manquante": (
        lambda e: {
            **e,
            "85": replace(
                e["85"], raw={RELATIONS: b"batiment_groupe_id;parcelle_id\nG;P\n"}
            ),
        },
        "colonnes absentes",
    ),
    "departement_non_livre": (
        lambda e: {d: x for d, x in e.items() if d != "85"},
        "85 refusé (HTTP 404)",
    ),
    "ffo_divergents": (_diverge, "ffo_divergents"),
}


@pytest.mark.parametrize("cas", sorted(REFUS))
def test_a_refused_run_leaves_production_unchanged(env, monkeypatch, tmp_path, cas):
    serve_exports(monkeypatch, tmp_path / "base", mini_exports())
    assert run()[1].success
    before = _state(env)
    alterer, message = REFUS[cas]
    serve_exports(monkeypatch, tmp_path / "refus", alterer(mini_exports()))

    _pipeline, result = run()

    assert not result.success
    assert message in result.error
    assert _state(env) == before
    if cas == "ffo_divergents":
        # D13: the count that refused the candidate is on the receipt.
        assert _receipt(env)["ffo_divergents"] == 1


def test_a_member_above_its_cap_is_refused(env, monkeypatch, tmp_path):
    serve_exports(monkeypatch, tmp_path / "base", mini_exports())
    assert run()[1].success
    before = _state(env)
    monkeypatch.setattr(bdnb_transform, "MAX_MEMBER_BYTES", 200)

    _pipeline, result = run()

    assert not result.success
    assert "au-delà du plafond 200" in result.error
    assert _state(env) == before


def test_a_departement_under_its_producer_floor_is_refused(env, monkeypatch, tmp_path):
    serve_exports(monkeypatch, tmp_path / "base", mini_exports())
    assert run()[1].success
    before = _state(env)
    # 85 publishes 17 rows of the mini-export: 80 % of 1 000 is 800.
    comptes = {"2A": 1, "44": 2, "85": 1_000}
    monkeypatch.setitem(
        floors.PRODUCER_COUNTS,
        TABLE,
        {"lignes_importees": comptes, "lignes_stockees": comptes},
    )

    _pipeline, result = run()

    assert not result.success
    assert "Department volume: 85 has 17 rows" in result.error
    assert _state(env) == before


# --- D8 on the real SQL of the theme (review Opus 3) --------------------------

PARCELLE_ANNEE_NULL = "85191000RF0006"  # 11 groups of 1975, 1 without year
PARCELLE_MEME_ANNEE = "85191000RD0004"  # 12 groups of 1990 (mini-export)


def _douze_dont_une_inconnue(exports):
    groupes = [f"bdnb-bg-85F0-0000-00{i:02d}" for i in range(1, 13)]
    dep85 = exports["85"]
    return {
        **exports,
        "85": replace(
            dep85,
            relations=(
                *dep85.relations,
                *((g, PARCELLE_ANNEE_NULL, "1") for g in groupes),
            ),
            ffo=(
                *dep85.ffo,
                *((g, "1975", "BETON", "TUILES") for g in groupes[:11]),
                (groupes[11], "", "BETON", "TUILES"),
            ),
        ),
    }


@pytest.fixture
def d8_charge(env, monkeypatch, tmp_path):
    """The mini-export plus a parcel of 12 groups, one without year, loaded by
    the runner. Sync: the pipeline downloads with ``asyncio.run``."""
    serve_exports(
        monkeypatch, tmp_path / "producteur", _douze_dont_une_inconnue(mini_exports())
    )
    _pipeline, result = run()
    assert result.success, result.error
    return env


def _contexte_85(session, parcelle):
    from datetime import date

    from app.adresse.relations import PointBan
    from app.adresse.schemas import (
        CommuneRef,
        DepartementRef,
        IdentiteAdresse,
        RelationParcelle,
    )
    from app.common.schemas import DatasetHosting
    from app.rapport.socle import ContexteRapport, Memo
    from app.scope.dependencies import RouteHosting
    from app.scope.model import InstanceScope

    return ContexteRapport(
        session=session,
        identite=IdentiteAdresse(
            ban_id="85191_0799_00001",
            libelle="1 Rue du Rapport, 85000 La Roche-sur-Yon",
            numero="1",
            nom_voie="Rue du Rapport",
            code_postal="85000",
            commune=CommuneRef(code_insee="85191", nom="La Roche-sur-Yon"),
            departement=DepartementRef(code="85", nom="Vendée"),
            parcelle=RelationParcelle(
                etat="unique",
                id_parcelle=parcelle,
                nombre_candidats=1,
                rattachement="parcelle_du_point",
            ),
        ),
        point=PointBan(lon=-1.4, lat=46.6),
        hosting=RouteHosting(
            {"bdnb": DatasetHosting(etat="heberge", departements=["44", "85"])}
        ),
        scope=InstanceScope(frozenset({"bdnb"}), DEPARTEMENTS),
        as_of=date(2026, 11, 2),
        memo=Memo(),
    )


async def test_d8_counts_every_group_of_the_parcel_on_the_real_sql(d8_charge):
    """D8 on PostgreSQL: 12 groups are counted, 10 are listed, the list says
    it is incomplete; one year shared by all 12 is served, a 12th group
    without year among 11 of 1975 is a gap with 12 candidates.

    Mutation it must catch: in ``_GROUPES_DE_LA_PARCELLE``, the summary read
    from the ten listed groups (``FROM (SELECT * FROM groupes ORDER BY
    parcelle_principale DESC, batiment_groupe_id LIMIT 10) g`` instead of
    ``FROM groupes`` in ``resume``): total 10, list complete, and the NULL year
    of the 12th group unseen.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.rapport.themes import construction

    engine = create_async_engine(
        d8_charge.replace("postgresql://", "postgresql+asyncpg://")
    )
    try:
        async with async_sessionmaker(engine)() as session:
            inconnue = await construction.lire_import(session, PARCELLE_ANNEE_NULL)
            meme = await construction.lire_import(session, PARCELLE_MEME_ANNEE)
            theme_inconnue = await construction.lire(
                _contexte_85(session, PARCELLE_ANNEE_NULL)
            )
            theme_meme = await construction.lire(
                _contexte_85(session, PARCELLE_MEME_ANNEE)
            )
    finally:
        await engine.dispose()

    assert (inconnue.total, len(inconnue.lignes)) == (12, 10)
    assert inconnue.annees == frozenset({1975, None})
    assert (meme.total, len(meme.lignes)) == (12, 10)
    assert meme.annees == frozenset({1990})

    for theme in (theme_inconnue, theme_meme):
        assert len(theme.details["groupes"]) == 10
        assert theme.details["liste_incomplete"] is True
        assert theme.rattachement.candidats == 12
    assert (theme_inconnue.etat, theme_inconnue.motif) == (
        "manque",
        "plusieurs_batiments_sur_la_parcelle",
    )
    assert theme_inconnue.rattachement.confiance == "incertaine"
    assert theme_inconnue.verdict.startswith("La BDNB recense 12 bâtiments")
    assert (theme_meme.etat, theme_meme.chiffres[0].valeur) == ("ok", 1990)
    assert theme_meme.rattachement.motif == "meme_annee_pour_tous"
    assert theme_meme.sources[0].millesime == LABEL


# -- Publication guard of the reader (04.7-19 review, Opus P2) ----------------


def _latest_publication(dsn) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT status FROM etl_publications WHERE table_name=%s "
            "ORDER BY id DESC LIMIT 1",
            (TABLE,),
        ).fetchone()[0]


def _phase_c_failed(self, dsn, old_table_name):
    raise RuntimeError("synthetic failure after the swap")


@pytest.fixture
def echange_non_acte(env, monkeypatch, tmp_path):
    """A published import, then a second one whose swap succeeds and whose
    publication fails right after (``register_old_table``, end of phase C):
    the swapped table stays in place, its publication is ``failed``."""
    serve_exports(monkeypatch, tmp_path / "base", mini_exports())
    assert run()[1].success
    before = published(env)
    serve_exports(
        monkeypatch, tmp_path / "suite", _douze_dont_une_inconnue(mini_exports())
    )
    monkeypatch.setattr(
        BdnbBatimentParcellePipeline, "register_old_table", _phase_c_failed
    )
    _pipeline, result = run()
    assert not result.success
    # The hole itself: the swapped generation is in production, not acted.
    assert published(env) != before
    assert _latest_publication(env) == "failed"
    return env


@pytest.fixture
def refus_avant_echange(env, monkeypatch, tmp_path):
    """A published import, then a second one refused before the swap (D13):
    production is untouched, the last publication is ``failed``."""
    serve_exports(monkeypatch, tmp_path / "base", mini_exports())
    assert run()[1].success
    serve_exports(monkeypatch, tmp_path / "refus", _diverge(mini_exports()))
    assert not run()[1].success
    assert _latest_publication(env) == "failed"
    return env


async def _theme(dsn, parcelle):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.rapport.themes import construction

    engine = create_async_engine(dsn.replace("postgresql://", "postgresql+asyncpg://"))
    try:
        async with async_sessionmaker(engine)() as session:
            return await construction.lire(_contexte_85(session, parcelle))
    finally:
        await engine.dispose()


async def test_a_swapped_table_whose_publication_failed_is_not_read(echange_non_acte):
    """The theme never serves a generation whose publication was not acted:
    an outage of the block (``publication_non_validee``), never its values.

    Mutation it must catch: the publication guard of ``lire_bloc`` removed
    (the theme then reads the swapped table and answers ``ok``)."""
    theme = await _theme(echange_non_acte, PARCELLE_MEME_ANNEE)
    assert (theme.etat, theme.motif) == ("panne", "publication_non_validee")
    assert theme.chiffres == []


async def test_a_run_refused_before_the_swap_keeps_the_theme_readable(
    refus_avant_echange,
):
    """A refusal before the swap leaves the published table in place: it is
    still the acted one, the theme reads it."""
    theme = await _theme(refus_avant_echange, PARCELLE_MEME_ANNEE)
    assert (theme.etat, theme.chiffres[0].valeur) == ("ok", 1990)
