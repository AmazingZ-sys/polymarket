#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
umask 077
compose(){ docker compose -f docker-compose.yml "$@"; }
value(){ awk -F= -v k="$1" '$1 == k {sub(/^[^=]*=/,""); print; exit}' .env; }
preflight(){ command -v docker >/dev/null; docker info >/dev/null; compose config -q; }
# Serialize update / rollback across processes; the inherited descriptor releases on termination.
release_lock(){
  mkdir -p .runtime
  if [[ -z ${PQ_RELEASE_LOCK_HELD:-} ]]; then
    exec python3 - "$0" "$@" <<'PY'
import fcntl,os,sys
fd=os.open('.runtime/release.lock',os.O_CREAT|os.O_RDWR,0o600)
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
os.set_inheritable(fd,True)
os.environ['PQ_RELEASE_LOCK_HELD']='1'
os.execv(sys.argv[1],sys.argv[1:])
PY
  fi
}
schema_id(){
  compose exec -T postgres psql -XAt -v ON_ERROR_STOP=1 -U pq -d "$1" -c "SELECT encode(sha256(convert_to(string_agg(name || '=' || sha256,E'\n' ORDER BY name),'UTF8')),'hex') FROM schema_migrations"
}
write_version(){
  local next
  next=$(mktemp .runtime/env.XXXXXX)
  awk -F= -v v="$1" '$1=="APP_VERSION"{print "APP_VERSION="v;found=1;next}{print} END{if(!found)print "APP_VERSION="v}' .env > "$next"
  mv "$next" .env
}
