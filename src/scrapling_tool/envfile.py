"""Load ``.env`` into the process environment.

Nothing in this project read ``.env``, so every key an operator put there was
silently ignored and each provider reported itself unconfigured. Adding a
dependency for twenty lines of parsing is not worth it, and python-dotenv is
not currently in the requirements, so this is a deliberate small reimplementation.

Existing environment variables always win: a value exported in the shell, or set
by a host's configuration UI, is a more specific instruction than a file
checked into a working directory.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

_log = logging.getLogger(__name__)

__all__ = ["load_env_file", "parse_env"]


def parse_env(text: str) -> dict[str, str]:
    """Parse ``.env`` content into a mapping.

    Supports ``KEY=value``, ``export KEY=value``, ``#`` comments, blank lines,
    and single- or double-quoted values. Does not do variable interpolation —
    the tool has no use for it, and it is the part of dotenv parsing that most
    often surprises people.
    """
    out: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()

        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue

        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        else:
            # Only strip trailing comments from unquoted values — a quoted value
            # is allowed to contain a '#', and passwords frequently do.
            hash_at = value.find(" #")
            if hash_at != -1:
                value = value[:hash_at].rstrip()

        out[key] = value

    return out


def load_env_file(path: str | Path | None = None, *, override: bool = False) -> int:
    """Load ``path`` (default: ``.env`` at the project root) into ``os.environ``.

    Returns how many variables were set. Missing or unreadable files are not an
    error — running without a ``.env`` is normal.
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent.parent / ".env"
    path = Path(path)

    if not path.is_file():
        return 0

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _log.warning("could not read %s: %s", path, exc)
        return 0

    applied = 0
    for key, value in parse_env(text).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied += 1

    if applied:
        _log.info("loaded %d variables from %s", applied, path.name)
    return applied
