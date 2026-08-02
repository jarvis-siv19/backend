# Deploying the SplitLLM backend to IBM Cloud Code Engine

Code Engine runs the existing `../Dockerfile` unchanged. Nothing in `app/`
needed to change to move off AppSail — the only Catalyst-specific pieces were
`catalyst/appsail/app-config.json` and the `X_ZOHO_CATALYST_LISTEN_PORT`
start command, and neither is used here. The Catalyst deployment is left in
place so you can run both and cut over when you're satisfied.

Region is **Chennai (`in-che`)**, which Code Engine
[added in 2026](https://github.com/ibm-cloud-docs/codeengine/blob/master/regions.md) —
the only in-country option among the free container platforms.

## One-time setup

```sh
# CLI + plugin
brew install ibm-cloud-cli            # or: curl -fsSL https://clis.cloud.ibm.com/install/osx | sh
ibmcloud plugin install code-engine

ibmcloud login --sso                  # add -a https://cloud.ibm.com if needed

cp secrets.example.env secrets.env    # gitignored
$EDITOR secrets.env                   # fill in MONGODB_URI and the rest
```

`secrets.env` is the single source of truth for the app's environment. Every
key in it becomes an env var on the running app, matching the names
`app/config.py` already reads.

## Deploy

```sh
./deploy.sh          # 0.5 vCPU / 1 GB, min-scale 1 — always warm
./deploy.sh --free   # 0.25 vCPU / 0.5 GB, min-scale 0 — scales to zero
```

The script is idempotent: it creates the project, secret, and app on the first
run and updates them on every run after. The first build is slow (it pulls
`python:3.12-slim` and installs the wheels); later builds reuse layers.

After the first deploy, point the Flutter app at the printed URL: set the
GitHub Actions repo **variable** `API_BASE_URL` (Settings → Variables — the
workflow reads `vars.API_BASE_URL`, not a secret), and optionally the local
`flutter-app/.env`. No Dart changes are needed; `AppConstants.apiBaseUrl`
already resolves that key at runtime.

`CORS_ORIGINS` does *not* need the Code Engine URL — it lists the origins the
web build is served from (`splitllm.com`), not the backend's own address.
Native builds don't go through CORS at all.

## Sizing: credit month vs. free tier

The free tier is **100,000 vCPU-seconds and 200,000 GB-seconds per month**,
refreshing monthly and never expiring. That does *not* stretch to an
always-on instance: at 0.25 vCPU you get roughly 111 hours of active runtime
against 730 hours in a month. So on the free tier `min-scale` must be 0 and
you accept a cold start (a few seconds for Python + FastAPI + motor) on the
first request after an idle period.

While the trial credit is active, `min-scale 1` removes cold starts entirely.
Rough cost for always-on at 0.5 vCPU / 1 GB is on the order of **$40–60 for
the month** — comfortably inside $200, but treat that as an estimate and
confirm against the IBM Cloud cost estimator, since the published per-second
rates were not verifiable from the docs at the time of writing.

Watch actual usage during the credit month and decide from data:

```sh
ibmcloud ce app get --name splitllm-api           # instance counts, revisions
ibmcloud ce app logs --name splitllm-api --follow
```

## Known trade-off: Atlas IP allowlisting

Code Engine workloads run from a **large, dynamic, multi-tenant IP pool**.
There is a `GET /project/{id}/egress_ips` API, but
[IBM explicitly advises against](https://github.com/ibm-cloud-docs/codeengine/blob/master/ts-allowlist-app.md)
relying on it: the list is unstable, and allowlisting those addresses also
grants access to other tenants' workloads on the same pool.

So Atlas Network Access has to be `0.0.0.0/0`. Mitigate with:

- A dedicated Atlas DB user scoped to the `splitllm` database only, with a
  long random password (SCRAM + TLS is then the only thing standing in front
  of the data — which is the same posture Cloudflare Containers would give).
- Code Engine's **outbound CIDR restrictions** (Project settings →
  Connectivity → CIDR ranges), which limit what the project can reach
  *outward*. This is the opposite direction from an Atlas allowlist and is
  worth setting up, but note that pinning it tightly to Atlas is awkward:
  `mongodb+srv` resolves to AWS `ap-south-1` addresses that rotate, so you'd
  be maintaining a broad AWS range list rather than a few hosts.

An Oracle Always Free VM in Mumbai is the alternative that *does* give a
static egress IP for a precise Atlas allowlist — at the cost of owning the OS
and winning the Ampere A1 capacity lottery.

## Rollback

Code Engine keeps every revision:

```sh
ibmcloud ce revision list --app splitllm-api
ibmcloud ce app update --name splitllm-api --traffic <REVISION_NAME>=100
```

The Catalyst/AppSail deployment also remains deployable via
`catalyst/scripts/build_appsail.sh` if you need to fall all the way back.
