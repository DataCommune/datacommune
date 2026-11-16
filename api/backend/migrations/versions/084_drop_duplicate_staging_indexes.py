"""Drop 48 exact-duplicate indexes left behind by the pre-hygiene ETL swaps.

Revision ID: 084_drop_duplicate_staging_indexes
Revises: 083_drop_diagnostics_dpe_old
Create Date: 2026-08-23

Provenance: DB health audit 2026-08-22 (internal note AUDIT-SANTE-DB-2026-08-22
§ 2) plus the read-only production inventory of 2026-08-23 (DB-HYGIENE-01 prod
plan, step 3). Before DB-HYGIENE-01 the ETL staging tables were created with
``LIKE ... INCLUDING ALL``, so every run rebuilt a full set of indexes on
staging and the atomic swap promoted them alongside the previous generation —
same definition, different name (``idx_<t>_staging_x`` vs ``<t>_staging_x_idx``
vs ``..._idx3/4/5``). The code fix (PR #61: ``EXCLUDING INDEXES`` +
``rename_indexes_after_swap``) stops the accumulation; this migration removes
what had already accumulated: 57 redundant indexes (874 233 856 bytes ≈ 834 MB,
partition children included) via 48 explicit ``DROP INDEX`` statements.

Safety, verified on production 2026-08-23: every dropped index is an **exact
duplicate** — ``pg_get_indexdef`` identical modulo the name — of a surviving
index on the same table, and none is a primary key or backs a constraint
(``NOT indisprimary``, no ``pg_constraint`` row). Query plans lose nothing:
the planner picks the surviving twin.

Special cases:

- **batiments is partitioned**: only the 4 parent indexes are dropped
  (``idx_batiments_staging_{cleabs,code_departement,geometrie,geometrie_geog}``).
  Their 12 partition children (``batiments_staging_{44,85,default}_*_idx1``)
  fall with them in cascade — PostgreSQL refuses a direct ``DROP INDEX`` on a
  child attached to a partitioned parent ("... requires it").
- **zonage_abc.code_insee** exists in triplicate: BOTH plain copies
  (``idx_zonage_abc_staging_code_insee`` and ``zonage_abc_staging_code_insee_idx``)
  are dropped; the survivor is the index of the UNIQUE constraint
  ``zonage_abc_staging_code_insee_key``, which is untouchable by design and
  covers the same single column.
- **copropriete** accumulated up to ``_idx5`` suffixes (three redundant
  generations per column) — all six duplicates go, the canonical
  ``idx_copropriete_staging_{commune,postal}`` survive.
- **Two billing indexes, outside the ETL family**:
  ``idx_subscriptions_stripe_sub`` duplicates the UNIQUE constraint index
  ``subscriptions_stripe_subscription_id_key`` and ``idx_users_stripe_customer``
  duplicates ``users_stripe_customer_id_key`` (migration 023 predates the
  UNIQUE constraints; both column lookups keep an index). They are declared
  nowhere in live code — no SQLAlchemy model, only migration 023's immutable
  history — so on a fresh install 023 creates them and this migration drops
  them, which is the normal Alembic lifecycle.

Deliberately NOT dropped (inventory false positives):

- ``geo_communes`` trigram-vs-pattern pairs: ``text_pattern_ops`` and
  ``gin_trgm_ops`` indexes serve different operators — different definitions,
  not duplicates.
- ``idx_subscriptions_user``: covered only by the *prefix* of a wider index,
  which is out of scope here (exact duplicates only; prefix coverage is a
  planner trade-off, not dead weight by definition).

Notes:

- ``IF EXISTS`` on every drop: if an ETL run lands between this migration being
  merged and the CD applying it, ``rename_indexes_after_swap`` (DB-HYGIENE-01)
  has already deduplicated that dataset's staging indexes — the no-op is the
  intended outcome, not an anomaly. And on a fresh install none of these names
  exist: they are swap artefacts no migration ever created (except the two
  billing ones from 023, present and dropped).
- Lock: each ``DROP INDEX`` takes a brief ACCESS EXCLUSIVE lock on its table —
  metadata-only, no data rewrite. 48 short exclusive locks interleaved with
  production reads are routine; no maintenance window needed.
- Deployment follows ADR-0003 (D1): supervised migrate-before-cutover.
"""

from alembic import op

revision: str = "084_drop_duplicate_staging_indexes"
down_revision: str = "083_drop_diagnostics_dpe_old"
branch_labels: str | None = None
depends_on: str | None = None

