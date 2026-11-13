FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DISPLAY=:99 \
    SCREEN_WIDTH=1280 \
    SCREEN_HEIGHT=800 \
    SCREEN_DEPTH=24 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    x11vnc \
    novnc \
    websockify \
    iptables \
    gosu \
    ca-certificates \
    curl \
    libatomic1 \
    fonts-noto-cjk \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi>=0.115.0 \
    uvicorn[standard]>=0.32.0 \
    playwright>=1.49.0 \
    pydantic>=2.9.0 \
    && apt-get update \
    && playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 --shell /bin/bash appuser \
    && mkdir -p /app /browser_profile /shared_files/uploads /shared_files/downloads /ms-playwright \
    && chown -R appuser:appuser /app /browser_profile /shared_files /ms-playwright

WORKDIR /app

COPY sandbox_server.py /app/sandbox_server.py
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh && chown appuser:appuser /app/sandbox_server.py

EXPOSE 6080 7000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:7000/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

