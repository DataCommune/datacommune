-- First part of the public state of a ZFS clone of the work cluster
-- (public-db-restore.sh --from-zfs-clone, step 6). Played in the public db container through
-- the local socket, connected to the maintenance database, outside any transaction:
-- DROP DATABASE cannot run in one. The clone carries the witness database of
-- the hypervisor's workbase-temoin.sh, which is never public (D-120, D-121).
-- It runs before public-reset.sql: a work role holding privileges in this database could
-- not be dropped otherwise.
DROP DATABASE IF EXISTS datacommune_temoin WITH (FORCE);
SELECT 'TEMOIN_DROPPED';
