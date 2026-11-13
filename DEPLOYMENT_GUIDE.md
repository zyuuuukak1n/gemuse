# Deployment Guide (Generic)

This guide describes how to deploy **Gemuse** on any Linux host running Docker and Docker Compose v2.

## Prerequisites

- Docker Engine 24+ and Docker Compose v2
- Python 3.12+ (optional, for local test execution)
- A Google Gemini API key (`GEMINI_API_KEY`)

## 1. Clone and Configure Environment

```bash
git clone <your-repository-url> gemuse
cd gemuse
cp .env.example .env
```

Generate cryptographically strong secrets and update `.env`:

```bash
python3 -c "import secrets; print('API_SECRET_TOKEN=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('APPROVAL_HMAC_SECRET=' + secrets.token_urlsafe(48))"
python3 -c "import secrets; print('GEMUSE_INGEST_TOKEN=' + secrets.token_urlsafe(36))"
```

Update `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `DATABASE_URL`, `REDIS_URL`, `GEMINI_API_KEY`, and any optional Step 2 service credentials inside `.env`.

## 2. Host Directory Permissions

Because the containers run as non-root `appuser` (UID `1000` / GID `1000`), ensure `./data` is writable by UID `1000`:

```bash
mkdir -p ./data ./config
chown -R 1000:1000 ./data
chmod 750 ./data
```

## 3. Build and Start Services

```bash
docker compose up -d --build
docker compose ps
```

## 4. Verify Health & Run Tests

```bash
curl -s http://127.0.0.1:8005/health
docker compose exec backend pytest -v
```

Expected health response:

```json
{"status":"ok","service":"Gemuse"}
```

## 5. Reverse Proxy Recommendations

The backend binds strictly to `127.0.0.1:${BACKEND_PORT:-8005}`. Place a TLS-terminating reverse proxy (Nginx, Apache2, Caddy, or Cloudflare Tunnel) in front of `http://127.0.0.1:8005/` and ensure response buffering is disabled for Server-Sent Events (`/api/chat`).
