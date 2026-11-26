#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
compose up -d collector
compose logs --tail 20 collector
