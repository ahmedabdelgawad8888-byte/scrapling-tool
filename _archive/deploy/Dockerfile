# Multi-stage build for optimal image size and security
FROM python:3.12-slim AS builder

# Set build environment variables. PLAYWRIGHT_BROWSERS_PATH must be set
# *before* the install step below: without it Playwright writes the browsers
# to ~/.cache/ms-playwright, the COPY into the runtime stage picks up an empty
# /ms-playwright, and browser/stealth modes fail at runtime with no Chromium.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Copy dependency manifest
COPY requirements.txt ./

# Install dependencies in a virtual environment for efficient layering
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel && \
    pip install -r requirements.txt

# Browser binaries only. The matching system libraries are installed in the
# runtime stage instead — apt packages here would be dropped with this layer.
RUN mkdir -p /ms-playwright && \
    python -m playwright install chromium && \
    python -m patchright install chromium && \
    chmod -R a+rX /ms-playwright

# Runtime stage - minimal final image
FROM python:3.12-slim

LABEL org.opencontainers.image.title="scrapling-tool" \
      org.opencontainers.image.description="Scrapling-powered scraping dashboard (Streamlit)" \
      org.opencontainers.image.version="1.3.0" \
      org.opencontainers.image.licenses="BSD-3-Clause" \
      org.opencontainers.image.source="https://github.com/D4Vinci/Scrapling"

# Runtime environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PORT=7860 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv

# Copy Playwright browsers from builder
COPY --from=builder /ms-playwright /ms-playwright

# Chromium's shared libraries (libnss3, libgbm1, …). The browser binaries
# copied above cannot launch without them, and the builder's --with-deps apt
# packages live in a layer this stage never sees.
RUN python -m playwright install-deps chromium && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd -m -u 1000 user && \
    chown -R user:user /app /ms-playwright && \
    chmod -R a+rX /ms-playwright

USER user
ENV HOME=/home/user

EXPOSE 7860

# Health check. Uses urllib rather than requests: requests is not in
# requirements.txt, so importing it here made the check fail permanently.
# start-period covers Streamlit's boot, which takes well over five seconds.
HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:7860/healthz', timeout=5)"

# FastAPI backend serving the single-page dashboard.
# One worker on purpose: jobs live in-process, so a second worker would answer
# /api/jobs/<id> for a job it has never heard of.
CMD ["uvicorn", "webapp.server:create_app", \
     "--factory", \
     "--host", "0.0.0.0", \
     "--port", "7860", \
     "--workers", "1"]
