"""Local synthetic projection only; no persistence, HTTP or real-stock dispatch.

The caller owns the transaction and lock. Private bindings are not response DTOs.
Only public_dump() may cross a future response boundary, never asdict(row).
"""

from calendar import monthrange
from dataclasses import dataclass, field
from datetime import date, datetime
from fractions import Fraction
from typing import Any
from uuid import UUID

import psycopg
from pydantic import TypeAdapter

from app.common.source_catalog import DATASET_CATALOG
from app.commune.indicator_context import (
    DVF_TERRITORIAL_UNQUALIFIED,
    DatasetContext,
    IndicatorBuildContext,
)
from app.commune.indicator_manifest import (
    Dataset,
    FrozenReceivedAnchor,
    ManifestRefused,
    QualifiedManifest,
    Scope,
    manifest_sha256,
)
from app.commune.indicator_schemas import (
    HOSTED_DEPARTEMENTS,
    ClasseDvf,
    FenetreIndicateur,
    IndicateurAbsent,
    IndicateurNombre,
    IndicateursDpe,
    IndicateursDvf,
    LivraisonDvfRecue,
    ResultatDpeIndicateur,
    StatistiquesFenetreDvf,
    commune_departement,
)
from app.commune.indicator_sql import query_dpe_indicators, query_dvf_indicators
from app.commune.indicators_dvf import WindowStatistic
from app.dvf.couverture import SEUIL_ND_COMMUNE
from app.etl.publication import ObservedCandidate

DVF_PROFILE = "dvf-canonical-windows-core.v1"
DVF_TREND_PROFILE = "dvf-canonical-window-median-change.v1"
DVF_TREND_QUALIFICATION = (
    "Évolution descriptive des médianes observées ; ce n'est pas un indice à "
    "qualité constante. La composition des ventes peut varier entre les fenêtres "
    "et les données peuvent être révisées."
)
DPE_PROFILE = "dpe-documentary-core.v1"
DPE_PUBLIC = (
    "dpe.document_count",
    "dpe.class_distribution",
    "dpe.fg_share",
    "dpe.mode",
    "dpe.ordinal_median",
)
_KINDS = ("residentiel_maison", "residentiel_appartement")


@dataclass(frozen=True)
class ProjectionRequest:
    calculated_at: datetime
    scope: Scope

    def __post_init__(self) -> None:
        if (
            not isinstance(self.calculated_at, datetime)
            or self.calculated_at.tzinfo is None
            or self.calculated_at.utcoffset() is None
            or self.scope not in {"synthetic", "preproduction", "publication"}
        ):
            raise ManifestRefused("invalid_projection_request")


@dataclass(frozen=True)
class ProjectionBinding:
    dataset: Dataset
    publication_id: int | None
    source_generation: UUID | None
    source_status: str | None
    relation_oids: tuple[tuple[str, int], ...]
    registry_id: int | None
    manifest_sha256: str | None
    snapshot_sha256: str | None
    cog_sha256: str
    cog_version: str
    scope: Scope
    checked_at: datetime
    calculated_at: datetime
    executed_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class CommuneIndicatorRow:
    code_insee: str
    indicateurs_dvf: IndicateursDvf | IndicateurAbsent
    indicateurs_dpe: IndicateursDpe | IndicateurAbsent
    dvf_binding: ProjectionBinding = field(repr=False)
    dpe_binding: ProjectionBinding = field(repr=False)

    def public_dump(self) -> dict[str, Any]:
        """Revalidate canonical public schemas and closed emitted absence reasons."""
        return {
            "code_insee": self.code_insee,
            "indicateurs_dvf": _public_block(self.indicateurs_dvf, "dvf"),
            "indicateurs_dpe": _public_block(self.indicateurs_dpe, "dpe"),
        }


