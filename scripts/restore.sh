#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
backup=${1:?Supply the backup directory}
(cd "$backup" && shasum -a 256 -c SHA256SUMS)
manifest=$(mktemp)
trap 'rm -f -- "$manifest"' EXIT
compose exec -T postgres pg_restore -a -t dataset_versions -f - < "$backup/db.dump" |
  python3 scripts/archive_manifest.py manifest > "$manifest"
python3 scripts/archive_manifest.py verify "$manifest" "$backup/parquet.tar.gz"
target=pq_restore_$(date -u +%Y%m%d%H%M%S)_$(openssl rand -hex 3)
project=$(compose config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')
volume="${project}_${target}"
compose exec -T postgres createdb -U pq "$target"
# Restore into a fresh database even on a replacement server with newly provisioned roles.
compose exec -T postgres pg_restore --no-owner --no-acl -U pq -d "$target" --exit-on-error --single-transaction < "$backup/db.dump"
docker volume create "$volume" >/dev/null
docker run --rm -i -v "$volume:/restore" postgres:17-alpine sh -ec 'tar -xz -C /restore; chown 10001 /restore' < "$backup/parquet.tar.gz"
compose exec -T postgres psql -v ON_ERROR_STOP=1 -U pq -d "$target" < scripts/ledger-check.sql
compose exec -T postgres psql -At -v ON_ERROR_STOP=1 -F ' ' -U pq -d "$target" -c 'SELECT sha256,file_path FROM dataset_versions ORDER BY first_seq' |
  docker run --rm -i -v "$volume:/restore:ro" -w /restore postgres:17-alpine sh -ec 'while read -r digest file; do printf "%s  %s\n" "$digest" "$file" | sha256sum -c -; done'
echo "Verified isolated database $target and archive volume $volume."
