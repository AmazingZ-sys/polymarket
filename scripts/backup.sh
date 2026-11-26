#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
mkdir -p backups
tmp=$(mktemp -d backups/.partial.XXXXXX)
trap 'if [[ -d $tmp ]]; then rm -r -- "$tmp"; fi' EXIT
compose exec -T postgres pg_dump -U pq -d polyquant -Fc > "$tmp/db.dump"
# The file list must come from the same committed snapshot as the ledger.
compose exec -T postgres pg_restore -a -t dataset_versions -f - < "$tmp/db.dump" |
  python3 scripts/archive_manifest.py manifest > "$tmp/manifest.json"
python3 scripts/archive_manifest.py names "$tmp/manifest.json" |
  compose exec -T ops tar -C /data/parquet -czf - --verbatim-files-from -T - > "$tmp/parquet.tar.gz"
python3 scripts/archive_manifest.py verify "$tmp/manifest.json" "$tmp/parquet.tar.gz"
(cd "$tmp" && shasum -a 256 db.dump parquet.tar.gz > SHA256SUMS)
dest=backups/$(date -u +%Y%m%dT%H%M%SZ)-${tmp##*.}
mv "$tmp" "$dest"
echo "$dest"
