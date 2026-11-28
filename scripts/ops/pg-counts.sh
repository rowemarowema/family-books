#!/bin/sh
# Exact row count of every table in the public schema, plus the number of
# user triggers and public functions. Used to prove a dump/restore moved
# everything (e.g. the Dec 6 2026 Postgres 16 -> 17 cutover): run it on the
# old and new cluster and diff the two outputs.
#
# Runs INSIDE a postgres container and reads POSTGRES_USER / POSTGRES_DB from
# that container's own environment. It prints table names and counts only,
# never a credential:
#
#   docker exec -i family-books-postgres sh -s < scripts/ops/pg-counts.sh
set -eu

psql_at() {
    psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$1"
}

q=$(psql_at "select 'select x from (' || string_agg(format('select %L || '' '' || count(*) from public.%I', tablename, tablename), ' union all ' order by tablename) || ') s(x) order by 1' from pg_tables where schemaname = 'public'")
[ -n "$q" ] || { echo "pg-counts: no tables in schema public" >&2; exit 1; }

psql_at "$q"
psql_at "select 'zz_triggers ' || count(*) from pg_trigger where not tgisinternal"
psql_at "select 'zz_public_functions ' || count(*) from pg_proc p join pg_namespace n on n.oid = p.pronamespace where n.nspname = 'public'"
