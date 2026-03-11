"""Regression test: static assets under /dashboard/assets/ must be served with
correct MIME types, NOT as text/html from the SPA catch-all.

Root cause (2026-03-12): the @app.get("/dashboard/{full_path:path}") catch-all
was registered before app.mount("/dashboard/assets", StaticFiles(...)), so every
CSS/JS request got index.html with text/html — blank white page in production.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient


@pytest.fixture()
def _static_dashboard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a fake dashboard build in a temp dir and point the app at it."""
    assets = tmp_path / "assets"
    assets.mkdir()

    (tmp_path / "index.html").write_text(
        "<html><head></head><body><div id='root'></div></body></html>"
    )
    (assets / "index-abc123.css").write_text("body { color: red; }")
    (assets / "index-abc123.js").write_text("console.log('app');")
    (assets / "vendor-def456.js").write_text("console.log('vendor');")

    # Patch the module-level _STATIC_DIR before the app re-evaluates.
    # We also need to re-mount the assets StaticFiles with the real directory.
    import edictum_server.main as main_mod

    monkeypatch.setattr(main_mod, "_STATIC_DIR", tmp_path)
    monkeypatch.setattr(main_mod, "_ASSETS_DIR", assets)

    # Re-mount the static files with the temp directory.
    # Remove existing mount if any, then add ours.
    app = main_mod.app
    app.routes[:] = [r for r in app.routes if getattr(r, "name", None) != "dashboard-assets"]

    from starlette.staticfiles import StaticFiles

    app.mount("/dashboard/assets", StaticFiles(directory=str(assets)), name="dashboard-assets")

    # Move the mount BEFORE the catch-all route (same fix as production code).
    # Find the catch-all and the mount, ensure mount comes first.
    mount_route = None
    catchall_idx = None
    for i, route in enumerate(app.routes):
        if getattr(route, "name", None) == "dashboard-assets":
            mount_route = route
        if getattr(route, "name", None) == "serve_spa":
            catchall_idx = i

    if mount_route and catchall_idx is not None:
        app.routes.remove(mount_route)
        app.routes.insert(catchall_idx, mount_route)

    return tmp_path


@pytest.mark.usefixtures("_static_dashboard")
async def test_css_served_with_correct_mime(no_auth_client: AsyncClient) -> None:
    """CSS files must return text/css, not text/html."""
    resp = await no_auth_client.get("/dashboard/assets/index-abc123.css")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert (
        "text/css" in content_type
    ), f"CSS file served as {content_type!r} — SPA catch-all is intercepting static assets"
    assert "body { color: red; }" in resp.text


@pytest.mark.usefixtures("_static_dashboard")
async def test_js_served_with_correct_mime(no_auth_client: AsyncClient) -> None:
    """JS files must return application/javascript, not text/html."""
    resp = await no_auth_client.get("/dashboard/assets/index-abc123.js")
    assert resp.status_code == 200
    content_type = resp.headers.get("content-type", "")
    assert (
        "javascript" in content_type
    ), f"JS file served as {content_type!r} — SPA catch-all is intercepting static assets"
    assert "console.log('app');" in resp.text


@pytest.mark.usefixtures("_static_dashboard")
async def test_spa_catchall_still_works(no_auth_client: AsyncClient) -> None:
    """Non-asset dashboard routes should still return index.html for client-side routing."""
    resp = await no_auth_client.get("/dashboard/events")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "<div id='root'>" in resp.text


@pytest.mark.usefixtures("_static_dashboard")
async def test_nonexistent_asset_not_served_as_html(no_auth_client: AsyncClient) -> None:
    """Missing assets must NOT silently return index.html (the original bug).

    Starlette's StaticFiles raises 404, which the global 404 handler converts
    to a 302 redirect.  Either way, it must NOT return 200 with text/html — that
    would mean the SPA catch-all swallowed the request.
    """
    resp = await no_auth_client.get(
        "/dashboard/assets/nonexistent.js",
        follow_redirects=False,
    )
    # 404 or 302 redirect — both acceptable. 200 text/html is the bug.
    assert resp.status_code != 200, "Missing asset returned 200 — catch-all is swallowing it"
