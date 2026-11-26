#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
docker compose version >/dev/null
python3 - <<'PY'
import fcntl
import os
from pathlib import Path
import secrets
import tempfile

keys=("POSTGRES_PASSWORD","ADMIN_PASSWORD","GRAFANA_PASSWORD","PQ_API_PASSWORD","PQ_ENGINE_PASSWORD",
      "PQ_COLLECTOR_PASSWORD","PQ_OPS_PASSWORD")
# Serialize installers with a directory lock. Keep any existing nonempty values.
lock=os.open(".",os.O_RDONLY)
try:
    fcntl.flock(lock,fcntl.LOCK_EX)
    path=Path(".env")
    content=path.read_text() if path.exists() else Path(".env.example").read_text()
    lines=content.splitlines()
    for key in keys:
        locations=[i for i,line in enumerate(lines) if line.startswith(key+"=")]
        if len(locations)>1:
            raise SystemExit("Duplicate environment key "+key)
        if not locations:
            lines.append(key+"="+secrets.token_hex(24))
        elif lines[locations[0]]==key+"=":
            lines[locations[0]]=key+"="+secrets.token_hex(24)
    desired="\n".join(lines)+"\n"
    if not path.exists() or desired!=content:
        with tempfile.NamedTemporaryFile(dir=".",prefix=".env.tmp.",delete=False,mode="w") as tmp:
            tmp.write(desired)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp.name,path)
    path.chmod(0o600)
finally:
    os.close(lock)
PY
preflight
echo "Environment ready. Local administrator credentials are in .env."
