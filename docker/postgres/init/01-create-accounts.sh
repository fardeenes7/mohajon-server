#!/bin/bash
set -e

# This script runs during the initial database creation.
# It creates the necessary roles and sets up permissions.

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
	-- 1. Create the application user (if it doesn't exist)
	DO \$\$
	BEGIN
	  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '$APP_DB_USER') THEN
	    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '$APP_DB_USER', '$APP_DB_PASSWORD');
	  ELSE
	    EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', '$APP_DB_USER', '$APP_DB_PASSWORD');
	  END IF;
	END
	\$\$;

	-- 2. Create the pgbouncer user (if it doesn't exist)
	DO \$\$
	BEGIN
	  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = '$PGBOUNCER_DB_USER') THEN
	    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '$PGBOUNCER_DB_USER', '$PGBOUNCER_DB_PASSWORD');
	  ELSE
	    EXECUTE format('ALTER ROLE %I LOGIN PASSWORD %L', '$PGBOUNCER_DB_USER', '$PGBOUNCER_DB_PASSWORD');
	  END IF;
	END
	\$\$;

	-- 3. Grant permissions to the application user
	GRANT ALL PRIVILEGES ON DATABASE "$POSTGRES_DB" TO "$APP_DB_USER";
	GRANT ALL PRIVILEGES ON SCHEMA public TO "$APP_DB_USER";

	-- 4. Grant connect permission to pgbouncer
	GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO "$PGBOUNCER_DB_USER";

	-- 5. Hardening: Ensure mohajon has access to future tables
	-- This is critical for Django migrations run by the superuser.
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "$APP_DB_USER";
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "$APP_DB_USER";
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO "$APP_DB_USER";

	-- 6. Grant read-only access to pgbouncer for its own needs (if any)
	-- Usually pgbouncer doesn't need data access, just connect.

	-- 7. Enable pgvector
	CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
