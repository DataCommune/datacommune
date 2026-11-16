-- Row counts and fingerprints of the backed-up tables (plan 05-01, D-07). Played twice with
-- the same text: by accounts-dump.sh in the exported snapshot of the production database, and
-- by accounts-load.sh in the database it reloaded. Equal lines = identical rows.
--
-- Input, set by the caller in the same session before this file:
--   accounts_manifest.tables  tables of the public schema, comma separated, in output order
-- Output, one line per table, tab separated:
--   table <name> <rows> <md5 fingerprint> <column list>
-- The column list is the one COPY exports (stored columns, attnum order, quote_ident); the
-- dump reuses it for its COPY, so rows, fingerprint and archive describe the same columns.
-- Fingerprint: md5 of every row rendered as text (the session settings below fix that
-- rendering), sorted, grouped by 100000, md5 of each group, then md5 of the group hashes in
-- order. No text ever holds a whole table: api_request_log can hold millions of rows and one
-- text value stops at 1 GB. SELECT only: it runs inside a READ ONLY transaction.
SET TimeZone = 'UTC';
SET DateStyle = 'ISO, YMD';
SET IntervalStyle = 'postgres';
SET extra_float_digits = 1;
SET bytea_output = 'hex';

SELECT concat_ws(E'\t', 'table', u.t, r.n, r.h, c.cols)
FROM unnest(string_to_array(current_setting('accounts_manifest.tables'), ','))
       WITH ORDINALITY AS u(t, i)
CROSS JOIN LATERAL (
  SELECT string_agg(quote_ident(a.attname), ',' ORDER BY a.attnum) AS cols
  FROM pg_attribute a
  WHERE a.attrelid = format('public.%I', u.t)::regclass
    AND a.attnum > 0 AND NOT a.attisdropped AND a.attgenerated = ''
) c
CROSS JOIN LATERAL (
  SELECT (xpath('/row/n/text()', x))[1]::text::bigint AS n,
         (xpath('/row/h/text()', x))[1]::text AS h
  FROM query_to_xml(format(
    $q$SELECT coalesce(sum(k), 0) AS n, md5(coalesce(string_agg(g_hash, '' ORDER BY g), '')) AS h
       FROM (SELECT g, count(*) AS k, md5(string_agg(row_hash, '' ORDER BY row_hash)) AS g_hash
             FROM (SELECT row_hash, (row_number() OVER (ORDER BY row_hash) - 1) / 100000 AS g
                   FROM (SELECT md5((x.*)::text) COLLATE "C" AS row_hash
                         FROM (SELECT %s FROM public.%I) x) rows_hashed) numbered
             GROUP BY g) groups$q$,
    c.cols, u.t), false, true, '') AS x
) r
ORDER BY u.i;
