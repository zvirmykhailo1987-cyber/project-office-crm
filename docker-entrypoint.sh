#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Mykhailo Zvir
# Demo mode (set by docker-compose.yml, never the image default) fills an EMPTY database with
# synthetic data; seed_demo refuses any database that already has accounts or data.
set -e
if [ "${OFIS_ENV}" = "demo" ]; then
  python -m app.seed_demo
fi
exec "$@"
