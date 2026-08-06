--- HOW TO START THE SCRAPLING TOOL ---

1. Double-click "scraper_panel.bat"
   - It will automatically set up the environment.
   - It will install missing browsers.
   - It will start the server on port 8080.
   - It will open the Dashboard in your browser.

2. Alternative: Run from terminal
   If you prefer the command line, run:
   python launcher.py

The Web Dashboard allows you to:
- Scrape any URL (HTTP, Browser, or Stealth modes)
- Perform Bulk Scrapes
- Discover influencers/accounts by keyword
- Post Finder: find a user's posts/videos/reels containing a
  #hashtag or @mention (returns post URLs only, no scraping).
  Also available from the command line, e.g.:
    python ultra_scraper.py posts USERNAME -t "@brandname" -p instagram -o urls.txt
  For big creator lists, paste all usernames in the Post Finder tab and
  turn OFF "Search engines" (Instagram requests are auto-paced to avoid
  rate limits, so large lists take a few minutes).
- Export results to JSON, CSV, Excel, or PDF

Everything is pre-configured and ready to use.
