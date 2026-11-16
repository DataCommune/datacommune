"""Key ``permis`` on (numero_dossier, destination), the grain of the source.

Revision ID: 109_permis_dossier_destination_key
Revises: 108_dvf_grain_valeur_fonciere
Create Date: 2026-10-30

Plan 04.2-03. The first national import (run 77, 2026-10-29) refused the
swap: ``PC 093 031 22 A0015`` appears twice in the Plaine Commune file
(sha256 9bcda940...), same commune and same values in every column but
``designation`` and ``sdp_creee`` (« Habitation - Hébergement » 5 873 m²,
« Habitation - Logement » 3 476 m²). The file publishes one row per
destination of a dossier, not two versions of it: migration 032 keyed the
table on the dossier alone. Collapsing the rows would drop one floor area;
the key becomes (numero_dossier, destination).

``NULLS NOT DISTINCT``: two rows of one dossier without destination still
collide, so a conflicting copy is refused as before. Dossier-level fields
(``nb_logements_crees``) repeat on each destination row: a sum per dossier
must not add them up (the public route stays quarantined, COR-07).

The table OID is unchanged (constraint only). The unique constraint on the
dossier is found by its definition, whatever name earlier swaps left it.
Downgrade refuses, naming the count, while a dossier holds several rows.
Applied on local disposable PostGIS only; the work base is migrated with
Ianis's agreement.
"""

from alembic import op

revision: str = "109_permis_dossier_destination_key"
down_revision: str = "108_dvf_grain_valeur_fonciere"
branch_labels: str | None = None
depends_on: str | None = None


def _drop_unique(definition: str) -> str:
    """DO block dropping the unique constraint of ``permis`` with *definition*."""
    return f"""
        DO $$
        DECLARE name text;
        BEGIN
            FOR name IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'permis'::regclass AND contype = 'u'
                  AND pg_get_constraintdef(oid) = '{definition}'
            LOOP
                EXECUTE format('ALTER TABLE permis DROP CONSTRAINT %I', name);
            END LOOP;
        END $$;
    """


def upgrade() -> None:
    op.execute(_drop_unique("UNIQUE (numero_dossier)"))
    op.execute(
        "ALTER TABLE permis ADD CONSTRAINT permis_numero_dossier_destination_key "
        "UNIQUE NULLS NOT DISTINCT (numero_dossier, destination)"
    )


def downgrade() -> None:
    op.execute("""
        DO $$
        DECLARE shared bigint;
        BEGIN
            SELECT count(*) INTO shared FROM (
                SELECT 1 FROM permis GROUP BY numero_dossier HAVING count(*) > 1
            ) AS dossiers;
            IF shared > 0 THEN
                RAISE EXCEPTION
                    'downgrade refused: % dossiers hold several destination rows',
                    shared;
            END IF;
        END $$;
    """)
    op.execute(_drop_unique("UNIQUE NULLS NOT DISTINCT (numero_dossier, destination)"))
    op.execute(
        "ALTER TABLE permis ADD CONSTRAINT permis_numero_dossier_key "
        "UNIQUE (numero_dossier)"
    )
