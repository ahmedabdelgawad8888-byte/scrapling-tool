FROM python:3.12-slim

LABEL org.opencontainers.image.title="scrapling-tool" \
      org.opencontainers.image.description="Scrapling-powered scraping dashboard (Streamlit)" \
      org.opencontainers.image.licenses="BSD-3-Clause"

# Browsers land outside $HOME so the non-root runtime user can read them.
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    STREAMLIT_SERVER_PORT=7860

WORKDIR /app

# Dependency manifest first so edits to the app don't rebuild the whole stack.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# `--with-deps` pulls the exact apt libraries Chromium links against, so this
# stays correct across base-image bumps (packages.txt is for Streamlit Cloud,
# which has no such command).
# Two browsers on purpose: "browser" mode drives playwright, "stealth" mode
# drives patchright (scrapling/_stealth.py). Installing only one silently
# breaks the other mode at runtime.
RUN python -m playwright install --with-deps chromium && \
    python -m patchright install chromium && \
    chmod -R a+rX /ms-playwright && \
    rm -rf /var/lib/apt/lists/*

COPY . .

# Hugging Face Spaces runs containers as UID 1000.
RUN useradd -m -u 1000 user && chown -R user:user /app
USER user
ENV HOME=/home/user

EXPOSE 7860

# enableXsrfProtection is off because the Spaces proxy strips the origin header
# that Streamlit's XSRF check needs, which otherwise breaks the file uploader.
CMD ["streamlit", "run", "streamlit_app.py", \
     "--server.port=7860", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.enableCORS=false", \
     "--server.enableXsrfProtection=false"]
