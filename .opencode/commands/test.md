---
description: Run the test suite
agent: build
---

Run the project tests using pytest.

Quick run:
```
python -m pytest tests/ -v
```

Stop on first failure:
```
python -m pytest tests/ -x -v
```

Run a specific test file:
```
python -m pytest tests/cli/test_cli.py -v
```

Run with coverage:
```
python -m pytest tests/ --cov=scrapling --cov-report=term
```

Using tox (multi-env):
```
python -m tox
```

Note: Some tests may require Playwright browsers installed. Run `python -m playwright install chromium` if needed.
