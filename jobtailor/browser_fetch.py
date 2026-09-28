"""Render a public job page normally; never log in or bypass challenges."""

from __future__ import annotations

from urllib.parse import urlsplit

from .job_details import JobDescriptionError


def render_public(url: str) -> tuple[str, str]:
    if urlsplit(url).scheme not in {"http", "https"}:
        raise JobDescriptionError("Use an HTTP(S) application URL.")
    try:
        from playwright.sync_api import TimeoutError as BrowserTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise JobDescriptionError(
            "Browser fallback is not installed. Run update_windows.bat."
        ) from exc
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response and response.status in {401, 403, 429}:
                raise JobDescriptionError(
                    "The employer blocked access or requires login. No bypass attempted."
                )
            try:
                page.wait_for_function(
                    """() => {
                    const nodes = document.querySelectorAll('[data-automation-id="jobPostingDescription"], #job-description, #jobDescriptionText, .job-description, .posting-page, main, article');
                    return [...nodes].some(n => /responsibilit|qualification|required|experience|sponsorship/i.test(n.innerText));
                }""",
                    timeout=12000,
                )
            except BrowserTimeout:
                pass
            text = page.locator("body").inner_text()
            title = page.title().casefold()
            if (
                any(
                    marker in title
                    for marker in ("access denied", "just a moment", "captcha")
                )
                or "verify you are human" in text.casefold()
            ):
                raise JobDescriptionError(
                    "The page presents an access challenge. No bypass attempted."
                )
            html = page.content()
            if len(html.encode()) > 4_000_000:
                raise JobDescriptionError(
                    "Rendered page exceeds the size limit; no truncated page accepted."
                )
            return html, page.url
        finally:
            browser.close()
