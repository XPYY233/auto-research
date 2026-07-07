from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from auto_research.models import PaperState


@dataclass
class BrowserResult:
    state: PaperState
    downloaded_path: Path | None = None
    message: str = ""


class BrowserAcquirer:
    """Headful browser acquisition with human handoff.

    This module intentionally does not bypass paywalls/captchas. It opens the real
    page in a persistent browser context so the user can use lawful institutional
    access. If a PDF download starts, it records the file; otherwise it returns a
    handoff state.
    """

    def __init__(self, profile_dir: Path, download_dir: Path):
        self.profile_dir = profile_dir
        self.download_dir = download_dir
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def open_and_wait_for_download(self, url: str, wait_seconds: int = 60) -> BrowserResult:
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        except Exception as e:
            return BrowserResult(PaperState.NEEDS_HUMAN, message=f"Playwright is not installed: {e}")

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(self.profile_dir),
                headless=False,
                accept_downloads=True,
                downloads_path=str(self.download_dir),
            )
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                with page.expect_download(timeout=wait_seconds * 1000) as download_info:
                    # Try obvious PDF/save controls only. If the page needs login/captcha,
                    # the user can interact with the visible browser during this window.
                    for selector in [
                        "a[href$='.pdf']",
                        "a:has-text('PDF')",
                        "button:has-text('PDF')",
                        "a:has-text('Download')",
                        "button:has-text('Download')",
                    ]:
                        try:
                            loc = page.locator(selector).first
                            if loc.count() > 0:
                                loc.click(timeout=3000)
                                break
                        except Exception:
                            continue
                download = download_info.value
                target = self.download_dir / download.suggested_filename
                download.save_as(str(target))
                context.close()
                return BrowserResult(PaperState.DOWNLOADED, downloaded_path=target, message="Downloaded via headful browser")
            except PlaywrightTimeoutError:
                html = page.content().lower()
                context.close()
                if "captcha" in html or "verify you are human" in html:
                    return BrowserResult(PaperState.NEEDS_CAPTCHA, message="Captcha/human verification detected")
                if "login" in html or "sign in" in html or "shibboleth" in html:
                    return BrowserResult(PaperState.NEEDS_LOGIN, message="Login or institutional authentication required")
                if "subscribe" in html or "purchase" in html:
                    return BrowserResult(PaperState.NEEDS_SUBSCRIPTION, message="Subscription/access confirmation required")
                return BrowserResult(PaperState.NEEDS_HUMAN, message="No PDF download detected; manual handoff required")
