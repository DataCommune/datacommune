"""RED stub tests for conditional dimensions (DQ-COND-01..04).

Wave 0 — RED stubs. These tests fail at EXECUTION by ImportError on
check_temporal/check_duplicates/check_format/check_integrity (not yet
created in profiles.py). Collection stays GREEN because all imports of
those symbols are inside each test body, never at module top-level.

Contract fixed here for plans 02/04:
- DQ-COND-01: temporal — dates / integer years out of range → ERROR
- DQ-COND-02: duplicates — duplicate rows on natural key → ERROR
- DQ-COND-03: format — length / regex / enum violations → WARN
- DQ-COND-04: integrity — FK orphans vs geo_communes → WARN
"""

from unittest.mock import MagicMock

import pytest

from tests.data_quality.scopes import FULL_FRANCE_SCOPE, scope

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# DQ-COND-01 — temporal
# ---------------------------------------------------------------------------


def test_temporal_skipped_when_none() -> None:
    """check_temporal is not called when profile.temporal is None — no findings."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        check_temporal,
    )

    profile = DatasetProfile(dataset="equipements", table="equipements")
    # temporal=None by default — check_temporal must return [] immediately
    mock_conn = MagicMock()
    findings = check_temporal(mock_conn, profile)
    assert findings == [], (
        "check_temporal with temporal=None must return [] (N/A dimension). "
        f"Got: {findings}"
    )


def test_temporal_error_below_min() -> None:
    """date_col has rows below min_val → ERROR Finding (D-02)."""
    from datetime import date

    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        temporal=TemporalSpec(
            date_col="date_etablissement_dpe",
            min_val=date(2006, 1, 1),
            max_val=date.today(),
            col_type="date",
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        # First call = SET LOCAL — ignore; subsequent calls return count
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "<" in query_str or "min" in query_str.lower():
            # below-min check: simulate 5 rows before 2006-01-01
            mock_result.fetchone.return_value = (5,)
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    errors = [f for f in findings if f.severity == "ERROR" and f.category == "temporal"]
    assert len(errors) >= 1, (
        "dates below min_val must produce at least 1 ERROR (D-02). "
        f"Findings: {findings}"
    )


def test_temporal_green_in_range() -> None:
    """All dates within [min_val, max_val] → no findings (green)."""
    from datetime import date

    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="dvf",
        table="mutations",
        temporal=TemporalSpec(
            date_col="date_mutation",
            min_val=date(2014, 1, 1),
            max_val=date.today(),
            col_type="date",
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)  # no out-of-range rows
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    assert findings == [], (
        f"All dates in range must produce 0 findings (green). Got: {findings}"
    )


def test_temporal_future_ok_when_max_none() -> None:
    """max_val=None → no upper-bound check, future dates allowed → green (D-02)."""
    from datetime import date

    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="permis",
        table="permis",
        temporal=TemporalSpec(
            date_col="date_decision",
            min_val=date(2010, 1, 1),
            max_val=None,  # future dates are legitimate
            col_type="date",
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)  # 0 rows below min
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    # With max_val=None the checker must NOT emit any finding for future dates
    assert findings == [], (
        "max_val=None means future dates are allowed — no ERROR must be raised. "
        f"Got: {findings}"
    )


def test_temporal_year_int_error() -> None:
    """col_type='year_int': annee < min_year → ERROR (D-02, Pitfall 1)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="copropriete",
        table="copropriete",
        temporal=TemporalSpec(
            date_col="annee_reglement",
            min_val=1950,
            max_val=None,
            col_type="year_int",
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            # Simulate 3 rows with annee_reglement < 1950
            mock_result.fetchone.return_value = (3,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    errors = [f for f in findings if f.severity == "ERROR" and f.category == "temporal"]
    assert len(errors) >= 1, (
        "year_int col with values below min_year must produce ERROR (D-02, Pitfall 1). "
        f"Findings: {findings}"
    )


# ---------------------------------------------------------------------------
# DQ-COND-02 — duplicates
# ---------------------------------------------------------------------------


def test_duplicates_skipped_when_none() -> None:
    """check_duplicates with duplicates=None → [] (N/A dimension)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        check_duplicates,
    )

    profile = DatasetProfile(dataset="equipements", table="equipements")
    mock_conn = MagicMock()
    findings = check_duplicates(mock_conn, profile)
    assert findings == [], (
        f"check_duplicates with duplicates=None must return [] (N/A). Got: {findings}"
    )


def test_duplicates_error_on_dup() -> None:
    """count of dup groups > 0 → ERROR Finding (D-05)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        DuplicatesSpec,
        check_duplicates,
    )

    profile = DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        duplicates=DuplicatesSpec(natural_key=("numero_dpe",)),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "GROUP BY" in query_str.upper():
            # 3 duplicate groups found
            mock_result.fetchone.return_value = (3,)
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_duplicates(mock_conn, profile)
    errors = [
        f for f in findings if f.severity == "ERROR" and f.category == "duplicates"
    ]
    assert len(errors) >= 1, (
        "Duplicate groups on natural key must produce ERROR (D-05). "
        f"Findings: {findings}"
    )


def test_duplicates_green_unique() -> None:
    """0 duplicate groups → no findings (green)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        DuplicatesSpec,
        check_duplicates,
    )

    profile = DatasetProfile(
        dataset="zonage_abc",
        table="zonage_abc",
        duplicates=DuplicatesSpec(natural_key=("code_insee",)),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)  # no dup groups
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_duplicates(mock_conn, profile)
    assert findings == [], (
        f"0 duplicate groups must produce 0 findings (green). Got: {findings}"
    )


# ---------------------------------------------------------------------------
# DQ-COND-03 — format
# ---------------------------------------------------------------------------


def test_format_skipped_empty() -> None:
    """format_rules=() (empty tuple, falsy) → [] (N/A dimension)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        check_format,
    )

    profile = DatasetProfile(dataset="batiments", table="batiments")
    # format_rules defaults to () — falsy
    mock_conn = MagicMock()
    findings = check_format(mock_conn, profile)
    assert findings == [], (
        f"check_format with empty format_rules must return [] (N/A). Got: {findings}"
    )


