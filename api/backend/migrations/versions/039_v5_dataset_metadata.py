"""Seed dataset_metadata for 17 missing datasets (10 v5.0 ETL + 7 proxies).

Revision ID: 039_v5_dataset_metadata
Revises: 038_commune_synthese
Create Date: 2026-03-14

Adds rows for v5.0 ETL datasets (with cron_schedule and freshness thresholds)
and proxy datasets (no schedule, status='proxy').  Uses ON CONFLICT to be
idempotent if rows already exist.
"""

revision = "039_v5_dataset_metadata"
down_revision = "038_commune_synthese"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # 10 v5.0 ETL datasets (status='disponible') + 7 proxy datasets (status='proxy')
    op.execute("""
        INSERT INTO dataset_metadata
            (dataset_name, status, cron_schedule, freshness_threshold_days)
        VALUES
            -- v5.0 ETL: monthly
            ('permis',             'disponible', '0 2 5 * *',          45),
            ('antennes',           'disponible', '0 3 5 * *',          45),
            -- v5.0 ETL: quarterly
            ('copropriete',        'disponible', '0 4 1 1,4,7,10 *',   135),
            -- v5.0 ETL: annual
            ('encadrement_loyers', 'disponible', '0 2 1 2 *',          545),
            ('fiscalite',          'disponible', '0 3 1 2 *',          545),
            ('zonage_abc',         'disponible', '0 4 1 1 *',          545),
            ('revenus',            'disponible', '0 2 15 6 *',         545),
            ('demographie',        'disponible', '0 3 15 1 *',         545),
            ('equipements',        'disponible', '0 4 15 2 *',         545),
            ('vacance',            'disponible', '0 2 15 2 *',         545),
            -- Proxy datasets (no ETL, no schedule)
            ('bdnb',               'proxy',      NULL,                  NULL),
            ('sirene',             'proxy',      NULL,                  NULL),
            ('georisques',         'proxy',      NULL,                  NULL),
            ('errial',             'proxy',      NULL,                  NULL),
            ('urbanisme',          'proxy',      NULL,                  NULL),
            ('rnb',                'proxy',      NULL,                  NULL),
            ('scoring',            'proxy',      NULL,                  NULL)
        ON CONFLICT (dataset_name) DO UPDATE SET
            status = EXCLUDED.status,
            cron_schedule = EXCLUDED.cron_schedule,
            freshness_threshold_days = EXCLUDED.freshness_threshold_days
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM dataset_metadata
        WHERE dataset_name IN (
            'encadrement_loyers', 'fiscalite', 'zonage_abc', 'revenus',
            'demographie', 'copropriete', 'permis', 'antennes',
            'equipements', 'vacance',
            'bdnb', 'sirene', 'georisques', 'errial',
            'urbanisme', 'rnb', 'scoring'
        )
    """)