# 48 exact duplicates, frozen from the 2026-08-23 production inventory.
# Grouped by carrier table; each name's surviving twin is identical in
# definition (see docstring). The 4 batiments entries are partitioned parents
# whose 12 children cascade.
_DUPLICATE_INDEXES: tuple[str, ...] = (
    # antennes (survivors: idx_antennes_staging_{insee,gen,op}, antennes_staging_geom_idx)
    "antennes_staging_code_insee_idx",
    "antennes_staging_generation_idx",
    "antennes_staging_operateur_idx",
    "idx_antennes_staging_geom",
    # arrets_transport (survivors: idx_arrets_staging_{stop_name,position})
    "arrets_transport_staging_stop_name_idx",
    "arrets_transport_staging_position_idx",
    # batiments — partitioned PARENTS, children *_idx1 drop in cascade
    # (survivors: batiments_staging_{cleabs,code_departement,geometrie,geography}_idx)
    "idx_batiments_staging_cleabs",
    "idx_batiments_staging_code_departement",
    "idx_batiments_staging_geometrie",
    "idx_batiments_staging_geometrie_geog",
    # carte_scolaire (survivors: idx_scolaire_staging_code_insee{,_rne})
    "carte_scolaire_staging_code_insee_idx",
    "carte_scolaire_staging_code_insee_code_rne_idx",
    # copropriete (survivors: idx_copropriete_staging_{commune,postal})
    "copropriete_staging_code_commune_idx3",
    "copropriete_staging_code_commune_idx4",
    "copropriete_staging_code_commune_idx5",
    "copropriete_staging_code_postal_idx3",
    "copropriete_staging_code_postal_idx4",
    "copropriete_staging_code_postal_idx5",
    # demographie (survivors: idx_demographie_staging_dept, demographie_staging_code_insee_idx)
    "demographie_staging_code_departement_idx",
    "idx_demographie_staging_code_insee",
    # eligibilite_fixe (survivors: idx_eligibilite_staging_{code_insee,code_insee_voie,id_imb})
    "eligibilite_fixe_staging_code_insee_idx",
    "eligibilite_fixe_staging_code_insee_voie_idx",
    "eligibilite_fixe_staging_id_imb_idx",
    # encadrement_loyers (survivors: idx_encadrement_staging_{annee,ville,geom})
    "encadrement_loyers_staging_annee_idx",
    "encadrement_loyers_staging_ville_idx",
    "encadrement_loyers_staging_geom_idx",
    # fiscalite_locale (survivors: idx_fiscalite_staging_{dept,code_insee})
    "fiscalite_locale_staging_code_departement_idx",
    "fiscalite_locale_staging_code_insee_idx",
    # gares (survivors: gares_staging_{code_insee,position}_idx)
    "idx_gares_staging_code_insee",
    "idx_gares_staging_position",
    # indicateurs_loyers (survivors: idx_loyers_staging_{code_departement,code_insee})
    "indicateurs_loyers_staging_code_departement_idx",
    "indicateurs_loyers_staging_code_insee_idx",
    # monuments_historiques (survivors: idx_monuments_staging_{code_insee,reference,position})
    "monuments_historiques_staging_code_insee_idx",
    "monuments_historiques_staging_reference_idx",
    "monuments_historiques_staging_position_idx",
    # permis (survivors: idx_permis_staging_{annee,commune,dept})
    "permis_staging_annee_idx",
    "permis_staging_code_commune_idx",
    "permis_staging_code_departement_idx",
    # revenus (survivors: idx_revenus_staging_{dept,code_insee})
    "revenus_staging_code_departement_idx",
    "revenus_staging_code_insee_idx",
    # vacance (survivors: idx_vacance_staging_{commune,dept})
    "vacance_staging_code_commune_idx",
    "vacance_staging_code_departement_idx",
    # zonage_abc (survivors: idx_zonage_abc_staging_{code_departement,zone};
    # code_insee survivor = UNIQUE zonage_abc_staging_code_insee_key)
    "zonage_abc_staging_code_departement_idx",
    "idx_zonage_abc_staging_code_insee",
    "zonage_abc_staging_code_insee_idx",
    "zonage_abc_staging_zone_idx",
    # billing (survivors: UNIQUE subscriptions_stripe_subscription_id_key,
    # users_stripe_customer_id_key)
    "idx_subscriptions_stripe_sub",
    "idx_users_stripe_customer",
)

assert len(_DUPLICATE_INDEXES) == 48, len(_DUPLICATE_INDEXES)


def upgrade() -> None:
    for index_name in _DUPLICATE_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")


def downgrade() -> None:
    # Deliberate no-op. Recreating 834 MB of indexes that are byte-for-byte
    # redundant with their surviving twins is not a useful rollback: no query
    # plan depends on the dropped names, only on the definitions, which all
    # survive. The staging halves would in any case be rebuilt (deduplicated,
    # under canonical names) by the next ETL run of each dataset. A real
    # rollback of this deploy is a PBS snapshot restore plus the previous
    # image (ADR-0003 D2), never `alembic downgrade`.
    pass
