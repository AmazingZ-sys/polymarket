#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
preflight
if [[ $(value IMAGE_PREFIX) == polyquant ]]; then
  docker compose -f docker-compose.yml -f docker-compose.dev.yml build migrate api web
else
  compose pull
fi
compose up -d --wait --wait-timeout 180
