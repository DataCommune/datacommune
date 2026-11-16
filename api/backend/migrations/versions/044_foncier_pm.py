"""Create partitioned foncier_pm table for MAJIC personnes morales data.

Stores ~17M rows of legal entity property ownership (parcelles).
Partitioned by departement (102 partitions + default).

Revision ID: 044_foncier_pm
Revises: 043_dvf_plus_bonus
Create Date: 2026-03-18
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "044_foncier_pm"
down_revision: str = "043_dvf_plus_bonus"
branch_labels: str | None = None
depends_on: str | None = None

# All 102 departement codes:
# 01-19 (19), 2A+2B (2), 21-95 (75), 971-976 (6) = 102
DEPARTEMENTS = [
    *[f"{i:02d}" for i in range(1, 20)],  # 01-19
    "2A",
    "2B",  # Corse
    *[str(i) for i in range(21, 96)],  # 21-95
    *[str(i) for i in range(971, 977)],  # DOM: 971-976
]


def upgrade() -> None:
    """Create partitioned foncier_pm table with B-tree and trigram indexes."""
    # Create partitioned parent table
    op.execute("""
        CREATE TABLE foncier_pm (
            id BIGSERIAL,
            code_departement VARCHAR(3) NOT NULL,
            code_commune VARCHAR(5) NOT NULL,
            nom_commune TEXT,
            adresse TEXT,
            code_parcelle VARCHAR(14) NOT NULL,
            contenance_parcelle INTEGER,
            nature_culture VARCHAR(5),
            code_droit VARCHAR(2),
            numero_siren VARCHAR(15),
            groupe_personne SMALLINT,
            forme_juridique_abregee VARCHAR(10),
            denomination TEXT,
            PRIMARY KEY (id, code_departement)
        ) PARTITION BY LIST (code_departement);
    """)

    # Create 102 partitions (one per departement)
    for dept in DEPARTEMENTS:
        safe_name = dept.lower()
        op.execute(
            f"CREATE TABLE foncier_pm_{safe_name} "
            f"PARTITION OF foncier_pm FOR VALUES IN ('{dept}');"
        )

    # Default partition for unexpected codes
    op.execute(
        "CREATE TABLE foncier_pm_default PARTITION OF foncier_pm DEFAULT;"
    )

    # B-tree index on commune code (partition-pruning + commune queries)
    op.execute(
        "CREATE INDEX idx_foncier_pm_code_commune "
        "ON foncier_pm (code_commune);"
    )

    # B-tree index on cadastral reference (JOIN with parcelles table)
    op.execute(
        "CREATE INDEX idx_foncier_pm_code_parcelle "
        "ON foncier_pm (code_parcelle);"
    )

    # B-tree index on SIREN (filter by legal entity)
    op.execute(
        "CREATE INDEX idx_foncier_pm_numero_siren "
        "ON foncier_pm (numero_siren);"
    )

    # GIN trigram index on denomination (ILIKE search on entity name)
    # pg_trgm extension already enabled in migration 002
    op.execute(
        "CREATE INDEX idx_foncier_pm_denomination "
        "ON foncier_pm USING gin (denomination gin_trgm_ops);"
    )


def downgrade() -> None:
    """Drop foncier_pm table and all partitions."""
    op.execute("DROP TABLE IF EXISTS foncier_pm CASCADE;")
