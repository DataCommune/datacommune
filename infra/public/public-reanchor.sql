-- Re-anchor etl_publications.relation_oids on the relations of this database (commit f922e0e).
--
-- Played by public-db-restore.sh after every restore (pg_restore) and after every switch to
-- a ZFS clone (--from-zfs-clone), in the public database, through the local socket.
-- A pg_restore recreates every table under a new OID; the publication guard of the API
-- (app/common/publication_readiness.py) compares relation_oids with to_regclass(): every
-- published source would be refused (503, blocks "Indisponible"). Re-anchor the LATEST
-- published row of each dataset on the relations present, only when every relation it names
-- exists; the data itself is the published one, unchanged. A ZFS clone keeps the OIDs, so
-- there it is expected to move nothing: the output says so.
--
-- Output, two lines:
--   REANCHORED <n> [<dataset>,...]
--   PUBLISHED <n> NOT_PUBLISHED <n> STALE <n>     (STALE: relations of the latest published
--                                                  rows still not anchored; must be 0)
WITH latest AS (
  SELECT DISTINCT ON (dataset_name) id, relation_oids FROM etl_publications
  ORDER BY dataset_name, id DESC
), fixed AS (
  SELECT l.id, jsonb_object_agg(r.name, to_regclass(r.name)::oid::bigint) AS oids
  FROM latest l, jsonb_each_text(l.relation_oids) r(name, oid)
  GROUP BY l.id
  HAVING bool_and(to_regclass(r.name) IS NOT NULL)
), moved AS (
  UPDATE etl_publications p SET relation_oids = f.oids
  FROM fixed f JOIN etl_publications e ON e.id = f.id
  WHERE p.id = f.id AND e.status = 'published' AND p.relation_oids IS DISTINCT FROM f.oids
  RETURNING p.dataset_name
)
SELECT 'REANCHORED ' || count(*)
  || coalesce(' ' || string_agg(dataset_name, ',' ORDER BY dataset_name), '')
FROM moved;

WITH latest AS (
  SELECT DISTINCT ON (dataset_name) * FROM etl_publications ORDER BY dataset_name, id DESC
)
SELECT 'PUBLISHED ' || count(*) FILTER (WHERE status = 'published')
  || ' NOT_PUBLISHED ' || count(*) FILTER (WHERE status <> 'published')
  || ' STALE ' || (
    SELECT count(*) FROM latest p, jsonb_each_text(p.relation_oids) r(n, o)
    WHERE p.status = 'published' AND to_regclass(r.n)::oid IS DISTINCT FROM r.o::oid
  )
FROM latest;
