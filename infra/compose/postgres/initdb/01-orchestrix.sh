#!/usr/bin/env bash
# Creates the Orchestrix roles and databases on first start (SPEC 21.2, plan M1).
# Passwords come from the environment and are passed as psql variables, never interpolated.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v app_pw="$ORCHESTRIX_APP_PASSWORD" \
  -v migrator_pw="$ORCHESTRIX_MIGRATOR_PASSWORD" \
  -v readonly_pw="$ORCHESTRIX_READONLY_PASSWORD" \
  -v litellm_pw="$LITELLM_DB_PASSWORD" \
  -v keycloak_pw="${KEYCLOAK_DB_PASSWORD:-$LITELLM_DB_PASSWORD}" \
  -v langfuse_pw="${LANGFUSE_DB_PASSWORD:-$LITELLM_DB_PASSWORD}" <<'SQL'
CREATE ROLE orchestrix_migrator LOGIN PASSWORD :'migrator_pw';
CREATE ROLE orchestrix_app LOGIN PASSWORD :'app_pw';
CREATE ROLE orchestrix_readonly LOGIN PASSWORD :'readonly_pw';
CREATE ROLE litellm LOGIN PASSWORD :'litellm_pw';
CREATE ROLE keycloak LOGIN PASSWORD :'keycloak_pw';
CREATE ROLE langfuse LOGIN PASSWORD :'langfuse_pw';

CREATE DATABASE orchestrix OWNER orchestrix_migrator;
CREATE DATABASE litellm OWNER litellm;
CREATE DATABASE keycloak OWNER keycloak;
CREATE DATABASE langfuse OWNER langfuse;

REVOKE ALL ON DATABASE orchestrix FROM PUBLIC;
GRANT CONNECT ON DATABASE orchestrix TO orchestrix_app, orchestrix_readonly;
SQL

# Extensions need a superuser; the migration's CREATE EXTENSION IF NOT EXISTS is then a no-op.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname orchestrix <<'SQL'
ALTER SCHEMA public OWNER TO orchestrix_migrator;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
