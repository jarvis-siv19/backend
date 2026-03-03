# SplitLLM Backend

Modern async FastAPI backend for the SplitLLM expense-splitting app with Firebase Authentication, MongoDB, and Google Gemini LLM integration.

## Tech Stack

- **Python 3.12+** with **FastAPI**
- **Motor** (async MongoDB driver)
- **Firebase Admin SDK** for token verification
- **Google Gemini** (`google-genai`) for natural language expense parsing
- **Pydantic v2** for request/response validation

## Quick Start

### 1. Prerequisites

- Python 3.12+
- MongoDB (local or Atlas)
- Firebase project with service account credentials
- Google Gemini API key

### 2. Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials
```

### 3. Firebase Setup

Set one of these:
- `FIREBASE_CREDENTIALS_PATH` in `.env` → path to your service account JSON
- `GOOGLE_APPLICATION_CREDENTIALS` env var → path to your service account JSON

### 4. Run

```bash
# Development (with auto-reload)
uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload

# Production
uvicorn app.main:app --host 0.0.0.0 --port 8081 --workers 4
```

### 5. Docker

```bash
docker-compose up -d
```

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
| `MONGODB_URI` | MongoDB connection string | `mongodb://localhost:27017` |
| `DB_NAME` | Database name | `splitllm` |
| `FIREBASE_CREDENTIALS_PATH` | Path to Firebase service account JSON | (uses default creds) |
| `GEMINI_API_KEY` | Google Gemini API key | (required for LLM) |
| `GEMINI_MODEL` | Gemini model name | `gemini-2.0-flash` |
| `CORS_ORIGINS` | Comma-separated allowed origins | `http://localhost:3000,...` |
| `HOST` | Server host | `0.0.0.0` |
| `PORT` | Server port | `8081` |
