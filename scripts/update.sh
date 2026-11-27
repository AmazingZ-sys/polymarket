#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
version=${1:?Provide the semantic release version}
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
[[ $# == 1 || ( $# == 2 && $2 == --local-images ) ]] || exit 2
release_lock "$@"
preflight
old=$(value APP_VERSION)
[[ "$old" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
[[ "$old" != "$version" ]] || { scripts/healthcheck.sh; exit; }
[[ $(df -Pk . | awk 'NR==2 {print $4}') -ge 2097152 ]] || { echo 'Require 2 GiB free' >&2; exit 1; }
prefix=$(value IMAGE_PREFIX)
if [[ ${2:-} == --local-images ]]; then
  for svc in api web worker; do docker image inspect "$prefix-$svc:$version" >/dev/null; done
else
  [[ "$prefix" == ghcr.io/* ]] || { echo 'Configure the GHCR prefix, or use --local-images with preloaded release images.' >&2; exit 1; }
  APP_VERSION="$version" compose pull migrate api web
fi
backup=$(scripts/backup.sh)
scratch=pq_upgrade_$(date -u +%s)_$(openssl rand -hex 3)
compose exec -T postgres createdb -U pq "$scratch"
trap 'compose exec -T postgres dropdb --if-exists -U pq "$scratch" >/dev/null' EXIT
compose exec -T postgres pg_restore -U pq --no-owner --no-acl --exit-on-error --single-transaction -d "$scratch" < "$backup/db.dump"
before=$(schema_id "$scratch")
APP_VERSION="$version" compose run -T --rm --no-deps -e PQ_UPGRADE_DB="$scratch" -e PQ_PREVIOUS_VERSION="$old" migrate python - < scripts/check-release-schema.py
after=$(schema_id "$scratch")
compose exec -T postgres psql -v ON_ERROR_STOP=1 -U pq -d "$scratch" < scripts/ledger-check.sql
[[ $(schema_id polyquant) == "$before" ]] || { echo 'Database changed during update preflight' >&2; exit 1; }
if command -v nginx >/dev/null; then nginx -t; fi
pause_live_for_upgrade
# Only advance the checkpoint after schema and archive preflight succeeded.
checkpoint=$(mktemp .runtime/rollback-point.XXXXXX)
printf '%s\n' "$old" "$before" "$after" "$backup" > "$checkpoint"
mv "$checkpoint" .runtime/rollback-point
write_version "$version"
if ! compose up -d --pull never --wait --wait-timeout 180 || ! scripts/healthcheck.sh; then
  echo 'Candidate failed health checks; attempting verified application rollback' >&2
  scripts/rollback.sh || { echo "Rollback failed; recover from validated backup $backup into an isolated database." >&2; exit 1; }
  exit 1
fi
