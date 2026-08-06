---
description: Run a one-off scrape from the CLI
agent: build
---

Run a scrape command using the CLI tool. Examples:

Quick run (auto-detect platform):
```
python ultra_scraper.py run <url> -o results.json
```

HTTP GET with markdown extraction:
```
python ultra_scraper.py get <url> --extraction markdown
```

Browser fetch with JS rendering:
```
python ultra_scraper.py fetch <url> --no-headless --network-idle
```

Stealth fetch with Cloudflare solving:
```
python ultra_scraper.py stealth <url> --solve-cloudflare
```

Bulk scrape from a file:
```
python ultra_scraper.py bulk urls.txt --mode http --concurrency 10
```

Available modes: http (default), browser, stealth
Output formats: json (default), md, html, txt
