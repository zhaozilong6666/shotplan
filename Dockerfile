FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHOTPLAN_PROVIDER=mock \
    SHOTPLAN_DB_PATH=/app/data/shotplan.db

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY web ./web
COPY evals ./evals
COPY docs ./docs

RUN python -m pip install --no-cache-dir -e ".[api]" \
    && mkdir -p /app/data

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"

CMD ["python", "-m", "shotplan.cli", "serve", "--host", "0.0.0.0", "--port", "8000"]
