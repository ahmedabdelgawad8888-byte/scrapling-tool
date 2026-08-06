FROM python:3.12-slim-trixie

# This image ships **scrapling-tool** (this fork), not upstream Scrapling.
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

# Dependency manifests first, for layer caching. uv.lock keeps the image
# reproducible; without it every rebuild re-resolves.
COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --all-extras

# Source. `_build/` (the in-tree build backend) and `index.html` (the
# dashboard) must both be present or the project install produces a wheel with
# no UI — see _build/backend.py.
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

# 8080: web dashboard (`scrape web`). 8000: MCP server HTTP transport.
EXPOSE 8080 8000

# `scrape` is this project's CLI. The bare `scrapling` command belongs to the
# upstream dependency and would have bypassed this tool entirely.
ENTRYPOINT ["uv", "run", "scrape"]

# Override with e.g. `web --host 0.0.0.0` or `mcp --http`.
CMD ["--help"]