def test_format_warn_bad_postal() -> None:
    r"""code_postal with length != 5 (or not matching ^\d{5}$) → WARN (D-04)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        FormatRule,
        check_format,
    )

    profile = DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        format_rules=(
            FormatRule(col="code_postal", rule_type="regex", value=r"^\d{5}$"),
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "NOT" in query_str.upper() and (
            "length" in query_str.lower()
            or "~" in query_str
            or "NOT IN" in query_str.upper()
        ):
            # 12 rows with bad postal codes
            mock_result.fetchone.return_value = (12,)
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_format(mock_conn, profile)
    warns = [f for f in findings if f.severity == "WARN" and f.category == "format"]
    assert len(warns) >= 1, (
        f"Rows not matching postal regex must produce WARN (D-04). Findings: {findings}"
    )


def test_format_warn_bad_enum() -> None:
    """etiquette_dpe value outside enum → WARN (D-04)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        FormatRule,
        check_format,
    )

    profile = DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        format_rules=(
            FormatRule(
                col="etiquette_dpe",
                rule_type="enum",
                value=("A", "B", "C", "D", "E", "F", "G", "N"),
            ),
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "NOT IN" in query_str.upper():
            # 7 rows with invalid etiquette
            mock_result.fetchone.return_value = (7,)
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_format(mock_conn, profile)
    warns = [f for f in findings if f.severity == "WARN" and f.category == "format"]
    assert len(warns) >= 1, (
        f"Values outside enum must produce WARN (D-04). Findings: {findings}"
    )


# ---------------------------------------------------------------------------
# DQ-COND-04 — integrity
# ---------------------------------------------------------------------------


def test_integrity_skipped_when_none() -> None:
    """check_integrity with integrity=None → [] (N/A dimension)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        check_integrity,
    )

    profile = DatasetProfile(dataset="batiments", table="batiments")
    mock_conn = MagicMock()
    findings = check_integrity(mock_conn, profile)
    assert findings == [], (
        f"check_integrity with integrity=None must return [] (N/A). Got: {findings}"
    )


def test_integrity_warn_above_threshold() -> None:
    """orphan_pct > warn_threshold_pct → WARN Finding (D-03/D-03b)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        FkSpec,
        check_integrity,
    )

    profile = DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        integrity=FkSpec(
            fk_col="code_insee",
            ref_table="geo_communes",
            ref_col="code",
            warn_threshold_pct=2.0,
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "LEFT JOIN" in query_str.upper():
            # total=1000, orphans=30 → 3.0% > 2.0% threshold → WARN
            mock_result.fetchone.return_value = (1000, 30)
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_integrity(mock_conn, profile)
    warns = [f for f in findings if f.severity == "WARN" and f.category == "integrity"]
    assert len(warns) >= 1, (
        "orphan_pct (3.0%) > warn_threshold_pct (2.0%) must produce WARN (D-03b). "
        f"Findings: {findings}"
    )


def test_integrity_green_below_threshold() -> None:
    """orphan_pct <= warn_threshold_pct → no findings (green) (D-03b)."""
    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        FkSpec,
        check_integrity,
    )

    profile = DatasetProfile(
        dataset="equipements",
        table="equipements",
        integrity=FkSpec(
            fk_col="code_commune",
            ref_table="geo_communes",
            ref_col="code",
            warn_threshold_pct=2.0,
        ),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "LEFT JOIN" in query_str.upper():
            # total=1000, orphans=10 → 1.0% <= 2.0% threshold → green
            mock_result.fetchone.return_value = (1000, 10)
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_integrity(mock_conn, profile)
    assert findings == [], (
        "orphan_pct (1.0%) <= warn_threshold_pct (2.0%) must produce 0 findings"
        f" (green). Got: {findings}"
    )


# ---------------------------------------------------------------------------
# Dispatch — run.py conditional dispatch
# ---------------------------------------------------------------------------


def test_run_dispatch_all_dims() -> None:
    """All 4 conditional specs present → all 4 dims in dimensions_run after audit."""
    from datetime import date

    from app.data_quality.profiles import (  # RED: symbols not yet created
        DatasetProfile,
        DuplicatesSpec,
        FkSpec,
        FormatRule,
        TemporalSpec,
    )
    from app.data_quality.run import _audit_profile  # RED: function not yet extended

    profile = DatasetProfile(
        dataset="dpe",
        table="diagnostics_dpe",
        temporal=TemporalSpec(
            date_col="date_etablissement_dpe",
            min_val=date(2006, 1, 1),
            max_val=date.today(),
            col_type="date",
        ),
        duplicates=DuplicatesSpec(natural_key=("numero_dpe",)),
        format_rules=(FormatRule(col="code_postal", rule_type="length", value=5),),
        integrity=FkSpec(fk_col="code_insee"),
    )

    mock_conn = MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif "LEFT JOIN" in query_str.upper():
            mock_result.fetchone.return_value = (1000, 10)  # total, orphans
        elif "GROUP BY" in query_str.upper():
            mock_result.fetchone.return_value = (0,)  # no dups
        else:
            mock_result.fetchone.return_value = (0,)  # no violations
        return mock_result

    mock_conn.execute.side_effect = side_effect

    _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)
    for dim in ("temporal", "duplicates", "format", "integrity"):
        assert dim in dims_run, (
            f"dim '{dim}' must be in dimensions_run when spec is present. "
            f"dimensions_run: {dims_run}"
        )


