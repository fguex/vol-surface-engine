FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    TZ=UTC \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# 1. Dependances systeme : change presque jamais
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# 2. Dependances Python : le lockfile SEUL, avant le code
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 3. Code source : change a chaque iteration
COPY README.md ./
COPY python/ ./python/
RUN uv sync --frozen --no-dev

# 4. Utilisateur non-root
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "python.jobs.ingest", "--help"]

