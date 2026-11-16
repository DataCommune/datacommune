#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
suffix="$$"
network="dc-preprod-seed-test-$suffix"
source_container="dc-preprod-source-$suffix"
target_container="dc-preprod-target-$suffix"
tmp=$(mktemp -d)

cleanup() {
  docker rm -f "$source_container" "$target_container" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  rm -rf "$tmp"
}
trap cleanup EXIT

docker network create "$network" >/dev/null
docker run -d --name "$source_container" --network "$network" \
  -e POSTGRES_PASSWORD=test-password -e POSTGRES_DB=datacommune \
  postgis/postgis:17-3.5 >/dev/null
docker run -d --name "$target_container" --network "$network" \
  -e POSTGRES_PASSWORD=test-password -e POSTGRES_DB=datacommune_preprod \
  postgis/postgis:17-3.5 postgres -c datacommune.environment=preproduction >/dev/null

for container in "$source_container" "$target_container"; do
  for _ in $(seq 1 30); do
    docker exec "$container" pg_isready -U postgres >/dev/null 2>&1 && break
    sleep 1
  done
  sleep 3
  docker exec "$container" pg_isready -U postgres >/dev/null
done

read -r -d '' fixture_schema <<'SQL' || true
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE TABLE alembic_version(version_num text primary key);
INSERT INTO alembic_version VALUES ('test_revision');
CREATE TABLE dataset_metadata(dataset_name text primary key, status text not null, row_count bigint);
CREATE TABLE geo_communes(code text primary key, nom text not null);
CREATE TABLE adresses(id bigint, code_departement text, numero text, rep text, nom_voie text, code_postal text, code_commune text references geo_communes(code), nom_commune text, position geometry(Point,4326), ban_id text, search_text text GENERATED ALWAYS AS (nom_voie) STORED) PARTITION BY LIST(code_departement);
CREATE TABLE adresses_44 PARTITION OF adresses FOR VALUES IN ('44');
CREATE TABLE adresses_75 PARTITION OF adresses FOR VALUES IN ('75');
CREATE TABLE adresses_13 PARTITION OF adresses FOR VALUES IN ('13');
CREATE TABLE adresses_2a PARTITION OF adresses FOR VALUES IN ('2A');
CREATE TABLE adresses_971 PARTITION OF adresses FOR VALUES IN ('971');
CREATE TABLE etl_publications(id bigserial primary key, table_name text not null, status text not null, relation_oids jsonb not null default '{}');
CREATE TABLE matview_metadata(matview_name text primary key, last_refresh_at timestamptz, last_refresh_duration_seconds double precision, last_refresh_status text, last_error text);
CREATE TABLE users(id bigint primary key, email text);
CREATE TABLE mutations_grain(code_commune text, date_mutation date, classe_mutation text, est_vente boolean, hors_stats_vefa boolean, nb_communes integer, surface_bati_totale numeric, valeur_fonciere numeric);
CREATE TABLE diagnostics_dpe(code_insee text, etiquette_dpe text, annee_construction integer, is_tombstone boolean, source_state text);
CREATE TABLE equipements(code_commune text, annee integer, nb_equipements integer);
CREATE TABLE carte_scolaire(code_insee text, code_rne text);
CREATE TABLE monuments_historiques(code_insee text, reference text);
CREATE TABLE foncier_pm(code_commune text, numero_siren text, code_parcelle text, contenance_parcelle numeric);
CREATE TABLE eligibilite_fixe(code_insee text, id_imb text, technologie text);
CREATE TABLE demographie(code_insee text, nom_commune text, code_departement text, code_region text, population integer, nb_menages integer, nb_logements integer, nb_logements_vacants integer, densite numeric, superficie numeric, annee integer);
CREATE TABLE revenus(code_insee text, nom_departement text, nom_region text, revenu_median numeric, taux_pauvrete numeric, indice_gini numeric, part_imposee numeric, annee integer);
CREATE TABLE zonage_abc(code_insee text, zone text, reclassement text);
CREATE TABLE vacance(code_commune text, nom_departement text, nom_region text, nb_logements_prives integer, nb_logements_vacants integer, taux_vacance numeric, nb_vacants_plus_2ans integer, annee integer);
CREATE TABLE fiscalite_locale(code_insee text, nom_departement text, nom_region text, taux_tfpb numeric, taux_th_rs numeric, taux_teom numeric, nom_epci text, annee integer);
CREATE TABLE indicateurs_loyers(code_insee text, type_habitat text, loyer_m2 numeric, annee integer);
CREATE MATERIALIZED VIEW communes_lookup AS SELECT code, nom FROM geo_communes;
SQL

