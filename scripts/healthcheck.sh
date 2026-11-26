#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
curl -fsS --max-time 10 "http://127.0.0.1:${WEB_PORT:-$(value WEB_PORT)}/readyz"
compose exec -T postgres psql -v ON_ERROR_STOP=1 -U pq -d polyquant < scripts/ledger-check.sql
compose exec -T postgres psql -v ON_ERROR_STOP=1 -U pq -d polyquant -c 'SELECT source,status,last_success_at,last_error FROM data_source_status ORDER BY source'
