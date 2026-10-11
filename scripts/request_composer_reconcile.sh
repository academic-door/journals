#!/usr/bin/env bash
# Only a request signal: ⑥ runner resolves private Composer main and deploys.
set -euo pipefail

if [ -z "${GH_TOKEN:-}" ]; then
  echo "::error::COMPOSER_RUNNER_DISPATCH_TOKEN is not available to this job."
  exit 1
fi

# No client_payload, private SHA/ref, or Cloudflare credentials cross the boundary.
gh api --method POST \
  --header "Accept: application/vnd.github+json" \
  repos/academic-door/academic-door-composer-runner/dispatches \
  --raw-field event_type=composer-reconcile >/dev/null

echo "COMPOSER_RECONCILE_REQUEST_ACCEPTED: GitHub repository_dispatch HTTP 204"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "COMPOSER_RECONCILE_REQUEST_ACCEPTED: request-only event sent to public runner." >> "$GITHUB_STEP_SUMMARY"
fi