printf '%s\n' "$fixture_schema" | docker exec -i "$source_container" \
  psql -v ON_ERROR_STOP=1 -U postgres -d datacommune >/dev/null
printf '%s\n' "$fixture_schema" | docker exec -i "$target_container" \
  psql -v ON_ERROR_STOP=1 -U postgres -d datacommune_preprod >/dev/null

docker exec "$source_container" psql -v ON_ERROR_STOP=1 -U postgres -d datacommune -c "\
CREATE SEQUENCE adresses_id_seq OWNED BY adresses.id;\
ALTER TABLE adresses ALTER COLUMN id SET DEFAULT nextval('adresses_id_seq');\
INSERT INTO dataset_metadata VALUES ('ban','healthy',2);\
INSERT INTO geo_communes VALUES ('44109','Nantes'),('85191','La Roche-sur-Yon');\
INSERT INTO adresses(id,code_departement,numero,nom_voie,code_postal,code_commune,nom_commune,position) VALUES (1,'44','8','Rue du test','44000','44109','Nantes',ST_SetSRID(ST_MakePoint(-1.5,47.2),4326));\
INSERT INTO etl_publications(table_name,status,relation_oids) VALUES ('adresses','published',jsonb_build_object('adresses','999999','communes_lookup','888888'));\
REFRESH MATERIALIZED VIEW communes_lookup;" >/dev/null
docker exec "$target_container" psql -v ON_ERROR_STOP=1 -U postgres -d datacommune_preprod \
  -c "INSERT INTO dataset_metadata VALUES ('migration-baseline','unknown',NULL);" >/dev/null

read -r -d '' migration_addresses <<'SQL' || true
SET session_replication_role = replica;
INSERT INTO adresses(code_departement,numero,rep,nom_voie,code_postal,code_commune,nom_commune,position) VALUES
  ('75','1',NULL,'Place de la Concorde','75008','75108','Paris',ST_SetSRID(ST_MakePoint(2.3211,48.8656),4326)),
  ('75','10',NULL,'Rue de Rivoli','75001','75101','Paris',ST_SetSRID(ST_MakePoint(2.3522,48.8566),4326)),
  ('75','5',NULL,'Avenue des Champs-Elysees','75008','75108','Paris',ST_SetSRID(ST_MakePoint(2.3065,48.8698),4326)),
  ('13','1',NULL,'Quai du Port','13002','13202','Marseille',ST_SetSRID(ST_MakePoint(5.3698,43.2965),4326)),
  ('13','80',NULL,'La Canebiere','13001','13201','Marseille',ST_SetSRID(ST_MakePoint(5.3757,43.2977),4326)),
  ('2A','1',NULL,'Cours Napoleon','20000','2A004','Ajaccio',ST_SetSRID(ST_MakePoint(8.7386,41.9192),4326)),
  ('2A','18',NULL,'Boulevard du Roi Jerome','20000','2A004','Ajaccio',ST_SetSRID(ST_MakePoint(8.7350,41.9210),4326)),
  ('971','1',NULL,'Rue de la Liberte','97200','97209','Fort-de-France',ST_SetSRID(ST_MakePoint(-61.0588,14.6161),4326)),
  ('971','25',NULL,'Boulevard du General de Gaulle','97200','97209','Fort-de-France',ST_SetSRID(ST_MakePoint(-61.0614,14.6037),4326));
SET session_replication_role = origin;
SQL
printf '%s\n' "$migration_addresses" | docker exec -i "$target_container" \
  psql -v ON_ERROR_STOP=1 -U postgres -d datacommune_preprod >/dev/null