_PUBLIC_ADAPTERS: dict[
    Dataset, TypeAdapter[IndicateursDvf | IndicateursDpe | IndicateurAbsent]
] = {
    "dvf": TypeAdapter(IndicateursDvf | IndicateurAbsent),
    "dpe": TypeAdapter(IndicateursDpe | IndicateurAbsent),
}
_PUBLIC_ABSENCES = frozenset(
    {
        ("not_qualified", "territorial_attribution_unqualified"),
        ("not_qualified", "source_generation_unqualified"),
        ("not_qualified", "indicator_policy_unqualified"),
        ("not_reported", "empty_sample"),
        ("insufficient_sample", "insufficient_sample"),
        ("zero_denominator", "known_class_denominator_zero"),
    }
)


def _check_absences(value: Any) -> None:
    if isinstance(value, dict):
        if (
            "etat" in value
            and "motif" in value
            and (value["etat"], value["motif"]) not in _PUBLIC_ABSENCES
        ):
            raise ManifestRefused("invalid_public_projection")
        for child in value.values():
            _check_absences(child)
    elif isinstance(value, list):
        for child in value:
            _check_absences(child)


def _public_block(
    block: IndicateursDvf | IndicateursDpe | IndicateurAbsent,
    dataset: Dataset,
) -> dict[str, Any]:
    try:
        result = (
            _PUBLIC_ADAPTERS[dataset]
            .validate_python(block.model_dump(mode="json"))
            .model_dump(mode="json")
        )
        _check_absences(result)
        return result
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise ManifestRefused("invalid_public_projection") from None


def _absent(motif: str, state: str = "not_qualified") -> IndicateurAbsent:
    return IndicateurAbsent.model_validate({"etat": state, "motif": motif})


def _supported(manifest: QualifiedManifest, concept: str, version: str) -> bool:
    decision = manifest.indicators[concept]
    return decision.status == "qualified" and decision.rule_version == version


def _decision(
    entry: DatasetContext, context: IndicatorBuildContext, request: ProjectionRequest
) -> QualifiedManifest | None:
    loaded = entry.decision
    if loaded is None:
        return None
    try:
        payload = loaded.manifest.model_dump(mode="json")
        manifest = QualifiedManifest.model_validate(payload)
        valid = (
            loaded.source == entry.source
            and not isinstance(entry.source, ObservedCandidate)
            and manifest.dataset_name == entry.dataset_name
            and manifest.source_generation == loaded.source.generation
            and entry.registry_identity == (loaded.registry_id, loaded.manifest_sha256)
            and manifest_sha256(payload) == entry.validated_decision_sha256
            and manifest.cog_sha256 == context.cog_sha256
            and manifest.cog_version == context.cog_version
            and manifest.scope == request.scope
            and manifest.qualified_at <= context.checked_at <= request.calculated_at
        )
    except (ValueError, TypeError, AttributeError):
        raise ManifestRefused("invalid_projection_decision") from None
    if not valid:
        raise ManifestRefused("invalid_projection_decision")
    return manifest


def _binding(
    entry: DatasetContext,
    context: IndicatorBuildContext,
    request: ProjectionRequest,
    versions: tuple[tuple[str, str], ...],
) -> ProjectionBinding:
    source, decision = entry.source, entry.decision
    return ProjectionBinding(
        entry.dataset_name,
        source.publication_id if source else None,
        source.generation if source else None,
        ("pending" if isinstance(source, ObservedCandidate) else "published")
        if source
        else None,
        source.relation_oids if source else (),
        decision.registry_id if decision else None,
        decision.manifest_sha256 if decision else None,
        decision.manifest.source_snapshot_sha256 if decision else None,
        context.cog_sha256,
        context.cog_version,
        request.scope,
        context.checked_at,
        request.calculated_at,
        versions,
    )