def test_run_dispatch_skips_na() -> None:
    """No conditional specs → none of the 4 conditional dims in dimensions_run."""
    from app.data_quality.profiles import DatasetProfile
    from app.data_quality.run import _audit_profile  # RED: function not yet extended

    profile = DatasetProfile(dataset="batiments", table="batiments")
    mock_conn = MagicMock()
    mock_conn.execute.return_value.fetchone.return_value = (0,)

    _result, dims_run = _audit_profile(mock_conn, profile, FULL_FRANCE_SCOPE)
    for dim in ("temporal", "duplicates", "format", "integrity"):
        assert dim not in dims_run, (
            f"dim '{dim}' must NOT be in dimensions_run when spec is None/empty. "
            f"dimensions_run: {dims_run}"
        )


# ---------------------------------------------------------------------------
# Frozen dataclasses — immutability guarantee
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CAL-01 — antennes temporal 2 faces (D-02, D-06, P29)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# CAL-01 — copropriete temporal 2 faces (D-03/D-07, P29)
# Diagnostic Task 1: distribution 1900-1949 = legitimate pre-war regulations
# Decision: widen min_val from 1950 to 1900
# ---------------------------------------------------------------------------


def test_copropriete_temporal_legitimate_year_no_finding() -> None:
    """Accept a legitimate copropriete year in the configured range.

    D-03/D-06 P29: pre-1950 règlements de copropriété (1900-1949) are legitimate
    historical data (verified by SQL diagnostic: natural distribution, not 9999/1900
    mass placeholders). min_val widened from 1950 to 1900.
    """
    from app.data_quality.profiles import (
        _CURRENT_YEAR,
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="copropriete",
        table="copropriete",
        temporal=TemporalSpec(
            date_col="annee_reglement",
            min_val=1900,
            max_val=_CURRENT_YEAR,
            col_type="year_int",
        ),
    )
    mock_conn = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)  # 0 rows out of range
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    assert findings == [], (
        "annee_reglement in [1900, CURRENT_YEAR] must produce 0 findings. "
        f"Got: {findings}"
    )


