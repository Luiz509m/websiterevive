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


def _xml_locs(root, tag_path_ns: str, tag_path_bare: str, ns: dict) -> list:
    """Find XML elements trying namespace first, then no-namespace fallback."""
    result = root.findall(tag_path_ns, ns)
    if not result:
        result = root.findall(tag_path_bare)
    return result


def fetch_sitemap_links(base_url: str, headers: dict) -> list[dict]:
    """Try to fetch sitemap.xml and extract page URLs. Returns list of {url, label}."""
    from urllib.parse import urlparse
    import xml.etree.ElementTree as ET

    base_parsed = urlparse(base_url)
    base        = f"{base_parsed.scheme}://{base_parsed.netloc}"

    # Bug fix: normalize www/non-www for domain comparison
    def norm(netloc: str) -> str:
        return netloc.lower().removeprefix("www.")

    base_domain_norm = norm(base_parsed.netloc)
    base_path        = base_parsed.path.rstrip("/")

    # Start with candidates; prepend any Sitemap: line from robots.txt
    candidates = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml"]
    try:
        rb = requests.get(f"{base}/robots.txt", headers=headers, timeout=5)
        if rb.ok:
            for line in rb.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm_url = line.split(":", 1)[1].strip()
                    if sm_url not in candidates:
                        candidates.insert(0, sm_url)
    except Exception:
        pass

    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    raw_urls = []

    for sitemap_url in candidates:
        try:
            print(f"[scrape] Trying sitemap: {sitemap_url}")
            resp = requests.get(sitemap_url, headers=headers, timeout=8)
            print(f"[scrape] Sitemap response: {resp.status_code} ({len(resp.text)} chars)")
            if not resp.ok or "<" not in resp.text:
                continue
            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError as xml_err:
                print(f"[scrape] Sitemap XML broken ({xml_err}) — trying regex fallback")
                loc_urls = re.findall(r'<loc>\s*(https?://[^\s<]+)\s*</loc>', resp.text)
                print(f"[scrape] Regex fallback found {len(loc_urls)} URLs")
                raw_urls.extend(loc_urls)
                if raw_urls:
                    break
                continue

            # Sitemap index → recurse into sub-sitemaps
            sub_maps = _xml_locs(root, "sm:sitemap/sm:loc", "sitemap/loc", ns)
            if sub_maps:
                print(f"[scrape] Sitemap index with {len(sub_maps)} sub-sitemaps")
                for loc_el in sub_maps[:4]:
                    loc_text = loc_el.text
                    if not loc_text:
                        continue
                    try:
                        sub = requests.get(loc_text.strip(), headers=headers, timeout=8)
                        if sub.ok:
                            sub_root = ET.fromstring(sub.text)
                            for u in _xml_locs(sub_root, "sm:url/sm:loc", "url/loc", ns):
                                if u.text:
                                    raw_urls.append(u.text.strip())
                    except Exception as e:
                        print(f"[scrape] Sub-sitemap error: {e}")
            else:
                for u in _xml_locs(root, "sm:url/sm:loc", "url/loc", ns):
                    if u.text:
                        raw_urls.append(u.text.strip())

            if raw_urls:
                print(f"[scrape] Sitemap OK: {len(raw_urls)} URLs collected")
                break
            else:
                print(f"[scrape] Sitemap parsed but 0 URLs found (check XML structure)")
        except Exception as e:
            print(f"[scrape] Sitemap failed ({sitemap_url}): {e}")
            continue

    if not raw_urls:
        return []

    skip = [
        "impressum", "datenschutz", "privacy", "legal", "agb", "cookie",
        "login", "register", "cart", "warenkorb", "404", "sitemap",
        "rss", "feed", "wp-", "admin", "logout", "tag/", "category/",
        "author/", "/page/", "feed/", "wp-content",
    ]
    important = [
        "about", "uber", "über", "equipe", "team", "uns", "wir",
        "contact", "kontakt", "service", "leistung", "angebot", "offer",
        "dienstleistung", "menu", "speise", "karte", "food", "drink",
        "kueche", "gallery", "galerie", "portfolio", "work", "referenz",
        "price", "preis", "tarif", "kosten", "paket", "product",
        "produkt", "shop", "funktion", "feature", "demo", "reserv", "termin",
    ]

    priority = []
    fallback = []
    seen = set()

    for u in raw_urls:
        parsed = urlparse(u)
        # Bug fix: compare normalized domains (www vs non-www)
        if norm(parsed.netloc) != base_domain_norm:
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

    print(f"[scrape] Sitemap filtered: {len(priority)} priority + {len(fallback)} other pages")
    return priority + fallback


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
        print("[scrape] Sitemap returned 0 links — falling back to HTML link parsing")
        links = find_subpage_links(homepage_html, base_url, max_links=max_pages * 2)

    print(f"[scrape] Total links to scrape: {len(links)} (will use first {max_pages})")

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
