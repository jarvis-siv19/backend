# SplitLLM Backend

Modern async FastAPI backend for the SplitLLM expense-splitting app with Supabase JWT authentication, MongoDB, and Google Gemini LLM integration.

## Tech Stack

- **Python 3.12+** with **FastAPI**
- **Motor** (async MongoDB driver)
- **Supabase JWT** verification (asymmetric, via the project's public JWKS)
- **Google Gemini** (`google-genai`) for natural language expense parsing
- **Pydantic v2** for request/response validation

## Quick Start

### 1. Prerequisites

- Python 3.12+
- MongoDB (local or Atlas)
- A Supabase project (for auth)
- Google Gemini API key

### 2. Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies (runtime + test tooling)
pip install -r requirements-dev.txt   # or `-r requirements.txt` for runtime only

# Configure environment
cp .env.example .env
# Edit .env with your credentials (MONGODB_URI, SUPABASE_*, GEMINI_API_KEY)
```

Auth is stateless: the API validates the Supabase access token (Bearer JWT) sent
by the client against `${SUPABASE_URL}/auth/v1/.well-known/jwks.json` and reads the
user id from the `sub` claim. No service-account file is required.

### 3. Run

```bash
# Development (with auto-reload)
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload

# Production-style
uvicorn app.main:app --host 0.0.0.0 --port 8081 --workers 4
```

### 4. Docker (local)

```bash
docker-compose up -d   # starts the API + a local MongoDB
```

## Deploy to Google Cloud Run

The API ships as a container ([`Dockerfile`](./Dockerfile)) that listens on `$PORT`
(Cloud Run injects `8080`). It scales to zero, so idle cost is \$0 — well within the
free tier at ~1000 users.

**Database:** create a free **MongoDB Atlas M0** cluster, add a database user, and
(for the M0 free tier) allow access from `0.0.0.0/0` (auth is via the connection
string). Use the SRV connection string as `MONGODB_URI`.

**One-time GCP setup** and deploy are scripted in [`deploy.sh`](./deploy.sh):

```bash
# Enable APIs + create secrets (see the header of deploy.sh for the exact commands),
# then:
CORS_ORIGINS="https://your-app.vercel.app" ./deploy.sh
```

What the script does:
- builds the image from source (Cloud Build) and deploys to Cloud Run,
- `--min-instances 0 --max-instances 4` (scale to zero),
- `--allow-unauthenticated` — the browser frontend calls the API directly; endpoints
  are protected by Supabase JWT validation,
- secrets (`MONGODB_URI`, `GEMINI_API_KEY`) come from **Secret Manager**; the public
  Supabase keys + `CORS_ORIGINS` are plain env vars.

After deploying, set the Flutter app's `API_BASE_URL` (Vercel env var) to the printed
Cloud Run URL, and make sure that Vercel URL is in `CORS_ORIGINS`.

> **Service-to-service:** another backend can call this API at its public URL. To make
> a call privately/without exposing it, grant that service's account the
> `roles/run.invoker` role and send a Google-signed ID token — Cloud Run handles the
> IAM check. (Keep `--allow-unauthenticated` while the browser frontend needs direct
> access.)

## API Documentation

Once running, visit:
- **Swagger UI**: http://localhost:8081/docs
- **ReDoc**: http://localhost:8081/redoc

## API Endpoints (22 total)

| Category | Method | Endpoint | Auth |
|----------|--------|----------|------|
| Auth | POST | `/db/login` | ✅ |
| Auth | POST | `/db/signup` | ✅ |
| Auth | POST | `/db/logout` | ✅ |
| Auth | PUT | `/db/changePassword` | ✅ |
| Account | GET | `/db/user/account` | ✅ |
| Account | PUT | `/db/user/account` | ✅ |
| Dashboard | POST | `/db/dashboard/summary` | ✅ |
| Dashboard | POST | `/db/dashboard/chart` | ✅ |
| Events | GET | `/db/user/events` | ✅ |
| Events | GET | `/db/event/:id` | ✅ |
| Events | POST | `/db/event` | ✅ |
| Events | PUT | `/db/event` | ✅ |
| Events | DELETE | `/db/event/:id` | ✅ |
| Events | GET | `/db/event/:id/expenses` | ✅ |
| Events | GET | `/db/user/event/:id/dues` | ✅ |
| Expenses | POST | `/db/expense` | ✅ |
| Expenses | GET | `/db/expense/:id` | ✅ |
| Expenses | PUT | `/db/expense/:id` | ✅ |
| Expenses | DELETE | `/db/expense/:id` | ✅ |
| Context | GET | `/db/:type/:id/users` | ✅ |
| Personal | POST | `/db/expenses/personal` | ✅ |
| LLM | POST | `/llm/expense` | ✅ |
| Friends | GET | `/db/user/friends` | ✅ |
| Friends | POST | `/db/addFriend` | ✅ |
| Friends | DELETE | `/db/deleteFriend` | ✅ |
| Friends | GET | `/db/user/expense/friend/:id` | ✅ |
| Friends | POST | `/db/user/expense/friend/:id/settleup` | ✅ |
| Payments | GET | `/db/payment/:link` | ❌ |

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `MONGODB_URI` | MongoDB connection string (Atlas SRV in prod) | `mongodb://localhost:27017` |
| `DB_NAME` | Database name | `splitllm` |
| `SUPABASE_URL` | Supabase project URL (used to fetch the JWKS) | `https://your-project.supabase.co` |
| `SUPABASE_ANON_KEY` | Supabase publishable / anon key | (set per environment) |
| `GEMINI_API_KEY` | Google Gemini API key (**secret**) | (required for LLM) |
| `GEMINI_MODEL` | Gemini model name | `gemini-2.0-flash` |
| `CORS_ORIGINS` | Comma-separated allowed origins (add your Vercel URL) | `http://localhost:3000,...` |
| `HOST` | Server host (local only) | `0.0.0.0` |
| `PORT` | Server port (Cloud Run injects `8080`) | `8081` |

> On Cloud Run, `MONGODB_URI` and `GEMINI_API_KEY` are provided via **Secret Manager**;
> the rest are plain env vars. Locally they all come from `.env`.
