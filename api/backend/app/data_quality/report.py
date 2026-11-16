"""Markdown report generation for audit results."""

from datetime import datetime
from pathlib import Path

from app.data_quality.base import AuditResult, Finding

SEVERITY_EMOJI = {"ERROR": "[X]", "WARN": "[!]", "INFO": "[i]"}


def _render_finding(f: Finding) -> str:
    lines = [
        f"### {SEVERITY_EMOJI[f.severity]} {f.title}",
        "",
        f"**Categorie** : {f.category}",
    ]
    if f.count is not None:
        lines.append(f"**Volume** : {f.count:,} lignes".replace(",", " "))
    lines += ["", f.description, ""]
    if f.sample_query:
        lines += [
            "**Requete de reproduction** :",
            "",
            "```sql",
            f.sample_query.strip(),
            "```",
            "",
        ]
    if f.fix_hint:
        lines += ["**Proposition de fix** :", "", f.fix_hint, ""]
    return "\n".join(lines)


def render_dataset_report(result: AuditResult) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = [
        f"# Audit dataset : {result.dataset}",
        "",
        f"Date : {timestamp}",
        f"Table : `{result.table}`",
        f"Lignes totales : **{result.row_count:,}**".replace(",", " "),
        "",
        "## Resume",
        "",
        f"- Erreurs : {len(result.errors)}",
        f"- Avertissements : {len(result.warnings)}",
        f"- Infos : {len(result.infos)}",
        "",
    ]

    if result.stats:
        header += ["## Statistiques", ""]
        for key, value in result.stats.items():
            header.append(f"- **{key}** : {value}")
        header.append("")

    if not result.findings:
        header += ["## Findings", "", "Aucune anomalie detectee.", ""]
        return "\n".join(header)

    sections: list[str] = ["## Findings", ""]
    for severity in ("ERROR", "WARN", "INFO"):
        group = [f for f in result.findings if f.severity == severity]
        if not group:
            continue
        sections += [f"### {severity} ({len(group)})", ""]
        sections += [_render_finding(f) for f in group]

    return "\n".join(header + sections)


def render_summary(results: list[AuditResult]) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Audit global DataCommune",
        "",
        f"Date : {timestamp}",
        f"Datasets audites : {len(results)}",
        "",
        "## Tableau de bord",
        "",
        "| Dataset | Lignes | Erreurs | Warnings | Infos |",
        "|---------|-------:|--------:|---------:|------:|",
    ]
    for r in results:
        lines.append(  # noqa: PERF401
            f"| [{r.dataset}]({r.dataset}.md) "
            f"| {r.row_count:,} "
            f"| {len(r.errors)} "
            f"| {len(r.warnings)} "
            f"| {len(r.infos)} |".replace(",", " ")
        )

    lines += ["", "## Top erreurs", ""]
    any_error = False
    for r in results:
        for f in r.errors:
            any_error = True
            lines.append(f"- **{r.dataset}** : {f.title}")
    if not any_error:
        lines.append("Aucune erreur.")
    return "\n".join(lines) + "\n"


def write_report(result: AuditResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.dataset}.md"
    path.write_text(render_dataset_report(result), encoding="utf-8")
    return path


def write_summary(results: list[AuditResult], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "AUDIT-SUMMARY.md"
    path.write_text(render_summary(results), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Matrix rendering — DQ-CORE-03, D-06, D-07, D-08
# ---------------------------------------------------------------------------


def _cell_verdict(result: AuditResult, dimension: str) -> str:
    """Compute matrix cell emoji for one (dataset, dimension) pair.

    Returns:
        "—"  if dimension was not run (not in dimensions_run)
        "🔴" if ≥1 Finding with severity=ERROR and category==dimension
        "🟠" if ≥1 Finding with severity=WARN (and no ERROR) for that category
        "🟢" if dimension was run and produced 0 findings in that category
    """
    if dimension not in result.dimensions_run:
        return "—"
    cat_findings = [f for f in result.findings if f.category == dimension]
    if any(f.severity == "ERROR" for f in cat_findings):
        return "🔴"
    if any(f.severity == "WARN" for f in cat_findings):
        return "🟠"
    return "🟢"


def render_matrix(results: list[AuditResult]) -> str:
    """Render datasets x dimensions matrix with emoji verdicts.

    Produces a Markdown table with one row per dataset and one column per
    dimension (sorted union of all dimensions_run across all results).
    Written to MATRIX.md by write_matrix().
    """
    all_dims: list[str] = sorted({d for r in results for d in r.dimensions_run})
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

    dims_label = ", ".join(all_dims) if all_dims else "aucune"
    lines = [
        "# Matrice data-quality",
        "",
        f"Date : {timestamp}",
        f"Datasets : {len(results)} | Dimensions : {dims_label}",
        "",
    ]

    if not all_dims:
        # Degenerate case: no dimensions run at all — still produce valid table
        lines += [
            "| Dataset |",
            "|---------|",
        ]
        for r in results:
            lines.append(f"| {r.dataset} |")
    else:
        header_cols = " | ".join(all_dims)
        sep_cols = " | ".join("---" for _ in all_dims)
        lines += [
            f"| Dataset | {header_cols} |",
            f"|---------|{sep_cols}|",
        ]
        for r in results:
            cells = " | ".join(_cell_verdict(r, d) for d in all_dims)
            lines.append(f"| {r.dataset} | {cells} |")

    return "\n".join(lines) + "\n"


def write_matrix(results: list[AuditResult], out_dir: Path) -> Path:
    """Write the data-quality matrix to MATRIX.md in out_dir.

    Creates out_dir (and parents) if they do not exist.
    Returns the path to the written file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "MATRIX.md"
    path.write_text(render_matrix(results), encoding="utf-8")
    return path
