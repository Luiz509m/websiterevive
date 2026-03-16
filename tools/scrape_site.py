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


def fetch_sitemap_links(base_url: str, headers: dict) -> list[dict]:
    """Try to fetch sitemap.xml and extract page URLs. Returns list of {url, label}."""
    from urllib.parse import urlparse
    import xml.etree.ElementTree as ET

    base = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    candidates = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/sitemap/"]
    urls = []

    for sitemap_url in candidates:
        try:
            resp = requests.get(sitemap_url, headers=headers, timeout=8)
            if not resp.ok or "<" not in resp.text:
                continue
            root = ET.fromstring(resp.text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            # Handle sitemap index (links to other sitemaps)
            sub_maps = root.findall("sm:sitemap/sm:loc", ns)
            if sub_maps:
                for loc in sub_maps[:3]:
                    try:
                        sub = requests.get(loc.text.strip(), headers=headers, timeout=8)
                        if sub.ok:
                            sub_root = ET.fromstring(sub.text)
                            for u in sub_root.findall("sm:url/sm:loc", ns):
                                urls.append(u.text.strip())
                    except Exception:
                        pass
            else:
                for loc in root.findall("sm:url/sm:loc", ns):
                    urls.append(loc.text.strip())
            if urls:
                print(f"[scrape] Sitemap found at {sitemap_url}: {len(urls)} URLs")
                break
        except Exception:
            continue

    if not urls:
        return []

    base_parsed = urlparse(base_url)
    base_domain = base_parsed.netloc
    base_path   = base_parsed.path.rstrip("/")

    skip = [
        "impressum", "datenschutz", "privacy", "legal", "agb", "cookie",
        "login", "register", "cart", "warenkorb", "404", "sitemap",
        "rss", "feed", "wp-", "admin", "logout", "tag/", "category/",
        "author/", "page/", "feed/", "wp-content",
    ]
    important = [
        "about", "uber", "über", "equipe", "team", "uns", "wir",
        "contact", "kontakt", "service", "leistung", "angebot", "offer",
        "dienstleistung", "menu", "speise", "karte", "food", "drink",
        "küche", "gallery", "galerie", "portfolio", "work", "referenz",
        "price", "preis", "tarif", "kosten", "paket", "product",
        "produkt", "shop", "funktion", "feature", "demo", "reserv", "termin",
    ]

    priority = []
    fallback = []
    seen = set()

    for u in urls:
        parsed = urlparse(u)
        if parsed.netloc != base_domain:
            continue
        path = parsed.path.rstrip("/")
        if not path or path == base_path or path in seen:
            continue
        combined = path.lower()
        if any(kw in combined for kw in skip):
            continue
        seen.add(path)
        label = path.split("/")[-1].replace("-", " ").replace("_", " ").title()
        entry = {"url": u, "label": label}
        if any(kw in combined for kw in important):
            priority.append(entry)
        else:
            fallback.append(entry)

    result = priority + fallback
    print(f"[scrape] Sitemap: {len(priority)} priority + {len(fallback)} other pages")
    return result


def find_subpage_links(html: str, base_url: str, max_links: int = 8) -> list[dict]:
    """Find links to important sub-pages. Priority links match known keywords;
    fallback collects any internal page link so we never return empty-handed."""
    from urllib.parse import urljoin, urlparse

    base_domain = urlparse(base_url).netloc
    base_path   = urlparse(base_url).path.rstrip("/")

    # Priority keywords (content-rich pages)
    important = [
        "about", "uber", "über", "equipe", "team", "uns", "wir",
        "contact", "kontakt", "reach",
        "service", "leistung", "angebot", "offer", "dienstleistung",
        "menu", "speise", "karte", "food", "drink", "küche", "kueche",
        "gallery", "galerie", "photo", "portfolio", "work", "referenz",
        "price", "preis", "tarif", "kosten", "paket",
        "product", "produkt", "shop", "funktion", "feature",
        "demo", "reserv", "booking", "termin",
    ]

    # Skip purely legal/technical pages
    skip = [
        "impressum", "datenschutz", "privacy", "legal", "agb", "cookie",
        "login", "register", "cart", "warenkorb", "404", "sitemap",
        "rss", "feed", "wp-", "admin", "logout",
    ]

    link_re = re.compile(
        r'<a[^>]+href=["\']([^"\'#][^"\']*)["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    priority = []
    fallback = []
    seen     = set()

    for m in link_re.finditer(html):
        href  = m.group(1).strip()
        label = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        absolute = urljoin(base_url, href)
        parsed   = urlparse(absolute)

        if parsed.netloc != base_domain:
            continue
        path = parsed.path.rstrip("/")
        if not path or path == base_path or path in seen:
            continue
        if re.search(r"\.(pdf|jpg|png|zip|xml|css|js)$", path, re.I):
            continue

        combined = (path + " " + label).lower()
        if any(kw in combined for kw in skip):
            continue

        seen.add(path)
        entry = {"url": absolute, "label": label or path.split("/")[-1]}
        if any(kw in combined for kw in important):
            priority.append(entry)
        else:
            fallback.append(entry)

        if len(priority) + len(fallback) >= max_links * 3:
            break

    # Priority first, fill remaining slots from fallback
    result = priority[:max_links]
    if len(result) < max_links:
        result += fallback[: max_links - len(result)]

    print(f"[scrape] Links found: {len(priority)} priority + {len(fallback)} fallback → using {len(result)}")
    return result


def scrape_subpages(base_url: str, homepage_html: str, max_pages: int = 4) -> list[dict]:
    """Scrape important sub-pages. Returns list of {url, label, html}."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    # Try sitemap first — works even on JS-rendered sites
    links = fetch_sitemap_links(base_url, headers)

    # Fall back to regex parsing of homepage HTML
    if not links:
        print("[scrape] No sitemap found — falling back to HTML link parsing")
        links = find_subpage_links(homepage_html, base_url, max_links=max_pages * 2)

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
