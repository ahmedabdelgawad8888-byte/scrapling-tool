"""Guards for how the dashboard and build metadata are packaged.

The wheel used to ship ``ultra_scraper.py`` (which serves ``index.html``)
without shipping ``index.html`` itself, so an installed ``scraper-serve``
answered ``{"error": "UI file not found"}``. ``_build/backend.py`` now stages
the root dashboard into the package at build time; these tests keep that wiring
honest.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
ROOT_DASHBOARD = REPO_ROOT / "index.html"

sys.path.insert(0, str(REPO_ROOT))


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_repo_has_a_dashboard() -> None:
    assert ROOT_DASHBOARD.is_file(), "index.html is the dashboard source of truth"
    assert ROOT_DASHBOARD.stat().st_size > 100_000


def test_build_backend_is_wired_up() -> None:
    """pyproject must route through the in-tree backend that stages the UI."""
    build_system = _pyproject()["build-system"]
    assert build_system["build-backend"] == "backend"
    assert build_system["backend-path"] == ["_build"]
    assert (REPO_ROOT / "_build" / "backend.py").is_file()


def test_build_backend_stages_the_dashboard(tmp_path: Path) -> None:
    """The staging step must copy the root dashboard byte-for-byte."""
    sys.path.insert(0, str(REPO_ROOT / "_build"))
    try:
        import backend  # type: ignore[import-not-found]
    finally:
        sys.path.pop(0)

    backend._stage_dashboard()
    assert backend._STAGED.is_file()
    assert backend._STAGED.read_bytes() == ROOT_DASHBOARD.read_bytes()


def test_only_one_ruff_config() -> None:
    """A stray ruff.toml silently overrides [tool.ruff] in pyproject.toml."""
    assert not (REPO_ROOT / "ruff.toml").exists()
    assert not (REPO_ROOT / ".ruff.toml").exists()
    assert "ruff" in _pyproject()["tool"]


def test_console_scripts_resolve() -> None:
    """Entry points must name callables that actually exist."""
    scripts = _pyproject()["project"]["scripts"]
    assert scripts == {
        "scrape": "ultra_scraper:cli",
        "scraper-mcp": "scraper_mcp:main",
        "scraper-serve": "ultra_scraper:serve_dashboard",
    }
    import ultra_scraper

    assert callable(ultra_scraper.cli)
    assert callable(ultra_scraper.serve_dashboard)


class TestDashboardResolution:
    """``ultra_scraper._dashboard_path`` across checkout / wheel / override."""

    def test_prefers_the_root_copy(self) -> None:
        import ultra_scraper

        assert ultra_scraper._dashboard_path() == ROOT_DASHBOARD

    def test_env_override_wins(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import ultra_scraper

        custom = tmp_path / "custom.html"
        custom.write_text("<h1>custom</h1>", encoding="utf-8")
        monkeypatch.setenv("SCRAPER_DASHBOARD", str(custom))
        assert ultra_scraper._dashboard_path() == custom

    def test_falls_back_to_the_packaged_copy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Simulates an installed wheel, where no index.html sits beside the module."""
        import ultra_scraper

        monkeypatch.delenv("SCRAPER_DASHBOARD", raising=False)
        real_is_file = Path.is_file

        def missing_at_root(self: Path) -> bool:
            if self == ROOT_DASHBOARD:
                return False
            return real_is_file(self)

        monkeypatch.setattr(Path, "is_file", missing_at_root)

        resolved = ultra_scraper._dashboard_path()
        assert resolved.name == "index.html"
        assert resolved.parent.name == "static"
        assert "scrapling_tool" in resolved.parts
        assert resolved.is_file()

    def test_resolution_does_not_need_fastapi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """scrapling_tool.web.__init__ imports FastAPI; the UI lookup must not.

        Importing the web subpackage to find a static file made dashboard
        resolution fail whenever FastAPI was unavailable.
        """
        import ultra_scraper

        monkeypatch.delenv("SCRAPER_DASHBOARD", raising=False)
        for name in ("fastapi", "scrapling_tool.web", "scrapling_tool.web.static"):
            monkeypatch.setitem(sys.modules, name, None)

        assert ultra_scraper._dashboard_path() == ROOT_DASHBOARD

    def test_missing_dashboard_is_reported_not_crashed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import ultra_scraper

        monkeypatch.setenv("SCRAPER_DASHBOARD", os.path.join("no", "such", "dashboard.html"))
        resolved = ultra_scraper._dashboard_path()
        assert not resolved.exists()
