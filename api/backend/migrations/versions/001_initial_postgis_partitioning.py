"""Create partitioned adresses table with 102 departements.

Revision ID: 001_initial
Revises:
Create Date: 2026-02-08
"""

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "001_initial"
down_revision: str | None = None
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
    """Create partitioned adresses table with spatial index."""
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis;")

    # Create partitioned parent table
    op.execute("""
        CREATE TABLE adresses (
            id BIGSERIAL,
            code_departement VARCHAR(3) NOT NULL,
            numero VARCHAR(10),
            rep VARCHAR(5),
            nom_voie TEXT,
            code_postal VARCHAR(5),
            code_commune VARCHAR(5),
            nom_commune TEXT,
            position GEOMETRY(Point, 4326),
            PRIMARY KEY (id, code_departement)
        ) PARTITION BY LIST (code_departement);
    """)

    # Create 102 partitions (one per departement)
    for dept in DEPARTEMENTS:
        safe_name = dept.lower()
        op.execute(
            f"CREATE TABLE adresses_{safe_name} "
            f"PARTITION OF adresses FOR VALUES IN ('{dept}');"
        )

    # Default partition for unexpected codes
    op.execute("CREATE TABLE adresses_default PARTITION OF adresses DEFAULT;")

    # Spatial index on parent table (propagates to all partitions)
    op.execute("CREATE INDEX idx_adresses_position ON adresses USING GIST (position);")

    # Insert test data in 4 representative departements
    op.execute("""
        INSERT INTO adresses (code_departement, numero, rep, nom_voie, code_postal, code_commune, nom_commune, position)
        VALUES
            -- Paris (75)
            ('75', '1', NULL, 'Place de la Concorde', '75008', '75108', 'Paris', ST_SetSRID(ST_MakePoint(2.3211, 48.8656), 4326)),
            ('75', '10', NULL, 'Rue de Rivoli', '75001', '75101', 'Paris', ST_SetSRID(ST_MakePoint(2.3522, 48.8566), 4326)),
            ('75', '5', NULL, 'Avenue des Champs-Elysees', '75008', '75108', 'Paris', ST_SetSRID(ST_MakePoint(2.3065, 48.8698), 4326)),
            -- Marseille (13)
            ('13', '1', NULL, 'Quai du Port', '13002', '13202', 'Marseille', ST_SetSRID(ST_MakePoint(5.3698, 43.2965), 4326)),
            ('13', '80', NULL, 'La Canebiere', '13001', '13201', 'Marseille', ST_SetSRID(ST_MakePoint(5.3757, 43.2977), 4326)),
            -- Corse-du-Sud (2A)
            ('2A', '1', NULL, 'Cours Napoleon', '20000', '2A004', 'Ajaccio', ST_SetSRID(ST_MakePoint(8.7386, 41.9192), 4326)),
            ('2A', '18', NULL, 'Boulevard du Roi Jerome', '20000', '2A004', 'Ajaccio', ST_SetSRID(ST_MakePoint(8.7350, 41.9210), 4326)),
            -- Martinique (971)
            ('971', '1', NULL, 'Rue de la Liberte', '97200', '97209', 'Fort-de-France', ST_SetSRID(ST_MakePoint(-61.0588, 14.6161), 4326)),
            ('971', '25', NULL, 'Boulevard du General de Gaulle', '97200', '97209', 'Fort-de-France', ST_SetSRID(ST_MakePoint(-61.0614, 14.6037), 4326));
    """)


def downgrade() -> None:
    """Drop adresses table and all partitions."""
    op.execute("DROP TABLE IF EXISTS adresses CASCADE;")
