---
description: Run linters and formatters
agent: build
---

Run code quality checks.

Ruff lint:
```
python -m ruff check .
```

Ruff format (check mode):
```
python -m ruff format --check .
```

Ruff format (auto-fix):
```
python -m ruff format .
```

Security audit:
```
python -m bandit -c pyproject.toml -r scrapling/ ultra_scraper.py
```

Type checking:
```
python -m mypy scrapling/ ultra_scraper.py --ignore-missing-imports
```
