"""
scrape_site.py
Fetches a website's HTML and takes a screenshot via Playwright.
Outputs to .tmp/
"""

import sys
import os
import re
import requests
from pathlib import Path

TMP = Path(__file__).parent.parent / ".tmp"
TMP.mkdir(exist_ok=True)


def slugify(url: str) -> str:
    url = re.sub(r"https?://", "", url)
    url = re.sub(r"[^\w]", "_", url)
    return url[:60]


def scrape(url: str) -> dict:
    """Fetch HTML from a URL. Returns dict with html, url, slug."""
    print(f"[scrape] Fetching {url}")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        print(f"[scrape] ERROR: {e}")
        sys.exit(1)

    slug = slugify(url)
    html_path = TMP / f"{slug}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"[scrape] Saved HTML → {html_path} ({len(html)} chars)")

    return {"url": url, "slug": slug, "html": html, "html_path": str(html_path)}


def screenshot(url: str, slug: str) -> str | None:
    """Take a full-page screenshot with Playwright. Returns path or None."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[screenshot] Playwright not installed — skipping screenshot.")
        print("  Install: pip install playwright && playwright install chromium")
        return None

    screenshot_path = TMP / f"{slug}.png"
    print(f"[screenshot] Taking screenshot of {url}")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.screenshot(path=str(screenshot_path), full_page=True)
        browser.close()
    print(f"[screenshot] Saved → {screenshot_path}")
    return str(screenshot_path)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scrape_site.py <url>")
        sys.exit(1)
    url = sys.argv[1]
    result = scrape(url)
    screenshot(url, result["slug"])
    print("\nDone.")
