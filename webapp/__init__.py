"""Scrapling Tool web app — FastAPI backend + single-page dashboard.

Replaces the Streamlit dashboard with a hand-built UI so results can stream in
live, runs can be cancelled mid-flight, and the layout isn't constrained by
widget chrome. ``streamlit_app.py`` is kept as a fallback front end.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(*args, **kwargs):
    """Lazy re-export so importing the package doesn't pull in FastAPI."""
    from webapp.server import create_app as _create_app

    return _create_app(*args, **kwargs)
