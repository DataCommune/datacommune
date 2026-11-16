-- Densest zoom-16 tiles of the parcel map: parcels and GeoJSON bytes (plan 04.10-01).
--
-- What it measures. The parcel route of the map serves a tile whole, without a
-- cap. This statement finds the zoom-16 tiles that hold the most parcels and,
-- for the five first, counts the parcels that really touch the tile and adds
-- up the bytes of their GeoJSON geometry at six decimals.
--
-- Why zoom 16. It is the widest zoom the map serves: its tiles cover the most
-- ground, so they carry the most parcels. Each deeper zoom splits a tile in four.
--
-- How. Step one ranks the tiles by the parcels whose bounding-box centre falls
-- in them: arithmetic on one pass over the table, no spatial predicate. Step
-- two reads the five first through the spatial index, with the tile envelope
-- brought to 4326. A parcel across several tiles counts in each of them in
-- step two, in one only in step one: the ranking is a spotting, and a tile
-- that is not among the five first by centres is not detailed.
--
-- What octets_geojson is not. It is the geometry alone: the properties and the
-- feature wrapping of a served tile come on top of it.
--
-- Read-only, one SELECT, no parameter. Optional bound: the session setting
-- densite.departement restricts step one to one partition of parcelles. Step
-- two still counts every parcel of the tile, whatever its departement, since
-- the weight of a tile is the tile's. Unset, empty or reset: the whole table.
--
-- Orchestrator, from Git Bash at the repository root (work base, reader role):
--   whole table: infra/pve02/wb-psql.sh "$(cat api/backend/scripts/densite_tuiles.sql)"
--   one departement: infra/pve02/wb-psql.sh "SET densite.departement = '<code>'; $(cat api/backend/scripts/densite_tuiles.sql)"
-- Output of psql -At, at most five lines, the tile with the most parcels first:
--   z|x|y|parcelles|octets_geojson|parcelles_centre|perimetre
WITH emprises AS (
    -- Bounded form. The setting is compared to the partition key in the scan
    -- itself, so the other partitions are pruned when the executor starts.
    SELECT Box2D(geometrie) AS emprise
    FROM parcelles
    WHERE code_departement = NULLIF(current_setting('densite.departement', true), '')
    UNION ALL
    -- National form. Its filter reads no column: the whole arm is skipped as
    -- soon as a departement is set.
    SELECT Box2D(geometrie)
    FROM parcelles
    WHERE NULLIF(current_setting('densite.departement', true), '') IS NULL
),
centres AS (
    SELECT (ST_XMin(emprise) + ST_XMax(emprise)) / 2.0 AS lon,
           (ST_YMin(emprise) + ST_YMax(emprise)) / 2.0 AS lat
    FROM emprises
    WHERE emprise IS NOT NULL
),
reperage AS (
    -- Tile of a point in the Web Mercator grid; 65536 is 2^16. The bounds keep
    -- a broken geometry from failing the logarithm or naming a tile off the grid.
    SELECT floor((lon + 180.0) / 360.0 * 65536.0)::integer AS x,
           floor(
               (1.0 - ln(tan(radians(lat)) + 1.0 / cos(radians(lat))) / pi())
               / 2.0 * 65536.0
           )::integer AS y,
           count(*) AS parcelles_centre
    FROM centres
    WHERE lon >= -180.0 AND lon < 180.0 AND lat > -85.0 AND lat < 85.0
    GROUP BY 1, 2
    ORDER BY count(*) DESC, 1, 2
    LIMIT 5
)
SELECT 16 AS z,
       r.x,
       r.y,
       d.parcelles,
       d.octets_geojson,
       r.parcelles_centre,
       COALESCE(
           'departement=' || NULLIF(current_setting('densite.departement', true), ''),
           'national'
       ) AS perimetre
FROM reperage r
CROSS JOIN LATERAL (
    SELECT count(*) AS parcelles,
           COALESCE(sum(octet_length(ST_AsGeoJSON(p.geometrie, 6))), 0)::bigint
               AS octets_geojson
    FROM parcelles p
    WHERE ST_Intersects(
        p.geometrie,
        ST_Transform(ST_TileEnvelope(16, r.x, r.y), 4326)
    )
) d
ORDER BY d.parcelles DESC, d.octets_geojson DESC, r.x, r.y;
