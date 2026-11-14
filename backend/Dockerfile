# syntax=docker/dockerfile:1
# Backend image: multi-stage, non-root, health-checked. No secrets are baked in;
# GEMINI_API_KEY is injected at runtime (compose environment / secret).

FROM python:3.12-slim AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY backend/requirements.txt .
# Test-only pins (pytest, hypothesis, respx) are not needed at runtime.
RUN grep -vE '^(pytest|hypothesis|respx)==' requirements.txt > runtime-requirements.txt \
 && python -m venv /opt/venv \
 && /opt/venv/bin/pip install -r runtime-requirements.txt

FROM python:3.12-slim AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE_URL=sqlite:////data/db/weatherwear.db \
    PHOTO_DIR=/data/photos
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home app \
 && mkdir -p /app/backend /app/data/demo /data/db /data/photos \
      /data/gallery /data/user/photos /data/secrets \
 && chown -R app:app /data
COPY --from=builder /opt/venv /opt/venv
# app.main resolves REPO_ROOT as parents[2] of backend/app/main.py -> /app.
COPY backend/app /app/backend/app
COPY data/demo/fixtures /app/data/demo/fixtures
COPY data/demo/scenarios.json /app/data/demo/scenarios.json
WORKDIR /app/backend
USER app
EXPOSE 8000
VOLUME ["/data/db", "/data/secrets"]
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/meta', timeout=2).status == 200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
