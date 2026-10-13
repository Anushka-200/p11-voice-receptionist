#!/bin/bash
# Runs once, on first start of an empty data volume. Creates two login roles:
#   p11_migrator - owns the tables; used only for migrations (can change structure)
#   p11_app      - the API and worker; reads/writes rows, cannot create or drop tables
# Least privilege: a bug or injection in the app cannot drop or alter tables.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username postgres --dbname p11 <<SQL
CREATE ROLE p11_migrator LOGIN PASSWORD '${P11_MIGRATOR_PASSWORD}' NOSUPERUSER NOBYPASSRLS;
CREATE ROLE p11_app      LOGIN PASSWORD '${P11_APP_PASSWORD}'      NOSUPERUSER NOBYPASSRLS;

ALTER DATABASE p11 OWNER TO p11_migrator;
ALTER SCHEMA public OWNER TO p11_migrator;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO p11_app;

-- Tables that p11_migrator creates later automatically become readable/writable by the app.
ALTER DEFAULT PRIVILEGES FOR ROLE p11_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO p11_app;
ALTER DEFAULT PRIVILEGES FOR ROLE p11_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO p11_app;
SQL
