# ---------------------------------------------------------------------------
# Backend image: FastAPI + OR-Tools CP-SAT
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# `curl` is used by the container health check.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first so that code changes do not invalidate the wheel layer.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod 0755 /app/entrypoint.sh

# Run as a non-root user and keep the writable state in /app/data.
RUN useradd --create-home --uid 10001 scheduler \
    && mkdir -p /app/data /app/data/uploads \
    && chown -R scheduler:scheduler /app
USER scheduler

ENV DATABASE_URL=sqlite:////app/data/app.db \
    DATA_DIR=/app/data \
    UPLOAD_DIR=/app/data/uploads \
    PYTHONPATH=/app

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=40s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