def test_copropriete_temporal_aberrant_year_still_flags() -> None:
    """copropriete extreme future year (e.g. 9182) → WARN Finding (face aberrante).

    D-06 P29 verrou anti-complaisance: widening min_val to 1900 + severity=warn must
    not MASK placeholder/corrupt values like 9182 > _CURRENT_YEAR — they must still
    produce a finding (WARN, not silent). Confirmed in diagnostic: 169 future rows,
    32 pré-1900 = genuine RNE source anomalies (0.03%). D-05 P29: severity=warn because
    source defect is upstream (RNE), cannot be fixed, matrice honnête 🟠 > 🔴 bloquant.
    """
    from app.data_quality.profiles import (
        _CURRENT_YEAR,
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="copropriete",
        table="copropriete",
        temporal=TemporalSpec(
            date_col="annee_reglement",
            min_val=1900,
            max_val=_CURRENT_YEAR,
            col_type="year_int",
            severity="warn",  # D-05 P29: genuine RNE source defect
        ),
    )
    mock_conn = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif ">" in query_str:
            mock_result.fetchone.return_value = (4,)  # 4 rows with extreme future years
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    # Aberrant values must still produce a WARN finding, never be silenced.
    flagged = [f for f in findings if f.category == "temporal"]
    assert len(flagged) >= 1, (
        "Extreme future annee_reglement (e.g. 9182) must still produce a WARN finding "
        "(verrou anti-complaisance D-06 — not silent even with severity=warn). "
        f"Findings: {findings}"
    )


# ---------------------------------------------------------------------------
# CAL-01 — permis temporal 2 faces (D-04/D-07, P29)
# Diagnostic Task 1: 2 rows (2005-11-22, 2009-10-01) — legitimate permits
# Decision: widen min_val from 2010-01-01 to 2005-01-01
# ---------------------------------------------------------------------------


def test_permis_temporal_legitimate_date_no_finding() -> None:
    """permis date_depot in [2005-01-01, ...] → no finding (face légitime).

    D-04/D-06 P29: 2 permit rows from 2005 and 2009 are legitimate historical
    permits (Saint-Denis 93072, verified by SQL diagnostic). min_val widened
    from 2010-01-01 to 2005-01-01.
    """
    from app.data_quality.profiles import (
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="permis",
        table="permis",
        temporal=TemporalSpec(
            date_col="date_depot",
            min_val=__import__("datetime").date(2005, 1, 1),
            max_val=None,  # future deposit dates are legitimate
        ),
    )
    mock_conn = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)  # 0 rows below 2005-01-01
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    assert findings == [], (
        "date_depot >= 2005-01-01 must produce 0 findings (face legitime). "
        f"Got: {findings}"
    )


