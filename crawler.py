import httpx
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

async def crawl_website(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    async with httpx.AsyncClient(timeout=15, verify=False, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

    # Titel
    title = soup.title.string.strip() if soup.title else ""

    # Meta Description
    meta_desc = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        meta_desc = meta.get("content", "")

    # Texte
    texts = []
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 20:
            texts.append(text)

    # Bilder — verbesserte Filterung
    skip_keywords = [
        "icon", "favicon", "sprite", "pixel", "tracking",
        "analytics", "1x1", "spacer", "blank", "placeholder",
        "arrow", "bullet", "star", "rating", "social",
        "facebook", "twitter", "instagram", "linkedin",
        "whatsapp", "youtube", "tiktok", "pinterest"
    ]

    priority_keywords = [
        "hero", "banner", "product", "food", "dish", "menu",
        "team", "about", "gallery", "main", "cover", "feature",
        "background", "bg", "photo", "image", "img"
    ]

    skip_extensions = [".svg", ".gif", ".ico", ".webp"]

    images = []
    seen_srcs = set()

    for img in soup.find_all("img"):
        src = img.get("src", "") or img.get("data-src", "") or img.get("data-lazy-src", "")
        alt = img.get("alt", "")

        if not src:
            continue

        # Absolute URL
        src = urljoin(base_url, src)

        # Duplikate überspringen
        if src in seen_srcs:
            continue
        seen_srcs.add(src)

        src_lower = src.lower()

        # Skip kleine/irrelevante Bilder
        if any(kw in src_lower for kw in skip_keywords):
            continue

        # Skip SVG, GIF, ICO (meist Icons)
        if any(src_lower.endswith(ext) for ext in skip_extensions):
            continue

        # Mindestgrösse prüfen via width/height Attribute
        width = img.get("width", "")
        height = img.get("height", "")
        try:
            if width and int(width) < 100:
                continue
            if height and int(height) < 100:
                continue
        except ValueError:
            pass

        # Nur echte Bildformate
        if not any(fmt in src_lower for fmt in [".jpg", ".jpeg", ".png", ".webp", "image", "photo", "media", "upload", "content"]):
            continue

        # Priorität für wichtige Bilder
        is_priority = any(kw in src_lower or kw in alt.lower() for kw in priority_keywords)

        if is_priority:
            images.insert(0, {"src": src, "alt": alt})
        else:
            images.append({"src": src, "alt": alt})

    # Auch background-images aus inline styles holen
    for tag in soup.find_all(style=True):
        style = tag.get("style", "")
        bg_matches = re.findall(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', style)
        for bg_url in bg_matches:
            if bg_url not in seen_srcs:
                seen_srcs.add(bg_url)
                images.append({"src": bg_url, "alt": ""})

    # Farben aus CSS
    css_colors = []
    for style in soup.find_all("style"):
        found = re.findall(r'#[0-9a-fA-F]{6}', style.string or "")
        css_colors.extend(found[:5])

    return {
        "title": title,
        "meta_description": meta_desc,
        "texts": texts[:30],
        "images": images[:12],
        "css_colors": css_colors[:5],
        "base_url": base_url
    }
