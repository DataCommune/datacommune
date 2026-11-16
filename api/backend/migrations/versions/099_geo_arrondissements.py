"""Municipal arrondissements of Paris, Lyon and Marseille (plan 02-11, WINDOWS 39).

``geo_communes`` holds the communes 75056, 69123 and 13055 only: a GPS point
in Paris resolved 75056, which ``expand_code_insee`` widens to 21 codes,
where the former resolution by ``parcelles`` returned the arrondissement.
``scripts/import_geo_boundaries.py`` loads the ``arrondissement-municipal``
contours of geo.api.gouv.fr here, and ``commune_at_point`` prefers the
arrondissement covering a point.

A dedicated table rather than a type column on ``geo_communes``: every reader
that lists or counts communes (``/v1/communes``, commune search, foncier_pm,
demographie backfill and checks, vector tiles) keeps reading communes only,
by construction.
"""

from alembic import op

revision = "099_geo_arrondissements"
down_revision = "098_commune_views_application_ddl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE geo_arrondissements (
            code VARCHAR(5) PRIMARY KEY,
            nom VARCHAR(200) NOT NULL,
            departement_code VARCHAR(3) NOT NULL,
            commune_code VARCHAR(5) NOT NULL
                CHECK (commune_code IN ('13055', '69123', '75056')),
            population INTEGER,
            geometry GEOMETRY(MultiPolygon, 4326) NOT NULL
        )
    """)
    op.execute(
        "CREATE INDEX idx_geo_arr_geom ON geo_arrondissements USING GIST (geometry)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS geo_arrondissements")
