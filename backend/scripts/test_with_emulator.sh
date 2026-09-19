#!/usr/bin/env bash
# Runs the backend pytest suite with a local Firestore emulator (needs Java 11+ and
# `npm install` in infrastructure/firestore-rules-tests). Emulator-backed tests
# (tenant queries, transactional rate limiter) FAIL rather than skip in this mode.
# Local only: touches no cloud project.
#
#   PYTEST_ARGS="tests/storage tests/ratelimit -q" backend/scripts/test_with_emulator.sh
set -euo pipefail
cd "$(dirname "$0")/../../infrastructure/firestore-rules-tests"
export RATEGUARD_REQUIRE_EMULATOR=1
npx firebase emulators:exec --only firestore --project demo-rateguard-tests --config ../firebase.json \
  "cd ../../backend && python -m pytest ${PYTEST_ARGS:--q}"
