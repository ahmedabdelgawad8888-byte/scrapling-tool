#!/usr/bin/env bash
# NOTE: Streamlit Community Cloud does NOT execute this file -- it only honours
# requirements.txt and packages.txt. streamlit_app.py installs Chromium at
# runtime instead (see _browser_ready). Kept for Docker/manual VPS setups.
# Installs Playwright's Chromium browser (needed for browser/stealth modes)
set -e
echo ">>> Installing Playwright Chromium..."
python -m playwright install chromium 2>&1 || echo "WARNING: playwright install failed (browser/stealth modes won't work)"
echo ">>> Done."