def _sources(dataset: Dataset) -> list[dict[str, Any]]:
    if dataset == "dvf":
        source = DATASET_CATALOG["dvf"]
        identities = [(source.nom, source.producteur, source.licence, source.url)]
    else:
        identities = [
            (
                "DPE logements existants depuis juillet 2021",
                "ADEME",
                "Licence Ouverte 2.0",
                "https://data.ademe.fr/datasets/dpe03existant",
            ),
            (
                "DPE logements neufs depuis juillet 2021",
                "ADEME",
                "Licence Ouverte 2.0",
                "https://data.ademe.fr/datasets/dpe02neuf",
            ),
        ]
    return [
        dict(
            nom=n,
            producteur=p,
            licence=licence,
            reference=r,
            date_publication_source=None,
            date_snapshot=None,
            date_import=None,
            couverture="unknown",
        )
        for n, p, licence, r in identities
    ]


def _previous(value: date) -> date:
    return value.replace(
        year=value.year - 1,
        day=min(value.day, monthrange(value.year - 1, value.month)[1]),
    )


def _dvf_cells(
    cells: tuple[WindowStatistic, ...], codes: tuple[str, ...], end: date
) -> dict[tuple[str, str, str], WindowStatistic]:
    middle = _previous(end)
    periods = {"previous": (_previous(middle), middle), "current": (middle, end)}
    expected = {
        (code, kind, period) for code in codes for kind in _KINDS for period in periods
    }
    result: dict[tuple[str, str, str], WindowStatistic] = {}
    for cell in cells:
        key = cell.commune, cell.kind, cell.period
        state = (
            "empty_sample"
            if cell.count == 0
            else "insufficient_sample"
            if cell.count < SEUIL_ND_COMMUNE
            else "value"
        )
        if (
            key not in expected
            or key in result
            or (cell.start_inclusive, cell.end_exclusive) != periods[cell.period]
            or type(cell.count) is not int
            or cell.count < 0
            or cell.median_state != state
            or cell.quantiles_status != "pending"
            or (
                state == "value"
                and (
                    not isinstance(cell.median_eur_m2, Fraction)
                    or cell.median_eur_m2 <= 0
                )
            )
            or (state != "value" and cell.median_eur_m2 is not None)
        ):
            raise ManifestRefused("invalid_dvf_projection")
        result[key] = cell
    if set(result) != expected:
        raise ManifestRefused("invalid_dvf_projection")
    return result


def _window(cell: WindowStatistic, median_supported: bool) -> StatistiquesFenetreDvf:
    median: IndicateurAbsent | IndicateurNombre
    if not median_supported:
        median = _absent("indicator_policy_unqualified")
    elif cell.median_state == "value":
        assert cell.median_eur_m2 is not None
        median = IndicateurNombre(etat="value", valeur=float(cell.median_eur_m2))
    else:
        median = _absent(
            cell.median_state,
            "not_reported" if cell.count == 0 else "insufficient_sample",
        )
    return StatistiquesFenetreDvf(
        fenetre=FenetreIndicateur(
            debut_inclus=cell.start_inclusive, fin_exclue=cell.end_exclusive
        ),
        effectif=cell.count,
        mediane=median,
        quantiles=_absent("indicator_policy_unqualified"),
    )


def _variation(
    previous: StatistiquesFenetreDvf,
    current: StatistiquesFenetreDvf,
    previous_cell: WindowStatistic,
    current_cell: WindowStatistic,
    *,
    supported: bool,
) -> tuple[IndicateurAbsent | IndicateurNombre, str | None]:
    if not supported:
        return _absent("indicator_policy_unqualified"), None
    for median in (previous.mediane, current.mediane):
        if isinstance(median, IndicateurAbsent):
            return median, None
    assert isinstance(previous.mediane, IndicateurNombre)
    assert isinstance(current.mediane, IndicateurNombre)
    before = previous_cell.median_eur_m2
    after = current_cell.median_eur_m2
    if not isinstance(before, Fraction) or not isinstance(after, Fraction):
        raise ManifestRefused("invalid_dvf_projection")
    if before <= 0:
        raise ManifestRefused("invalid_dvf_projection")
    return (
        IndicateurNombre(
            etat="value",
            valeur=float(100 * (after / before - 1)),
        ),
        DVF_TREND_QUALIFICATION,
    )


