FROM python:3.12-slim-trixie

LABEL org.opencontainers.image.title="scrapling-tool" \
      org.opencontainers.image.description="Hardened Scrapling-powered CLI/MCP/dashboard for social-media and generic site scraping" \
      org.opencontainers.image.licenses="BSD-3-Clause"

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependency manifests first, for layer caching.
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --all-extras

# Source code
COPY . .

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt \
    apt-get update && \
    uv run playwright install-deps chromium && \
    uv run playwright install chromium && \
    uv sync --locked --all-extras && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# HF Spaces uses port 7860
EXPOSE 7860 8080 8000

# HF Spaces sets PORT=7860. We use it for the web dashboard.
ENTRYPOINT uv run scrape web --host 0.0.0.0 --port ${PORT:-8080}
