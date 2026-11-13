# Gemuse

Self-hosted personal AI agent platform powered by **Gemini 4 Hydrogen** (`gemini-4-hydrogen`), featuring a three-layer persistent memory architecture, plugin-style MCP Hub, asynchronous background execution, Human-in-the-loop cryptographic approval gates, and defense-in-depth prompt injection protection.

## Architecture Overview (4-Step Roadmap)

- **Step 1 (Implemented)**: Core backend, Docker Compose stack (`postgres` with `pgvector`, `redis`, `backend`, `worker`), 3-layer memory & `MemoryConsolidator`, dynamic thinking-level router (`low` / `medium` / `high` / `xhigh`), SSE streaming & ARQ background jobs, HMAC-SHA256 one-time approval gates, FastAPI API, and full `pytest` suite.
- **Step 2 (Implemented)**: Unified `gemuse` branding, plugin-based `MCPHubManager` with graceful credential degradation & external `mcp_servers.json` loading, multi-server Discord integration (`config/discord_permissions.yaml`), unified Mail (Outlook Graph / iCloud IMAP+SMTP / Gmail), Google Workspace (Calendar & Drive), X & YouTube Data/Transcript connectors, and Official Gemini One-Click Bridge (`gemini_bridge` + Takeout importer).
- **Step 3 (Implemented)**: Isolated headful Chromium + Xvfb + x11vnc + noVNC sandbox (`browser-sandbox/`), `BrowserController` Computer Use loop with prompt-injection & checkout auto-pause guardrails, Take Control locks & authenticated noVNC reverse proxy (`/api/browser/*`), shared `/shared_files` upload/download bridge, APScheduler proactive morning briefings & event alerts (`config/proactive_rules.yaml`, Discord Webhook + Web Push VAPID, Redis 48h idempotency), and Twilio Media Streams <-> Gemini Live bidirectional voice bridge (`/api/voice/*` + approval-gated `voice_make_call`).
- **Step 4 (Planned)**: Next.js PWA frontend (`frontend/`).

## Quick Start

1. Copy `.env.example` to `.env` and fill in secure secrets (`API_SECRET_TOKEN`, `APPROVAL_HMAC_SECRET`, `GEMINI_API_KEY`, DB/Redis passwords, and any optional Step 2 service tokens).
2. Start the stack:
   ```bash
   docker compose up -d --build
   ```
3. Verify health:
   ```bash
   curl http://127.0.0.1:8005/health
   ```

## Running Tests

In Docker:
```bash
docker compose exec backend pytest -v
```

Or locally:
```bash
cd backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest -v
```

## License

MIT License - Copyright (c) 2026 じゅーかきん
