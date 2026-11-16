-- Public state of a ZFS clone of the work cluster (public-db-restore.sh --from-zfs-clone,
-- step 6), after public-reset-temoin.sql. Played in the public database, as the superuser
-- of the image, through the local socket. One transaction: on any error nothing changes.
--
-- Input, set by the caller in the same session before this file:
--   public_reset.tables      application tables, comma separated, parents first (foreign
--                            keys are checked on reload); defined once in
--                            public-db-restore.sh (APP_TABLES)
--   public_reset.counts      <table>=<rows> exported from the public base, comma separated
--   public_reset.drop_roles  roles of the work cluster to remove (WORK_ROLES), may be empty
--   public_reset.keep_roles  optional: roles allowed to remain besides current_user (the
--                            switch leaves it unset: only the superuser of the image remains)
--   schema public_reset_rows one table per application table, holding the exported rows
--                            with the exported columns (loaded by COPY ... FROM STDIN)
--
-- Effect: the work roles are removed (REASSIGN OWNED first, so DROP OWNED only revokes
-- privileges and default privileges); every application table is emptied in one TRUNCATE
-- and reloaded from public_reset_rows; the owned sequences are set past the reloaded ids;
-- every table must then hold exactly the exported rows, compared row by row as text, so no
-- work row (an internal api key above all) can remain. public_reset_rows is dropped.
-- Output: PUBLIC_RESET_OK api_keys=<n>
BEGIN;

DO $reset$
DECLARE
  tables text[] := string_to_array(current_setting('public_reset.tables'), ',');
  counts jsonb;
  drop_roles text[] := coalesce(
    string_to_array(nullif(current_setting('public_reset.drop_roles'), ''), ','), '{}');
  keep_roles text[] := coalesce(
    string_to_array(nullif(current_setting('public_reset.keep_roles', true), ''), ','), '{}')
    || current_user::text;
  t text;
  r text;
  cols text;
  n bigint;
  extra text;
  seq record;
BEGIN
  IF tables IS NULL OR cardinality(tables) = 0 THEN
    RAISE EXCEPTION 'public_reset.tables is empty';
  END IF;
  SELECT coalesce(jsonb_object_agg(split_part(kv, '=', 1), split_part(kv, '=', 2)::bigint), '{}')
    INTO counts
    FROM unnest(string_to_array(current_setting('public_reset.counts'), ',')) kv;

  -- 1. The staged rows are complete: every application table exists in the clone and has
  --    its staged rows, exactly as many as exported; the staging holds nothing else.
  FOREACH t IN ARRAY tables LOOP
    IF to_regclass(format('public.%I', t)) IS NULL THEN
      RAISE EXCEPTION 'application table public.% missing in the clone', t;
    END IF;
    IF to_regclass(format('public_reset_rows.%I', t)) IS NULL THEN
      RAISE EXCEPTION 'staged rows of % missing', t;
    END IF;
    IF NOT counts ? t THEN
      RAISE EXCEPTION 'no exported row count for %', t;
    END IF;
    EXECUTE format('SELECT count(*) FROM public_reset_rows.%I', t) INTO n;
    IF n <> (counts ->> t)::bigint THEN
      RAISE EXCEPTION 'staged rows of %: % instead of the % exported', t, n, counts ->> t;
    END IF;
  END LOOP;
  SELECT string_agg(c.relname, ',' ORDER BY c.relname) INTO extra
    FROM pg_class c JOIN pg_namespace s ON s.oid = c.relnamespace
    WHERE s.nspname = 'public_reset_rows' AND c.relkind = 'r' AND c.relname <> ALL (tables);
  IF extra IS NOT NULL THEN
    RAISE EXCEPTION 'public_reset_rows holds tables outside public_reset.tables: %', extra;
  END IF;

  -- 2. Roles of the work cluster (wb_reader: the hypervisor's workbase-reader-role.sh). Any other
  --    role left fails the reset: it is checked, never kept silently.
  FOREACH r IN ARRAY drop_roles LOOP
    CONTINUE WHEN NOT EXISTS (SELECT FROM pg_roles WHERE rolname = r);
    IF r = current_user THEN
      RAISE EXCEPTION 'refusing to drop the superuser %', r;
    END IF;
    EXECUTE format('REASSIGN OWNED BY %I TO %I', r, current_user);
    EXECUTE format('DROP OWNED BY %I', r);
    EXECUTE format('DROP ROLE %I', r);
    RAISE NOTICE 'role % dropped', r;
  END LOOP;
  SELECT string_agg(rolname, ',' ORDER BY rolname) INTO extra
    FROM pg_roles WHERE rolname NOT LIKE 'pg\_%' AND rolname <> ALL (keep_roles);
  IF extra IS NOT NULL THEN
    RAISE EXCEPTION 'roles left from the work cluster: %', extra;
  END IF;

  -- 3. Application rows: one TRUNCATE without CASCADE (a table outside the list that
  --    references one of them fails the reset instead of being emptied silently), then
  --    the public rows, parents first.
  EXECUTE 'TRUNCATE ' || (SELECT string_agg(format('public.%I', x), ', ') FROM unnest(tables) x);
  FOREACH t IN ARRAY tables LOOP
    SELECT string_agg(quote_ident(a.attname), ', ' ORDER BY a.attnum) INTO cols
      FROM pg_attribute a
      WHERE a.attrelid = format('public_reset_rows.%I', t)::regclass
        AND a.attnum > 0 AND NOT a.attisdropped;
    EXECUTE format('INSERT INTO public.%I (%s) OVERRIDING SYSTEM VALUE SELECT %s FROM public_reset_rows.%I',
                   t, cols, cols, t);
    FOR seq IN
      SELECT a.attname, pg_get_serial_sequence(format('public.%I', t), a.attname) AS s
      FROM pg_attribute a
      WHERE a.attrelid = format('public.%I', t)::regclass AND a.attnum > 0 AND NOT a.attisdropped
    LOOP
      CONTINUE WHEN seq.s IS NULL;
      EXECUTE format('SELECT setval(%L, coalesce(max(%I), 1), max(%I) IS NOT NULL) FROM public.%I',
                     seq.s, seq.attname, seq.attname, t);
    END LOOP;
    -- 4. Exactly the exported rows, compared as text (json columns have no equality).
    EXECUTE format(
      'SELECT count(*) FROM ((SELECT (x.*)::text FROM (SELECT %s FROM public.%I) x
                              EXCEPT ALL SELECT (y.*)::text FROM public_reset_rows.%I y)
                             UNION ALL
                             (SELECT (y.*)::text FROM public_reset_rows.%I y
                              EXCEPT ALL SELECT (x.*)::text FROM (SELECT %s FROM public.%I) x)) d',
      cols, t, t, t, cols, t) INTO n;
    IF n <> 0 THEN
      RAISE EXCEPTION 'public.% differs from the exported rows (% rows)', t, n;
    END IF;
  END LOOP;
END
$reset$;

DROP SCHEMA public_reset_rows CASCADE;
COMMIT;

SELECT 'PUBLIC_RESET_OK api_keys=' || count(*) FROM public.api_keys;
