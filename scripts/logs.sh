#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
compose logs --tail 100 "$@"
