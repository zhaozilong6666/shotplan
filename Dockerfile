# Pinned to the multi-arch image index: the `bookworm` tag is a moving target,
# and a rebuild silently changing the base image is how "works on my machine"
# starts. Bump with:
#   docker buildx imagetools inspect python:3.12-slim-bookworm
# Digest verified against mirror.gcr.io on 2026-11-13.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHOTPLAN_PROVIDER=mock \
    SHOTPLAN_DB_PATH=/app/data/shotplan.db

WORKDIR /app

COPY pyproject.toml README.md LICENSE demo_app.py ./
COPY src ./src

RUN useradd --create-home --uid 1000 appuser \
    && python -m pip install --no-cache-dir -e ".[api]" \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

# demo_app.py reads $PORT, so the same image works on any single-port host
# (Hugging Face Spaces injects 7860, Render injects its own port). It also
# pins the provider to mock, so a public demo never spends API credit.
CMD ["python", "demo_app.py"]
