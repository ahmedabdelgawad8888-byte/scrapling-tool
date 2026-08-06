#!/usr/bin/env bash
# Streamlit Cloud runs this after installing requirements.txt
# Installs Playwright's Chromium browser (needed for browser/stealth modes)
set -e
echo ">>> Installing Playwright Chromium..."
python -m playwright install chromium 2>&1 || echo "WARNING: playwright install failed (browser/stealth modes won't work)"
echo ">>> Done."