#!/bin/sh
set -eu

# Persist a failed seed attempt across container restarts.
: > "${PGDATA:?PGDATA must be set}/.text_to_sql_seed_pending"
exec seed-db seed