# Reproduce the production drift: a raw data restore includes a source sequence
# value for adresses_id_seq, which the same-revision target does not own.
docker exec "$source_container" pg_dump -U postgres -d datacommune \
  --format=custom --data-only --table-and-children public.adresses \
  --file /tmp/legacy-sequence.dump
docker cp "$source_container:/tmp/legacy-sequence.dump" "$tmp/legacy-sequence.dump" \
  >/dev/null
docker cp "$tmp/legacy-sequence.dump" "$target_container:/tmp/legacy-sequence.dump" \
  >/dev/null
if docker exec "$target_container" pg_restore -U postgres -d datacommune_preprod \
  --data-only --disable-triggers --single-transaction \
  /tmp/legacy-sequence.dump >/dev/null 2>"$tmp/raw-restore.err"; then
  echo "raw restore unexpectedly accepted the missing legacy sequence" >&2
  exit 1
fi
grep -Eq 'adresses_id_seq.*does not exist' "$tmp/raw-restore.err"
[[ $(docker exec "$target_container" psql -XAtq -U postgres \
  -d datacommune_preprod -c 'SELECT count(*) FROM adresses') == 9 ]]

printf 'postgresql://postgres:test-password@%s:5432/datacommune\n' "$source_container" >"$tmp/source.dsn"
printf 'postgresql://postgres:test-password@%s:5432/datacommune_preprod\n' "$target_container" >"$tmp/target.dsn"
chmod 0600 "$tmp"/*.dsn

docker run --rm --network "$network" \
  -v "$repo_dir:/repo:ro" -v "$tmp:/run/test" python:3.12-slim \
  sh -ceu 'apt-get update >/dev/null && apt-get install -y postgresql-client >/dev/null && pip install "psycopg[binary]" >/dev/null && cd /repo && python scripts/seed_preproduction.py --source-dsn-file /run/test/source.dsn --target-dsn-file /run/test/target.dsn --expected-revision test_revision --tables dataset_metadata geo_communes adresses etl_publications --report /run/test/report.json'

result=$(docker exec "$target_container" psql -At -U postgres -d datacommune_preprod -c "\
SELECT count(*) FROM geo_communes;\
SELECT count(*) FROM adresses;\
SELECT status FROM dataset_metadata WHERE dataset_name='ban';\
SELECT (relation_oids->>'adresses')::oid = 'adresses'::regclass::oid FROM etl_publications;\
SELECT (relation_oids->>'communes_lookup')::oid = 'communes_lookup'::regclass::oid FROM etl_publications;\
SELECT nextval('etl_publications_id_seq') > (SELECT max(id) FROM etl_publications);\
SELECT count(*) FROM communes_lookup;")
[[ "$result" == $'2\n1\nhealthy\nt\nt\nt\n2' ]]
python3 - "$tmp/report.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
assert p["schema_revision"] == "test_revision"
assert len(p["data_manifest_sha256"]) == 64
assert p["tables"]["geo_communes"]["rows"] == 2
PY

# A same-revision legacy matview can still encode a false zero. Repair must use
# the canonical application builder, preserve positive DVF evidence, and roll
# back all DDL if rebuilding fails.
docker exec "$target_container" psql -v ON_ERROR_STOP=1 -U postgres \
  -d datacommune_preprod -c "\
INSERT INTO users VALUES (1,'dev-only@example.test');\
INSERT INTO demographie(code_insee,nom_commune,code_departement,code_region,annee) VALUES\
('85165','L Oie','85','52',2023),('85212','Sainte-Florence','85','52',2023),\
('85084','Commune DVF','85','52',2023);\
INSERT INTO mutations_grain SELECT '85084','2025-01-01'::date + n,'residentiel_maison',true,false,1,100,196700 FROM generate_series(1,52) n;\
CREATE MATERIALIZED VIEW v_commune_dvf AS SELECT code_commune code_insee, count(*)::int nb_mutations_12m FROM mutations_grain GROUP BY code_commune;\
CREATE MATERIALIZED VIEW v_commune_synthese AS SELECT d.code_insee, COALESCE(v.nb_mutations_12m,0)::int nb_mutations_12m FROM demographie d LEFT JOIN v_commune_dvf v USING(code_insee);\
UPDATE etl_publications SET relation_oids = relation_oids || jsonb_build_object('v_commune_synthese','999998');" >/dev/null
legacy=$(docker exec "$target_container" psql -XAtq -U postgres \
  -d datacommune_preprod -c "SELECT code_insee||':'||nb_mutations_12m FROM v_commune_synthese ORDER BY 1")
[[ "$legacy" == $'85084:52\n85165:0\n85212:0' ]]

if docker run --rm --network "$network" -v "$repo_dir:/repo:ro" \
  -v "$tmp:/run/test" python:3.12-slim sh -ceu \
  'pip install "psycopg[binary]" >/dev/null && cd /repo && python scripts/seed_preproduction.py --target-dsn-file /run/test/source.dsn --expected-revision test_revision --mode repair-views --report /run/test/production-refused.json' \
  >/dev/null 2>&1; then
  echo "repair unexpectedly accepted the production database" >&2
  exit 1
fi

docker exec "$target_container" psql -v ON_ERROR_STOP=1 -U postgres \
  -d datacommune_preprod -c 'ALTER TABLE mutations_grain RENAME COLUMN nb_communes TO broken_nb_communes' >/dev/null
if docker run --rm --network "$network" -v "$repo_dir:/repo:ro" \
  -v "$tmp:/run/test" python:3.12-slim sh -ceu \
  'pip install "psycopg[binary]" >/dev/null && cd /repo && python scripts/seed_preproduction.py --target-dsn-file /run/test/target.dsn --expected-revision test_revision --mode repair-views --report /run/test/repair-failed.json' \
  >/dev/null 2>&1; then
  echo "broken canonical rebuild unexpectedly succeeded" >&2
  exit 1
fi
[[ $(docker exec "$target_container" psql -XAtq -U postgres \
  -d datacommune_preprod -c 'SELECT count(*) FROM v_commune_synthese') == 3 ]]
docker exec "$target_container" psql -v ON_ERROR_STOP=1 -U postgres \
  -d datacommune_preprod -c 'ALTER TABLE mutations_grain RENAME COLUMN broken_nb_communes TO nb_communes' >/dev/null

repair_views() {
  docker run --rm --network "$network" -v "$repo_dir:/repo:ro" \
    -v "$tmp:/run/test" python:3.12-slim sh -ceu \
    'pip install "psycopg[binary]" >/dev/null && cd /repo && python scripts/seed_preproduction.py --target-dsn-file /run/test/target.dsn --expected-revision test_revision --mode repair-views --report /run/test/repair-report.json'
}
repair_views
repair_views
repaired=$(docker exec "$target_container" psql -XAtq -U postgres \
  -d datacommune_preprod -c "\
SELECT code_insee||':'||COALESCE(nb_mutations_12m::text,'NULL') FROM v_commune_synthese WHERE code_insee IN ('85165','85212') ORDER BY 1;\
SELECT nb_mutations_12m||':'||nb_mutations_12m_maison||':'||nb_mutations_12m_appartement||':'||prix_m2_median FROM v_commune_synthese WHERE code_insee='85084';\
SELECT (relation_oids->>'v_commune_synthese')::oid = 'v_commune_synthese'::regclass::oid FROM etl_publications;\
SELECT (relation_oids->>'communes_lookup')::oid = 'communes_lookup'::regclass::oid FROM etl_publications;\
SELECT count(*) FROM matview_metadata WHERE last_refresh_status='success';")
[[ "$repaired" == $'85165:NULL\n85212:NULL\n52:52:0:1967\nt\nt\n9' ]]
python3 - "$tmp/repair-report.json" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
assert p["guarded_table_counts_before"] == p["guarded_table_counts_after"]
assert p["guarded_table_counts_after"]["users"] == 1
assert set(p["canonical_builders_sha256"]) == {
    "communes_lookup", "v_commune_synthese"
}
assert len(p["target_identity_hash"]) == 64
PY

echo "seed-preproduction e2e: PASS"
