#!/usr/bin/env bash
# Deploy the SplitLLM FastAPI backend to IBM Cloud Code Engine (Chennai).
#
# Idempotent: creates the project / secret / app on first run and updates them
# on every run after that. Code Engine builds the image from ../Dockerfile
# using a local-source build, so there is no registry to manage by hand.
#
#   ./deploy.sh              # credit-funded sizing: always warm, no cold starts
#   ./deploy.sh --free       # free-tier sizing: scales to zero, cold starts
#
# Requires: ibmcloud CLI with the code-engine plugin, and `ibmcloud login`.
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_DIR=$(dirname "$SCRIPT_DIR")

REGION=${CE_REGION:-in-che}
RESOURCE_GROUP=${CE_RESOURCE_GROUP:-Default}
PROJECT=${CE_PROJECT:-splitllm}
APP=${CE_APP:-splitllm-api}
SECRET=${CE_SECRET:-splitllm-env}
ENV_FILE=${CE_ENV_FILE:-$SCRIPT_DIR/secrets.env}

# Sizing. The paid defaults keep one instance always warm — worth doing while
# the trial credit lasts, since it removes cold starts entirely. The free-tier
# numbers must scale to zero: 100,000 vCPU-sec/month only buys ~111 hours at
# 0.25 vCPU, against 730 hours in a month.
if [[ "${1:-}" == "--free" ]]; then
    CPU=0.25; MEMORY=0.5G; MIN_SCALE=0; MAX_SCALE=3
    echo "==> Free-tier sizing: ${CPU} vCPU / ${MEMORY}, scales to zero"
else
    CPU=0.5; MEMORY=1G; MIN_SCALE=1; MAX_SCALE=5
    echo "==> Credit sizing: ${CPU} vCPU / ${MEMORY}, always warm"
fi

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found." >&2
    echo "       cp $SCRIPT_DIR/secrets.example.env $ENV_FILE and fill it in." >&2
    exit 1
fi

# Code Engine's --from-env-file rejects `KEY=` with an unhelpful "not of format
# KEY=VALUE" error naming only a line number. Catch it here instead. Absent
# keys are fine: app/config.py defaults every optional setting to "".
EMPTY_KEYS=$(grep -nE '^[A-Za-z_][A-Za-z0-9_]*=[[:space:]]*$' "$ENV_FILE" || true)
if [[ -n "$EMPTY_KEYS" ]]; then
    echo "ERROR: $ENV_FILE has keys with empty values:" >&2
    echo "$EMPTY_KEYS" | sed 's/^/       line /' >&2
    echo >&2
    echo "       Give each a value, or comment it out with '#' — Code Engine" >&2
    echo "       rejects empty values, and an absent key falls back to the" >&2
    echo "       same default anyway." >&2
    exit 1
fi

# ─── Target region + resource group ─────────────────────────────────────
echo "==> Targeting $REGION / $RESOURCE_GROUP"
ibmcloud target -r "$REGION" -g "$RESOURCE_GROUP" >/dev/null

# ─── Project ────────────────────────────────────────────────────────────
# `project get` exits non-zero when the project does not exist yet.
if ibmcloud ce project get --name "$PROJECT" >/dev/null 2>&1; then
    echo "==> Selecting existing project '$PROJECT'"
    ibmcloud ce project select --name "$PROJECT" >/dev/null
else
    echo "==> Creating project '$PROJECT' (takes a minute or two)"
    ibmcloud ce project create --name "$PROJECT"
    ibmcloud ce project select --name "$PROJECT" >/dev/null
fi

# ─── Secret ─────────────────────────────────────────────────────────────
# Every key becomes an env var on the app. Updating the secret alone does not
# restart the app — the `app update` below picks the new values up.
if ibmcloud ce secret get --name "$SECRET" >/dev/null 2>&1; then
    echo "==> Updating secret '$SECRET'"
    ibmcloud ce secret update --name "$SECRET" --from-env-file "$ENV_FILE" >/dev/null
else
    echo "==> Creating secret '$SECRET'"
    ibmcloud ce secret create --name "$SECRET" --from-env-file "$ENV_FILE" >/dev/null
fi

# ─── App ────────────────────────────────────────────────────────────────
# --port 8080 matches the Dockerfile's default; Code Engine also injects $PORT,
# which the image's CMD already honours.
#
# Readiness hits /ready (503 while Mongo is unreachable, so an instance only
# joins the load balancer once it can actually serve). Liveness hits /health,
# which stays 200 as long as the process is alive — restarting a container
# cannot fix a database outage, so liveness must not depend on Mongo.
#
# The probe tuning below is load-bearing. Code Engine's defaults are
# initial-delay=0, timeout=1, failure-threshold=1 — so the very first liveness
# check fires before the lifespan has connected to Mongo, gets a 500, and kills
# the container on a single failure. That restart loop is what makes a deploy
# report "container exited with no error". Readiness is the probe that should
# gate slow startup; liveness only needs to catch a wedged process.
#
# Code Engine caps initial-delay at 10s, so the startup grace cannot come from
# the delay alone — it comes from interval x failure-threshold. Liveness first
# checks at t=10 and only restarts after 3 consecutive failures (~t=70), which
# is far more than the few seconds the lifespan needs. Each probe's timeout
# also has to exceed the matching ping budget in the endpoint it hits (1s for
# /health, 3s for /ready), or the probe times out before the app can answer.
COMMON_ARGS=(
    --port 8080
    --cpu "$CPU"
    --memory "$MEMORY"
    --min-scale "$MIN_SCALE"
    --max-scale "$MAX_SCALE"
    --concurrency 50
    --request-timeout 300
    --env-from-secret "$SECRET"
    --probe-ready type=http --probe-ready path=/ready --probe-ready port=8080
    --probe-ready initial-delay=2 --probe-ready interval=10
    --probe-ready timeout=5 --probe-ready failure-threshold=10
    --probe-live type=http --probe-live path=/health --probe-live port=8080
    --probe-live initial-delay=10 --probe-live interval=30
    --probe-live timeout=5 --probe-live failure-threshold=3
)

if ibmcloud ce app get --name "$APP" >/dev/null 2>&1; then
    echo "==> Updating app '$APP' (rebuilding from local source)"
    ibmcloud ce app update --name "$APP" \
        --build-source "$REPO_DIR" \
        --build-dockerfile Dockerfile \
        --build-strategy dockerfile \
        "${COMMON_ARGS[@]}"
else
    echo "==> Creating app '$APP' (first build pulls the Python base image)"
    ibmcloud ce app create --name "$APP" \
        --build-source "$REPO_DIR" \
        --build-dockerfile Dockerfile \
        --build-strategy dockerfile \
        "${COMMON_ARGS[@]}"
fi

URL=$(ibmcloud ce app get --name "$APP" --output url)
echo
echo "==> Deployed: $URL"
echo "    Health:   curl $URL/health"
echo "    Version:  curl $URL/version"
echo
echo "    If this is the first deploy, add $URL to CORS_ORIGINS in"
echo "    $ENV_FILE and re-run, or the Flutter web build will be blocked."
