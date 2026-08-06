---
description: Start the scraper web dashboard
agent: build
---

Run the scraper web dashboard.

1. Activate the venv: `.venv\Scripts\python.exe`
2. Run the launcher for auto-setup: `python launcher.py`
   - This creates/verifies the venv, installs deps, ensures Playwright browsers, and starts the web server
3. Or run directly: `python ultra_scraper.py web --port 9876`
4. Open http://127.0.0.1:9876 in a browser

The web UI has sections: Dashboard, Single Fetch, Bulk Scrape, Discovery, Results, Logs, Settings.
