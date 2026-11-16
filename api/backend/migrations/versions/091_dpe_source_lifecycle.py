"""Add closed, initially unknown DPE provenance and lifecycle facts.

Revision ID: 091_dpe_source_lifecycle
Revises: 090_dpe_adresse_brute_ademe

Existing payload and geometry nullability stay unchanged. New facts are only
published by a complete staged import. Downgrade removes schema additions;
it cannot restore documentary content intentionally purged by reconciliation.
"""

from alembic import op

revision = "091_dpe_source_lifecycle"
down_revision = "090_dpe_adresse_brute_ademe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE FUNCTION public.dc_dpe_json_keys(value jsonb, expected text[])
        RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
            SELECT CASE WHEN jsonb_typeof(value) = 'object'
                THEN value ?& expected AND value - expected = '{}'::jsonb
                ELSE false END
        $$;

        CREATE FUNCTION public.dc_dpe_ordered_flags(value jsonb, allowed text[])
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
        DECLARE item jsonb; ordinal integer; previous integer := 0;
        BEGIN
            IF jsonb_typeof(value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
            IF jsonb_array_length(value) > cardinality(allowed) THEN RETURN false; END IF;
            IF jsonb_array_length(value) = 0 THEN RETURN true; END IF;
            FOR item IN SELECT jsonb_array_elements(value) LOOP
                IF jsonb_typeof(item) IS DISTINCT FROM 'string' THEN RETURN false; END IF;
                ordinal := array_position(allowed, item #>> '{}');
                IF ordinal IS NULL OR ordinal <= previous THEN RETURN false; END IF;
                previous := ordinal;
            END LOOP;
            RETURN true;
        END $$;

        CREATE FUNCTION public.dc_dpe_utc_timestamp(value text)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
        BEGIN
            IF value IS NULL OR value !~
                '^[0-9]{4}-[0-9]{2}-[0-9]{2}T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|\+00:00)$'
            THEN RETURN false; END IF;
            PERFORM value::timestamptz;
            RETURN true;
        EXCEPTION WHEN invalid_datetime_format OR datetime_field_overflow THEN
            RETURN false;
        END $$;

        CREATE FUNCTION public.dc_dpe_observations_valid(value jsonb)
        RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
        DECLARE item jsonb; field text; pair text; previous text := '';
        BEGIN
            IF jsonb_typeof(value) IS DISTINCT FROM 'array' THEN RETURN false; END IF;
            IF jsonb_array_length(value) > 4 THEN RETURN false; END IF;
            IF jsonb_array_length(value) = 0 THEN RETURN true; END IF;
            FOR item IN SELECT jsonb_array_elements(value) LOOP
                IF NOT public.dc_dpe_json_keys(item, ARRAY[
                    'source_slug','department','source_version','observed_at',
                    'last_seen_at','state','import_id'
                ]) THEN RETURN false; END IF;
                FOREACH field IN ARRAY ARRAY[
                    'source_slug','department','source_version','observed_at',
                    'last_seen_at','state','import_id'
                ] LOOP
                    IF jsonb_typeof(item->field) IS DISTINCT FROM 'string'
                    THEN RETURN false; END IF;
                END LOOP;
                IF item->>'source_slug' NOT IN ('dpe03existant','dpe02neuf')
                    OR item->>'department' NOT IN ('44','85')
                    OR item->>'source_version' !~ '^[a-f0-9]{64}$'
                    OR btrim(item->>'import_id') = ''
                    OR item->>'state' NOT IN (
                        'present','absent_from_complete_scope','unknown'
                    ) THEN RETURN false; END IF;
                IF NOT public.dc_dpe_utc_timestamp(item->>'observed_at')
                    OR NOT public.dc_dpe_utc_timestamp(item->>'last_seen_at')
                THEN RETURN false; END IF;
                IF (item->>'last_seen_at')::timestamptz >
                    (item->>'observed_at')::timestamptz THEN RETURN false; END IF;
                pair := (item->>'source_slug') || ':' || (item->>'department');
                IF pair COLLATE "C" <= previous COLLATE "C" THEN RETURN false; END IF;
                previous := pair;
            END LOOP;
            RETURN true;
        END $$;

        CREATE FUNCTION public.dc_dpe_lifecycle_valid(value jsonb)
        RETURNS boolean LANGUAGE sql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
            SELECT public.dc_dpe_json_keys(value,
                    ARRAY['replacement_state','graph_issues','date_issues'])
                AND jsonb_typeof(value->'replacement_state') = 'string'
                AND value->>'replacement_state' IN (
                    'not_checked','no_successor_known','replaced','ambiguous')
                AND public.dc_dpe_ordered_flags(value->'graph_issues', ARRAY[
                    'cycle','fork','missing_target','truncated','ambiguous_target'])
                AND public.dc_dpe_ordered_flags(value->'date_issues', ARRAY[
                    'invalid_establishment_date','invalid_end_date'])
        $$;

        CREATE FUNCTION public.dc_dpe_source_coherent(
            observations jsonb, state text, snapshot timestamptz
        ) RETURNS boolean LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE
        SET search_path = pg_catalog AS $$
        DECLARE item jsonb; has_present boolean := false;
        BEGIN
            IF NOT public.dc_dpe_observations_valid(observations) THEN RETURN false; END IF;
            IF state = 'unknown' THEN
                RETURN observations = '[]'::jsonb AND snapshot IS NULL;
            END IF;
            IF state NOT IN ('present','absent_from_complete_scope')
                OR snapshot IS NULL OR NOT isfinite(snapshot) THEN RETURN false; END IF;
            FOR item IN SELECT jsonb_array_elements(observations) LOOP
                IF (item->>'observed_at')::timestamptz > snapshot THEN RETURN false; END IF;
                has_present := has_present OR item->>'state' = 'present';
            END LOOP;
            RETURN (state = 'present') = has_present;
        END $$;

        ALTER TABLE public.diagnostics_dpe
            ADD COLUMN source_observations JSONB NOT NULL DEFAULT '[]'::jsonb,
            ADD COLUMN source_state TEXT NOT NULL DEFAULT 'unknown',
            ADD COLUMN source_snapshot_at TIMESTAMPTZ,
            ADD COLUMN is_tombstone BOOLEAN NOT NULL DEFAULT false,
            ADD COLUMN lifecycle_facts JSONB NOT NULL DEFAULT
                '{"replacement_state":"not_checked","graph_issues":[],"date_issues":[]}'::jsonb,
            ADD COLUMN revision_evidence JSONB NOT NULL DEFAULT
                '{"status":"unverified","kind": null,"revision_date": null,"verified_at": null,"evidence_sha256": null,"official_source": null}'::jsonb,
            ADD CONSTRAINT ck_dpe_source_observations
                CHECK (public.dc_dpe_observations_valid(source_observations) IS TRUE),
            ADD CONSTRAINT ck_dpe_lifecycle_facts
                CHECK (public.dc_dpe_lifecycle_valid(lifecycle_facts) IS TRUE),
            ADD CONSTRAINT ck_dpe_revision_evidence CHECK (revision_evidence =
                '{"status":"unverified","kind": null,"revision_date": null,"verified_at": null,"evidence_sha256": null,"official_source": null}'::jsonb),
            ADD CONSTRAINT ck_dpe_source_coherence CHECK (
                public.dc_dpe_source_coherent(source_observations, source_state, source_snapshot_at) IS TRUE
                AND is_tombstone = (source_state = 'absent_from_complete_scope')),
            ADD CONSTRAINT ck_dpe_tombstone_minimized CHECK (NOT is_tombstone OR (
                date_etablissement_dpe IS NULL AND annee_construction IS NULL
                AND type_batiment IS NULL AND surface_habitable IS NULL
                AND etiquette_dpe IS NULL AND etiquette_ges IS NULL
                AND type_installation_chauffage IS NULL AND conso_5_usages_ep IS NULL
                AND emission_ges_5_usages IS NULL AND code_postal IS NULL
                AND nom_commune IS NULL AND longitude IS NULL AND latitude IS NULL
                AND position IS NULL AND periode_construction IS NULL
                AND cout_total_5_usages IS NULL AND type_energie_principale_chauffage IS NULL
                AND qualite_isolation_enveloppe IS NULL AND numero_etage_appartement IS NULL
                AND complement_adresse_logement IS NULL AND date_fin_validite_dpe IS NULL
                AND id_rnb IS NULL AND provenance_id_rnb IS NULL AND score_ban IS NULL
                AND adresse_brut IS NULL AND adresse_complete_brut IS NULL
                AND nom_commune_brut IS NULL AND code_postal_brut IS NULL
                AND adresse_ban IS NULL AND numero_voie_ban IS NULL
                AND nom_rue_ban IS NULL AND statut_geocodage IS NULL
                AND lifecycle_facts->'date_issues' = '[]'::jsonb
            ));
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE public.diagnostics_dpe
            DROP CONSTRAINT ck_dpe_tombstone_minimized,
            DROP CONSTRAINT ck_dpe_source_coherence,
            DROP CONSTRAINT ck_dpe_revision_evidence,
            DROP CONSTRAINT ck_dpe_lifecycle_facts,
            DROP CONSTRAINT ck_dpe_source_observations,
            DROP COLUMN revision_evidence, DROP COLUMN lifecycle_facts,
            DROP COLUMN is_tombstone, DROP COLUMN source_snapshot_at,
            DROP COLUMN source_state, DROP COLUMN source_observations;
        DROP FUNCTION public.dc_dpe_source_coherent(jsonb, text, timestamptz);
        DROP FUNCTION public.dc_dpe_lifecycle_valid(jsonb);
        DROP FUNCTION public.dc_dpe_observations_valid(jsonb);
        DROP FUNCTION public.dc_dpe_utc_timestamp(text);
        DROP FUNCTION public.dc_dpe_ordered_flags(jsonb, text[]);
        DROP FUNCTION public.dc_dpe_json_keys(jsonb, text[]);
    """)