def test_permis_volume_warn_severity() -> None:
    """permis VolumeSpec severity='warn' → below-floor volume Finding is WARN.

    Uses the REAL permis profile (D-11 P29 + CR-01 fix): full_france_min must scale,
    via effective_volume_floor, to a meaningful dept85 effective floor. A row count
    BELOW that real floor must emit a finding, and severity='warn' makes it 🟠 (the
    SITADEL source is quarantined — a green here would be dishonest). Pinning to the
    real profile prevents a fabricated floor from masking a 102x-too-low regression.
    """
    from unittest.mock import MagicMock

    from app.data_quality.profiles import (
        PROFILES,
        check_volume,
        effective_volume_floor,
    )

    profile = PROFILES["permis"]
    assert profile.volume is not None, "permis profile must declare a VolumeSpec"
    assert profile.volume.severity == "warn", (
        "permis VolumeSpec must keep severity='warn' (D-11 P29 SITADEL quarantaine)"
    )

    # Real effective floor at dept85 scope — must be meaningful (CR-01: not ~5).
    effective_floor = effective_volume_floor(profile.volume, scope("85"), "permis")
    assert effective_floor >= 400, (
        f"permis effective dept85 floor collapsed to {effective_floor} — CR-01 "
        "regression: full_france_min must scale to a real floor, not ~5"
    )

    # Mock a count BELOW the real floor → must emit a finding.
    below_floor = effective_floor - 100
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (below_floor,)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cur)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_ctx

    _row_count, findings = check_volume(mock_conn, profile, scope("85"))

    vol_findings = [f for f in findings if f.category == "volume"]
    assert len(vol_findings) >= 1, (
        f"count {below_floor} below floor {effective_floor} must emit a finding. "
        f"Findings: {findings}"
    )
    assert all(f.severity == "WARN" for f in vol_findings), (
        "permis severity='warn' must make below-floor findings WARN not ERROR "
        f"(D-11 P29). Findings: {findings}"
    )


def test_permis_source_known_partial_present_warns() -> None:
    """Face A: source_known_partial=True + count ABOVE floor (727 > 582) → 🟠 WARN.

    Without the flag, count 727 > floor 582 would emit 0 findings → 🟢.
    This test verifies that the flag forces WARN regardless of the count being
    above the floor — structurally signalling the SITADEL source partiality (D-06
    P30). The test fails if the source_known_partial branch is removed.
    """
    from unittest.mock import MagicMock

    from app.data_quality.profiles import PROFILES, check_volume

    profile = PROFILES["permis"]
    assert profile.volume is not None, "permis profile must declare a VolumeSpec"
    assert profile.volume.source_known_partial is True, (
        "permis VolumeSpec must set source_known_partial=True (D-06 P30)"
    )

    # Mock count ABOVE the floor (727 dept85 real count) — without the flag this
    # returns 0 findings → 🟢.  With the flag it must emit ≥1 WARN finding.
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (727,)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cur)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_ctx

    _row_count, findings = check_volume(mock_conn, profile, scope("85"))

    vol_findings = [f for f in findings if f.category == "volume"]
    assert len(vol_findings) >= 1, (
        f"source_known_partial=True must emit ≥1 volume finding even when count "
        f"727 > floor. Without the flag: 0 findings (🟢). Findings: {findings}"
    )
    assert all(f.severity == "WARN" for f in vol_findings), (
        "source_known_partial finding must be WARN (🟠), not ERROR. "
        f"Findings: {findings}"
    )


def test_permis_source_known_partial_empty_table_errors() -> None:
    """Face B (anti-complaisance verrou D-07): count=0 on permis → ERROR, never WARN.

    source_known_partial must NOT mask a dead/collapsed table — the flag only
    applies when total > 0. An empty table must still produce an ERROR finding,
    proving the flag cannot be used to whitewash a catastrophic data loss.
    """
    from unittest.mock import MagicMock

    from app.data_quality.profiles import PROFILES, check_volume

    profile = PROFILES["permis"]
    assert profile.volume is not None, "permis profile must declare a VolumeSpec"
    assert profile.volume.source_known_partial is True, (
        "This test assumes permis has source_known_partial=True (D-06 P30)"
    )

    # Mock count = 0 (empty / collapsed table).
    mock_cur = MagicMock()
    mock_cur.fetchone.return_value = (0,)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cur)
    mock_ctx.__exit__ = MagicMock(return_value=False)
    mock_conn = MagicMock()
    mock_conn.cursor.return_value = mock_ctx

    _row_count, findings = check_volume(mock_conn, profile, scope("85"))

    vol_findings = [f for f in findings if f.category == "volume"]
    assert len(vol_findings) >= 1, (
        "An empty permis table (count=0) must always emit ≥1 volume finding. "
        f"Findings: {findings}"
    )
    assert all(f.severity == "ERROR" for f in vol_findings), (
        "le flag ne doit pas masquer une table effondrée (D-07): "
        "empty table must emit ERROR, not WARN. "
        f"Findings: {findings}"
    )


