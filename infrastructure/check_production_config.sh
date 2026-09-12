#!/usr/bin/env bash
# RateGuard AI -- Production Revision Configuration Guard
#
# Read-only. Inspects the deployed environment of a Cloud Run revision (or,
# by default, whichever revision(s) currently carry production traffic for
# the given service) and FAILS if any environment variable VALUE contains
# the substring "-staging" or "_staging". This is the exact failure mode
# that let rateguard-api/rateguard-worker run in "production" against
# assurance-runs-staging / rateguard_staging / rateguard-ai-artifacts-staging
# undetected -- this script exists so that can never happen silently again.
#
# Usage:
#   infrastructure/check_production_config.sh <service-name> [--revision=<name>]
#
#   <service-name>        Required. e.g. rateguard-api, rateguard-worker, rateguard-web
#   --revision=<name>     Optional. Check this specific revision instead of
#                          whichever revision(s) currently serve traffic.
#
# Exit code 0: clean. Exit code 1: at least one staging value found (details
# printed). Exit code 2: usage/lookup error (service or revision not found).
#
# Never mutates anything -- only `gcloud run revisions describe` /
# `gcloud run services describe`, both read-only.

set -euo pipefail

REGION="us-central1"

SERVICE="${1:-}"
REVISION=""
for arg in "$@"; do
  case "$arg" in
    --revision=*) REVISION="${arg#*=}" ;;
  esac
done

if [ -z "$SERVICE" ]; then
  echo "Usage: $0 <service-name> [--revision=<name>]" >&2
  exit 2
fi

py=python3
if ! "$py" -c "" >/dev/null 2>&1; then
  py=python
fi

check_revision() {
  local revision="$1"
  local json
  if ! json=$(gcloud run revisions describe "$revision" --region "$REGION" --format=json 2>&1); then
    echo "Error: could not describe revision '$revision': $json" >&2
    exit 2
  fi
  echo "$json" | "$py" -c "
import json, sys

data = json.load(sys.stdin)
containers = data.get('spec', {}).get('containers', [])
env = (containers[0].get('env', []) if containers else [])

flagged = []
for entry in env:
    name = entry.get('name', '')
    value = str(entry.get('value', ''))
    if '-staging' in value or '_staging' in value:
        flagged.append((name, value))

if flagged:
    print('FAIL: revision \"$revision\" carries staging-named configuration:')
    for name, value in flagged:
        print(f'  {name} = {value}')
    sys.exit(1)
else:
    print('OK: revision \"$revision\" carries no staging-named configuration.')
"
}

if [ -n "$REVISION" ]; then
  check_revision "$REVISION"
  exit $?
fi

# No explicit revision given: check every revision currently receiving
# nonzero traffic on this service (there can be more than one during a
# gradual percentage-split promotion).
TRAFFIC_JSON=$(gcloud run services describe "$SERVICE" --region "$REGION" --format="json(status.traffic)" 2>&1) \
  || { echo "Error: could not describe service '$SERVICE': $TRAFFIC_JSON" >&2; exit 2; }

REVISIONS=$(echo "$TRAFFIC_JSON" | "$py" -c "
import json, sys
data = json.load(sys.stdin)
names = sorted({
    t['revisionName'] for t in data.get('status', {}).get('traffic', []) or []
    if t.get('percent', 0) and t.get('revisionName')
})
print('\n'.join(names))
")

if [ -z "$REVISIONS" ]; then
  echo "Error: service '$SERVICE' has no revision currently carrying traffic." >&2
  exit 2
fi

OVERALL=0
while IFS= read -r rev; do
  [ -z "$rev" ] && continue
  if ! check_revision "$rev"; then
    OVERALL=1
  fi
done <<< "$REVISIONS"

exit $OVERALL
