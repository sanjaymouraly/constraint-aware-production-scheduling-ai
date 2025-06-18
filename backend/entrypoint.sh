#!/usr/bin/env sh
# Fail fast: containers must not serve requests on a schema that has not been
# migrated.  Alembic runs against SQLite in the default stack and PostgreSQL
# in the production overlay.
set -eu

alembic upgrade head
exec "$@"
