---
description: Build the package for distribution
agent: build
---

Build the scrapling-tool package.

Requirements: `python -m pip install build`

Build:
```
python -m build
```

This creates `.tar.gz` (source) and `.whl` (wheel) in the `dist/` directory.

Install the built wheel:
```
python -m pip install dist/scrapling_tool-*.whl
```

Check the build:
```
python -m twine check dist/*
```
