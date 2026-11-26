#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
seconds=${1:-300}
if [[ $# -gt 1 || ! "$seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo 'Usage: scripts/collect-data.sh [positive duration in seconds]' >&2
  exit 2
fi
compose run --rm --no-deps -T collector python -m pqcollector once --seconds "$seconds"
