#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
: "${1:?Pass a JSON file containing start_time, end_time and strategy_id}"
base=http://127.0.0.1:${WEB_PORT:-$(value WEB_PORT)}/api/v1
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
python3 - <<'PY' | curl -fsS -c "$jar" -H 'Content-Type: application/json' --data-binary @- "$base/auth/login" >/dev/null
import json
from pathlib import Path
values=dict(line.split('=',1) for line in Path('.env').read_text().splitlines() if '=' in line)
print(json.dumps(dict(username=values['ADMIN_USERNAME'],password=values['ADMIN_PASSWORD'])))
PY
curl -fsS -b "$jar" -H 'Content-Type: application/json' -H "Idempotency-Key: $(openssl rand -hex 16)" --data-binary "@$1" "$base/backtests"
