"""In-tree PEP 517 build backend that packages the dashboard.

``index.html`` lives at the repository root because that is what Vercel deploys
(see ``vercel.json``) and what ``ultra_scraper.py web`` serves during local
development. setuptools can only ship data files that live *inside* a package,
so a wheel built straight from the root left the dashboard out entirely and
``scraper-serve`` answered ``{"error": "UI file not found"}``.

Rather than keeping a second 170 KB copy in version control and letting the two
drift, this backend stages the root ``index.html`` into
``src/scrapling_tool/web/static/`` immediately before delegating to setuptools.
The root file stays the single source of truth.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import build_meta as _setuptools

_ROOT = Path(__file__).resolve().parent.parent
_SOURCE = _ROOT / "index.html"
_STAGED = _ROOT / "src" / "scrapling_tool" / "web" / "static" / "index.html"


def _stage_dashboard() -> None:
    if not _SOURCE.is_file():
        raise FileNotFoundError(
            f"the dashboard source {_SOURCE} is missing; it is required to build a usable wheel"
        )
    _STAGED.parent.mkdir(parents=True, exist_ok=True)
    if _STAGED.is_file() and _STAGED.read_bytes() == _SOURCE.read_bytes():
        return
    shutil.copy2(_SOURCE, _STAGED)


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    _stage_dashboard()
    return _setuptools.build_wheel(wheel_directory, config_settings, metadata_directory)


def build_sdist(sdist_directory, config_settings=None):
    _stage_dashboard()
    return _setuptools.build_sdist(sdist_directory, config_settings)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    _stage_dashboard()
    return _setuptools.build_editable(wheel_directory, config_settings, metadata_directory)


# Everything else (metadata preparation, build requirements) is plain setuptools.
def get_requires_for_build_wheel(config_settings=None):
    _stage_dashboard()
    return _setuptools.get_requires_for_build_wheel(config_settings)


def get_requires_for_build_sdist(config_settings=None):
    _stage_dashboard()
    return _setuptools.get_requires_for_build_sdist(config_settings)


def get_requires_for_build_editable(config_settings=None):
    _stage_dashboard()
    return _setuptools.get_requires_for_build_editable(config_settings)
def prepare_metadata_for_build_wheel(metadata_directory, config_settings=None):
    _stage_dashboard()
    return _setuptools.prepare_metadata_for_build_wheel(metadata_directory, config_settings)


def prepare_metadata_for_build_editable(metadata_directory, config_settings=None):
    _stage_dashboard()
    return _setuptools.prepare_metadata_for_build_editable(metadata_directory, config_settings)
