#!/usr/bin/env bash
# Thin wrapper around the project's alembic commands.
#
#   db.sh up                    -> alembic upgrade head
#   db.sh down                  -> alembic downgrade -1
#   db.sh new "message"         -> alembic revision --autogenerate -m "message"
#   db.sh current               -> alembic current
#
# DATABASE_URL must be set in the environment. See docs/persistence.md.

set -euo pipefail

require_database_url() {
  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "DATABASE_URL is not set. See docs/persistence.md." >&2
    exit 2
  fi
}

cmd="${1:-}"
case "$cmd" in
  up)
    require_database_url
    exec uv run alembic upgrade head
    ;;
  down)
    require_database_url
    exec uv run alembic downgrade -1
    ;;
  new)
    if [[ $# -lt 2 ]]; then
      echo "usage: db.sh new \"<message>\"" >&2
      exit 2
    fi
    require_database_url
    shift
    exec uv run alembic revision --autogenerate -m "$*"
    ;;
  current)
    require_database_url
    exec uv run alembic current
    ;;
  ""|-h|--help|help)
    cat <<'USAGE'
usage: db.sh <command>

commands:
  up                  alembic upgrade head
  down                alembic downgrade -1
  new "<message>"     alembic revision --autogenerate -m "<message>"
  current             alembic current

requires: DATABASE_URL set in the environment.
USAGE
    ;;
  *)
    echo "unknown command: $cmd" >&2
    exit 2
    ;;
esac