def _dvf(
    conn: psycopg.Connection,
    context: IndicatorBuildContext,
    request: ProjectionRequest,
    manifest: QualifiedManifest | None,
) -> tuple[dict[str, IndicateursDvf], tuple[tuple[str, str], ...]]:
    if manifest is None:
        return {}, ()
    if not _supported(manifest, "dvf.count", DVF_PROFILE):
        return {}, ()
    anchor = manifest.dvf_anchor
    if not isinstance(anchor, FrozenReceivedAnchor):
        return {}, ()
    public_anchor = LivraisonDvfRecue.model_validate(anchor.model_dump(mode="json"))
    end = public_anchor.periode_fin_exclue
    if end.year < 3 or _previous(_previous(end)) < public_anchor.periode_debut_inclus:
        raise ManifestRefused("invalid_projection_period")
    # Canonical order (a v2 anchor may list 85 before 44).
    lot = tuple(c for c in HOSTED_DEPARTEMENTS if c in public_anchor.departements)
    # The COG reference and the received lot cover the same departements:
    # a reference commune outside the lot would read as zero sales, a lot
    # departement outside the reference would go unchecked.
    if {commune_departement(c) for c in context.communes} != set(lot):
        raise ManifestRefused("dvf_lot_scope_mismatch")
    codes = tuple(c for c in context.communes if c not in DVF_TERRITORIAL_UNQUALIFIED)
    cells = _dvf_cells(
        query_dvf_indicators(
            conn,
            communes=codes,
            departments=lot,
            end_exclusive=end,
            observed_at=public_anchor.date_observation,
        ),
        codes,
        end,
    )
    median_supported = _supported(manifest, "dvf.median_eur_m2", DVF_PROFILE)
    trend_supported = median_supported and _supported(
        manifest, "dvf.trend_pct", DVF_TREND_PROFILE
    )
    versions = (
        (("dvf.count", DVF_PROFILE),)
        + ((("dvf.median_eur_m2", DVF_PROFILE),) if median_supported else ())
        + ((("dvf.trend_pct", DVF_TREND_PROFILE),) if trend_supported else ())
    )
    result = {}
    for code in codes:
        classes = {}
        for kind in _KINDS:
            previous_cell = cells[code, kind, "previous"]
            current_cell = cells[code, kind, "current"]
            previous = _window(previous_cell, median_supported)
            current = _window(current_cell, median_supported)
            variation, qualification = _variation(
                previous,
                current,
                previous_cell,
                current_cell,
                supported=trend_supported,
            )
            classes[kind] = ClasseDvf(
                precedente=previous,
                courante=current,
                variation_pct=variation,
                qualification_tendance=qualification,
            )
        result[code] = IndicateursDvf.model_validate(
            dict(
                version_contrat="1.1",
                version_population=DVF_PROFILE,
                date_calcul=request.calculated_at,
                generation=manifest.source_generation,
                provenance=_sources("dvf"),
                ancrage_temporel=public_anchor,
                population="mutations_residentielles_mono_local_hors_vefa_mono_commune",
                unite_prix="EUR/m2",
                methode_quantiles="percentile_cont",
                methode_variation="100*(mediane_courante/mediane_precedente-1)",
                **classes,
            )
        )
    return result, versions


