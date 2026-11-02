"""Viewport layout checks for dashboard HTML (plan §12 M4, 1280 px)."""

from __future__ import annotations

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800
STYLESHEET_MARK = '<link rel="stylesheet" href="/static/status.css">'


def scroll_width(
    html: str,
    *,
    css: str,
    width: int = VIEWPORT_WIDTH,
    height: int = VIEWPORT_HEIGHT,
) -> int:
    """Render HTML at ``width`` using the fixture-gen Chromium and return scrollWidth."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError(
            "playwright is not installed. From the repo root: uv sync --group fixturegen"
        ) from exc

    from watson_fixturegen.render import CHROMIUM_ARGS

    injected = html.replace(STYLESHEET_MARK, f"<style>{css}</style>", 1)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
        try:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.set_content(injected, wait_until="domcontentloaded")
            measured = page.evaluate("() => document.documentElement.scrollWidth")
        finally:
            browser.close()
    return int(measured)


def assert_fits_viewport(
    html: str,
    *,
    css: str,
    width: int = VIEWPORT_WIDTH,
    height: int = VIEWPORT_HEIGHT,
) -> None:
    measured = scroll_width(html, css=css, width=width, height=height)
    if measured > width:
        raise AssertionError(f"page scrollWidth {measured} exceeds {width}px")
