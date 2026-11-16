"""Populate superficie and densite from cadastre parcelles.

The INSEE populations legales file does not include SUPERF/DENS columns.
This migration computes them from cadastre contenance (sum per commune)
and inserts aggregated PLM rows (Paris/Lyon/Marseille) that are missing
from the source (which only has arrondissements).

Also refreshes v_commune_synthese to expose the new data.

Revision ID: 053_densite_from_cadastre
Revises: 052_recreate_commune_synthese
Create Date: 2026-04-05
"""

from alembic import op

revision = "053_densite_from_cadastre"
down_revision = "052_recreate_commune_synthese"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Insert aggregated PLM rows (Paris, Marseille, Lyon)
    # NOTE: %% needed because Alembic/psycopg2 uses % as placeholder
    op.execute("""
        INSERT INTO demographie
            (code_insee, nom_commune, code_departement,
             code_region, population, population_hommes,
             population_femmes, nb_menages, nb_logements,
             nb_residences_principales, nb_logements_vacants,
             nb_residences_secondaires, annee)
        SELECT
            CASE
                WHEN code_insee LIKE '751%%' THEN '75056'
                WHEN code_insee LIKE '132%%' THEN '13055'
                WHEN code_insee LIKE '6938%%' THEN '69123'
            END,
            CASE
                WHEN code_insee LIKE '751%%' THEN 'Paris'
                WHEN code_insee LIKE '132%%' THEN 'Marseille'
                WHEN code_insee LIKE '6938%%' THEN 'Lyon'
            END,
            CASE
                WHEN code_insee LIKE '751%%' THEN '75'
                WHEN code_insee LIKE '132%%' THEN '13'
                WHEN code_insee LIKE '6938%%' THEN '69'
            END,
            MAX(code_region),
            SUM(population),
            SUM(population_hommes),
            SUM(population_femmes),
            SUM(nb_menages),
            SUM(nb_logements),
            SUM(nb_residences_principales),
            SUM(nb_logements_vacants),
            SUM(nb_residences_secondaires),
            annee
        FROM demographie
        WHERE (code_insee LIKE '751__'
            OR code_insee LIKE '132__'
            OR code_insee LIKE '6938_')
          AND annee = (SELECT MAX(annee) FROM demographie)
        GROUP BY annee,
            CASE WHEN code_insee LIKE '751%%' THEN '75056'
                 WHEN code_insee LIKE '132%%' THEN '13055'
                 WHEN code_insee LIKE '6938%%' THEN '69123'
            END,
            CASE WHEN code_insee LIKE '751%%' THEN 'Paris'
                 WHEN code_insee LIKE '132%%' THEN 'Marseille'
                 WHEN code_insee LIKE '6938%%' THEN 'Lyon'
            END,
            CASE WHEN code_insee LIKE '751%%' THEN '75'
                 WHEN code_insee LIKE '132%%' THEN '13'
                 WHEN code_insee LIKE '6938%%' THEN '69'
            END
        ON CONFLICT (code_insee, annee) DO NOTHING
    """)

    # 2. Compute superficie + densite from cadastre for all communes
    op.execute("""
        UPDATE demographie d
        SET superficie = sub.km2,
            densite = ROUND(
                d.population::numeric / NULLIF(sub.km2, 0), 1
            )
        FROM (
            SELECT commune AS code_insee,
                   ROUND(SUM(contenance)::numeric / 1000000, 2) AS km2
            FROM parcelles
            GROUP BY commune
        ) sub
        WHERE d.code_insee = sub.code_insee
          AND d.annee = (SELECT MAX(annee) FROM demographie)
    """)

    # 3. Compute superficie + densite for PLM global codes
    op.execute("""
        UPDATE demographie d
        SET superficie = sub.km2,
            densite = ROUND(
                d.population::numeric / NULLIF(sub.km2, 0), 1
            )
        FROM (
            SELECT
                CASE
                    WHEN commune LIKE '751%%' THEN '75056'
                    WHEN commune LIKE '132%%' THEN '13055'
                    WHEN commune LIKE '6938%%' THEN '69123'
                END AS code_insee,
                ROUND(SUM(contenance)::numeric / 1000000, 2) AS km2
            FROM parcelles
            WHERE commune LIKE '751%%'
               OR commune LIKE '132%%'
               OR commune LIKE '6938%%'
            GROUP BY
                CASE WHEN commune LIKE '751%%' THEN '75056'
                     WHEN commune LIKE '132%%' THEN '13055'
                     WHEN commune LIKE '6938%%' THEN '69123'
                END
        ) sub
        WHERE d.code_insee = sub.code_insee
    """)

    # 4. Refresh matview to expose updated data
    op.execute(
        "REFRESH MATERIALIZED VIEW CONCURRENTLY v_commune_synthese"
    )


def downgrade() -> None:
    # Reset densite and superficie to NULL
    op.execute(
        "UPDATE demographie SET densite = NULL, superficie = NULL"
    )
    # Remove PLM aggregate rows
    op.execute(
        "DELETE FROM demographie "
        "WHERE code_insee IN ('75056', '13055', '69123')"
    )
    op.execute(
        "REFRESH MATERIALIZED VIEW CONCURRENTLY v_commune_synthese"
    )
