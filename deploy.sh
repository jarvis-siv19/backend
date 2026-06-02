#!/usr/bin/env bash
#
# Deploy the SplitLLM API to Google Cloud Run.
#
# Prerequisites (one-time):
#   1. Install + auth the gcloud CLI:  https://cloud.google.com/sdk/docs/install
#        gcloud auth login
#        gcloud config set project "$PROJECT_ID"
#   2. Enable the required APIs:
#        gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
#          artifactregistry.googleapis.com secretmanager.googleapis.com
#   3. Create secrets for the sensitive values (run once; --data-file=- reads stdin):
#        printf '%s' 'mongodb+srv://USER:PASS@cluster.mongodb.net/?retryWrites=true' \
#          | gcloud secrets create MONGODB_URI   --data-file=-
#        printf '%s' 'YOUR_GEMINI_API_KEY' \
#          | gcloud secrets create GEMINI_API_KEY --data-file=-
#      (to rotate later: `gcloud secrets versions add MONGODB_URI --data-file=-`)
#
# Then deploy:  ./deploy.sh
# Override defaults with env vars, e.g.:  REGION=asia-south1 ./deploy.sh
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-splitllm-af10e}"
REGION="${REGION:-us-central1}"
SERVICE="${SERVICE:-splitllm-api}"

# Frontend origins allowed by CORS (comma-separated). Set to your Vercel URL(s).
CORS_ORIGINS="${CORS_ORIGINS:-https://your-app.vercel.app}"

# Non-secret config (Supabase keys are publishable/public by design).
SUPABASE_URL="${SUPABASE_URL:-https://jolggtuiatbxrjomfbxl.supabase.co}"
SUPABASE_ANON_KEY="${SUPABASE_ANON_KEY:-sb_publishable_Y-dpGB63ipwupP3Y3-6HAw_6d6w3wpO}"
DB_NAME="${DB_NAME:-splitllm}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-2.0-flash}"

echo "==> Deploying $SERVICE to Cloud Run ($PROJECT_ID / $REGION)"

gcloud run deploy "$SERVICE" \
  --source . \
  --project "$PROJECT_ID" \
  --region "$REGION" \
  --platform managed \
  --port 8080 \
  --memory 512Mi \
  --cpu 1 \
  --concurrency 80 \
  --min-instances 0 \
  --max-instances 4 \
  --timeout 60 \
  --allow-unauthenticated \
  --set-env-vars "DB_NAME=${DB_NAME},GEMINI_MODEL=${GEMINI_MODEL},SUPABASE_URL=${SUPABASE_URL},SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY},CORS_ORIGINS=${CORS_ORIGINS}" \
  --set-secrets "MONGODB_URI=MONGODB_URI:latest,GEMINI_API_KEY=GEMINI_API_KEY:latest"

echo "==> Done. Service URL:"
gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --format 'value(status.url)'
