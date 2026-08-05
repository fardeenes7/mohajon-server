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

	-- 2. Grant permissions to the application user
	GRANT ALL PRIVILEGES ON DATABASE "$POSTGRES_DB" TO "$APP_DB_USER";
	GRANT ALL PRIVILEGES ON SCHEMA public TO "$APP_DB_USER";

	-- 3. Hardening: Ensure mohajon has access to future tables
	-- This is critical for Django migrations run by the superuser.
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "$APP_DB_USER";
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "$APP_DB_USER";
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO "$APP_DB_USER";

	-- 4. Enable pgvector
	CREATE EXTENSION IF NOT EXISTS vector;
EOSQL