def _dpe(
    conn: psycopg.Connection,
    context: IndicatorBuildContext,
    request: ProjectionRequest,
    manifest: QualifiedManifest | None,
) -> tuple[dict[str, IndicateursDpe], tuple[tuple[str, str], ...]]:
    if manifest is None or not all(
        _supported(manifest, c, DPE_PROFILE) for c in DPE_PUBLIC
    ):
        return {}, ()
    assert manifest.dpe_as_of is not None
    rows = query_dpe_indicators(
        conn, communes=context.communes, as_of_date=manifest.dpe_as_of
    )
    result = {}
    for row in rows:
        if (
            row.commune not in context.communes
            or row.commune in result
            or row.calculation.as_of_date != manifest.dpe_as_of
        ):
            raise ManifestRefused("invalid_dpe_projection")
        payload = ResultatDpeIndicateur.model_validate(
            row.calculation.result.model_dump(mode="json")
        )
        if isinstance(payload.part_fg_pct, IndicateurAbsent):
            # The state is validated; arbitrary source/registry prose is not public.
            payload.part_fg_pct = _absent(
                "known_class_denominator_zero", "zero_denominator"
            )
        result[row.commune] = IndicateursDpe.model_validate(
            dict(
                version_contrat="1.0",
                version_population=DPE_PROFILE,
                date_calcul=request.calculated_at,
                generation=manifest.source_generation,
                provenance=_sources("dpe"),
                date_evaluation=manifest.dpe_as_of,
                population="diagnostics_canoniques_disponibles_hors_traces_retirees",
                periode="bornes_etablissement_observees_non_exhaustives",
                methode_mediane="percentile_disc",
                methode_modes="toutes_classes_frequence_maximale_ordre_A_G",
                resultat=payload,
            )
        )
    if set(result) != set(context.communes):
        raise ManifestRefused("invalid_dpe_projection")
    return result, tuple((c, DPE_PROFILE) for c in DPE_PUBLIC)


def project_indicator_blocks(
    conn: psycopg.Connection,
    *,
    context: IndicatorBuildContext,
    request: ProjectionRequest,
) -> tuple[CommuneIndicatorRow, ...]:
    """Project once per enabled dataset; never convert technical errors to absence."""
    if not isinstance(context, IndicatorBuildContext) or not isinstance(
        request, ProjectionRequest
    ):
        raise ManifestRefused("invalid_projection_request")
    request = ProjectionRequest(request.calculated_at, request.scope)
    if request.scope != context.scope or request.scope not in {
        "synthetic",
        "preproduction",
    }:
        raise ManifestRefused("projection_scope_not_enabled")
    if request.calculated_at < context.checked_at:
        raise ManifestRefused("projection_predates_context")
    context.assert_current(conn)
    entries = {entry.dataset_name: entry for entry in context.datasets}
    if len(context.datasets) != 2 or set(entries) != {"dvf", "dpe"}:
        raise ManifestRefused("invalid_projection_context")
    manifests = {d: _decision(e, context, request) for d, e in entries.items()}
    dvf, dvf_versions = _dvf(conn, context, request, manifests["dvf"])
    dpe, dpe_versions = _dpe(conn, context, request, manifests["dpe"])
    # Recheck loaded object facts before constructing private bindings.
    for entry in entries.values():
        _decision(entry, context, request)
    rows = []
    for code in context.communes:
        dvf_value = dvf.get(code) or _absent(
            "territorial_attribution_unqualified"
            if code in DVF_TERRITORIAL_UNQUALIFIED
            else "source_generation_unqualified"
            if manifests["dvf"] is None
            else "indicator_policy_unqualified"
        )
        dpe_value = dpe.get(code) or _absent(
            "source_generation_unqualified"
            if manifests["dpe"] is None
            else "indicator_policy_unqualified"
        )
        rows.append(
            CommuneIndicatorRow(
                code,
                dvf_value,
                dpe_value,
                _binding(
                    entries["dvf"],
                    context,
                    request,
                    dvf_versions if code in dvf else (),
                ),
                _binding(
                    entries["dpe"],
                    context,
                    request,
                    dpe_versions if code in dpe else (),
                ),
            )
        )
    context.assert_current(conn)
    return tuple(rows)