def test_antennes_temporal_legitimate_date_no_finding() -> None:
    """Accept an antennes date inside the configured valid window.

    D-02/D-06 P29 verrou anti-complaisance: data in the valid window must not flag.
    """

    from app.data_quality.profiles import (  # type: ignore[attr-defined]
        _ANTENNES_MAX_DATE,
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="antennes",
        table="antennes",
        temporal=TemporalSpec(
            date_col="date_mise_en_service",
            min_val=__import__("datetime").date(1990, 1, 1),
            max_val=_ANTENNES_MAX_DATE,
        ),
    )
    mock_conn = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.fetchone.return_value = (0,)  # 0 rows out of range
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    assert findings == [], (
        f"Date within valid antennes window must produce 0 findings. Got: {findings}"
    )


def test_antennes_temporal_aberrant_date_above_max() -> None:
    """antennes date beyond _ANTENNES_MAX_DATE → ERROR Finding (face aberrante).

    D-02/D-06 P29 verrou: a date too far in the future (e.g. 2035) must still flag
    even after the window was widened to today + 2 years.
    """
    from app.data_quality.profiles import (  # type: ignore[attr-defined]
        _ANTENNES_MAX_DATE,
        DatasetProfile,
        TemporalSpec,
        check_temporal,
    )

    profile = DatasetProfile(
        dataset="antennes",
        table="antennes",
        temporal=TemporalSpec(
            date_col="date_mise_en_service",
            min_val=__import__("datetime").date(1990, 1, 1),
            max_val=_ANTENNES_MAX_DATE,
        ),
    )
    mock_conn = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()

    def side_effect(query, *args, **kwargs):
        mock_result = __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
        query_str = query if isinstance(query, str) else ""
        if "SET LOCAL" in query_str:
            mock_result.fetchone.return_value = None
        elif ">" in query_str:
            mock_result.fetchone.return_value = (1,)  # 1 row above max — aberrant
        else:
            mock_result.fetchone.return_value = (0,)
        return mock_result

    mock_conn.execute.side_effect = side_effect
    findings = check_temporal(mock_conn, profile)
    errors = [f for f in findings if f.severity == "ERROR" and f.category == "temporal"]
    assert len(errors) >= 1, (
        "Date beyond _ANTENNES_MAX_DATE must produce ERROR "
        "(verrou anti-complaisance D-06). "
        f"Findings: {findings}"
    )


def test_conditional_specs_are_frozen() -> None:
    """TemporalSpec, DuplicatesSpec, FormatRule, FkSpec are all frozen dataclasses."""
    from dataclasses import FrozenInstanceError
    from datetime import date

    from app.data_quality.profiles import (  # RED: symbols not yet created
        DuplicatesSpec,
        FkSpec,
        FormatRule,
        TemporalSpec,
    )

    ts = TemporalSpec(
        date_col="d", min_val=date(2006, 1, 1), max_val=None, col_type="date"
    )
    with pytest.raises(FrozenInstanceError):
        ts.date_col = "other"  # type: ignore[misc]

    ds = DuplicatesSpec(natural_key=("col1",))
    with pytest.raises(FrozenInstanceError):
        ds.natural_key = ("col2",)  # type: ignore[misc]

    fr = FormatRule(col="code_postal", rule_type="length", value=5)
    with pytest.raises(FrozenInstanceError):
        fr.col = "other"  # type: ignore[misc]

    fk = FkSpec(fk_col="code_insee")
    with pytest.raises(FrozenInstanceError):
        fk.fk_col = "other"  # type: ignore[misc]
