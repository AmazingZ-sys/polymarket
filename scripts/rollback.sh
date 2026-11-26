#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
release_lock "$@"
point=.runtime/rollback-point
[[ -s "$point" ]] || { echo 'No verified application rollback point' >&2; exit 1; }
old=$(sed -n '1p' "$point")
[[ "$old" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
schema=$(schema_id polyquant)
[[ "$schema" == "$(sed -n '2p' "$point")" || "$schema" == "$(sed -n '3p' "$point")" ]] || {
  echo 'Live schema does not match the verified rollback point. Use isolated restore.' >&2; exit 1;
}
for svc in api web worker; do docker image inspect "$(value IMAGE_PREFIX)-$svc:$old" >/dev/null; done
APP_VERSION="$old" compose up -d --pull never --wait --wait-timeout 180
APP_VERSION="$old" scripts/healthcheck.sh
write_version "$old"
