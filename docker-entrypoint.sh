#!/bin/sh
# In demo mode an empty database is filled with synthetic data (idempotent: restarts change nothing).
set -e
if [ "${OFIS_ENV}" = "demo" ]; then
  python -m app.seed_demo
fi
exec "$@"
