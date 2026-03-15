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


def find_subpage_links(html: str, base_url: str, max_links: int = 8) -> list[dict]:
    """Find links to important sub-pages (about, services, contact, menu, etc.)."""
    from urllib.parse import urljoin, urlparse

    base_domain = urlparse(base_url).netloc
    base_path   = urlparse(base_url).path.rstrip("/")

    # Keywords that indicate a page worth scraping
    important = [
        "about", "uber", "über", "equipe", "team", "uns", "wir",
        "contact", "kontakt", "contact", "reach",
        "service", "leistung", "angebot", "offer",
        "menu", "speise", "karte", "food", "drink",
        "gallery", "galerie", "photo", "portfolio", "work",
        "price", "preis", "tarif", "kosten",
        "product", "produkt", "shop",
    ]

    link_re = re.compile(
        r'<a[^>]+href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    found = []
    seen  = set()

    for m in link_re.finditer(html):
        href   = m.group(1).strip()
        label  = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        absolute = urljoin(base_url, href)
        parsed   = urlparse(absolute)

        if parsed.netloc != base_domain:
            continue
        path = parsed.path.rstrip("/")
        if not path or path == base_path or path in seen:
            continue
        # Skip files, fragments, queries that look like posts/tags
        if re.search(r"\.(pdf|jpg|png|zip|xml|css|js)$", path, re.I):
            continue

        combined = (path + " " + label).lower()
        if not any(kw in combined for kw in important):
            continue

        seen.add(path)
        found.append({"url": absolute, "label": label or path.split("/")[-1]})
        if len(found) >= max_links:
            break

    return found


def scrape_subpages(base_url: str, homepage_html: str, max_pages: int = 4) -> list[dict]:
    """Scrape important sub-pages. Returns list of {url, label, html}."""
    links = find_subpage_links(homepage_html, base_url, max_links=max_pages * 2)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }
    result = []
    for link in links[:max_pages]:
        try:
            print(f"[scrape] Sub-page: {link['url']}")
            resp = requests.get(link["url"], headers=headers, timeout=10)
            if resp.ok:
                result.append({"url": link["url"], "label": link["label"], "html": resp.text})
                print(f"[scrape] Got '{link['label']}' ({len(resp.text):,} chars)")
        except Exception as e:
            print(f"[scrape] Failed {link['url']}: {e}")
    return result


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
