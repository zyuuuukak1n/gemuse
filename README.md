# Personal Muse

Self-hosted personal AI agent platform powered by **Gemini 4 Hydrogen** (`gemini-4-hydrogen`), featuring a three-layer persistent memory architecture, asynchronous background execution, Human-in-the-loop cryptographic approval gates, and defense-in-depth prompt injection protection.

## Architecture Overview (4-Step Roadmap)

- **Step 1 (Implemented)**: Core backend, Docker Compose stack (`postgres` with `pgvector`, `redis`, `backend`, `worker`), 3-layer memory & `MemoryConsolidator`, dynamic thinking-level router (`low` / `medium` / `high` / `xhigh`), SSE streaming & ARQ background jobs, HMAC-SHA256 one-time approval gates, FastAPI API, and full `pytest` suite.
- **Step 2 (Planned)**: Plugin-based MCP Hub for Discord, Email, and SNS (`config/mcp_servers.json`, `config/discord_permissions.yaml`).
- **Step 3 (Planned)**: Isolated noVNC browser sandbox (`browser-sandbox/`), manual handover, proactive briefings (`config/proactive_rules.yaml`), and voice bridge.
- **Step 4 (Planned)**: Next.js PWA frontend (`frontend/`).

## Directory Structure

```text
.
├── docker-compose.yml
├── .env.example
├── Makefile
├── config/
│   ├── mcp_servers.json
│   ├── discord_permissions.yaml
│   └── proactive_rules.yaml
├── data/
│   └── core_memory.md
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py
│   │   ├── core/       # config, JSON secret-masking logging, security, thinking router
│   │   ├── db/         # SQLAlchemy async session & ORM models
│   │   ├── agent/      # orchestrator, 3-layer memory, MemoryConsolidator
│   │   ├── worker/     # ARQ background worker tasks
│   │   ├── mcp_hub/    # Step 2 MCP abstract client & ToolRegistry
│   │   └── api/        # chat (SSE/background), jobs, memory, approval
│   └── tests/          # security, memory, orchestrator, API E2E tests
├── browser-sandbox/    # Step 3 placeholder
└── frontend/           # Step 4 placeholder
```

## Quick Start

1. Copy `.env.example` to `.env` and fill in secure secrets (`API_SECRET_TOKEN`, `APPROVAL_HMAC_SECRET`, `GEMINI_API_KEY`, DB/Redis passwords).
2. Start the stack:
   ```bash
   docker compose up -d --build
   ```
3. Verify health:
   ```bash
   curl http://127.0.0.1:8000/health
   ```

## Running Tests

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

