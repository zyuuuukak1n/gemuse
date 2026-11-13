FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Create non-root user early
RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid appuser --shell /sbin/nologin --create-home appuser

# Install dependencies
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy application code
COPY app /app/app
COPY pytest.ini /app/pytest.ini
COPY tests /app/tests

# Prepare runtime directories with appropriate ownership
RUN mkdir -p /app/data /app/config /shared_files \
    && chown -R appuser:appuser /app /shared_files

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

