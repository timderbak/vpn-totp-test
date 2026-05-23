#!/usr/bin/env bash
set -euo pipefail

# TLS cert generation and uvicorn startup are wired in Task 3.
# For now: keep container alive so tests can run via `docker compose run --rm admin ...`.
exec "$@"
