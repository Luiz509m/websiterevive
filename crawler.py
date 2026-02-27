import httpx
import re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin


# Bilder die wir überspringen wollen
SKIP_KEYWORDS = [
    "icon", "favicon", "sprite", "pixel", "tracking", "analytics",
    "1x1", "spacer", "blank", "arrow", "bullet", "star", "rating",
    "facebook", "twitter", "instagram", "linkedin", "whatsapp",
    "youtube", "tiktok", "pinterest", "logo-white", "logo-dark",
    "badge", "seal", "award", "flag", "map-marker"
]

# Bilder die wir priorisieren wollen
PRIORITY_KEYWORDS = [
    "hero", "banner", "cover", "feature", "main", "header",
    "product", "food", "dish", "menu", "meal", "drink",
    "team", "staff", "chef", "about", "gallery", "portfolio",
    "photo", "image", "bg", "background"
]

# Dateiformate die wir akzeptieren
VALID_EXTENSIONS = [".jpg", ".jpeg", ".png", ".webp"]

# Pfad-Keywords die auf echte Content-Bilder hinweisen
CONTENT_PATH_KEYWORDS = [
    "/uploads/", "/images/", "/img/", "/photos/", "/media/",
    "/content/", "/assets/", "/files/", "/wp-content/",
    "/static/", "/public/"
]


def is_valid_image_url(src: str, width: str = "", height: str = "") -> bool:
    """Prüft ob eine Bild-URL sinnvoll ist"""
    src_lower = src.lower()

    # Skip Keywords
    if any(kw in src_lower for kw in SKIP_KEYWORDS):
        return False

    # Skip SVG und GIF (meist Icons/Animationen)
    if src_lower.endswith(".svg") or src_lower.endswith(".gif") or src_lower.endswith(".ico"):
        return False

    # Skip Data URLs ausser es ist ein echtes Bild
    if src.startswith("data:") and "image" not in src[:20]:
        return False

    # Mindestgrösse prüfen
    try:
        if width and int(width) < 150:
            return False
        if height and int(height) < 150:
            return False
    except (ValueError, TypeError):
        pass

    # Muss entweder eine gültige Extension oder Content-Pfad haben
    has_valid_ext = any(src_lower.endswith(ext) for ext in VALID_EXTENSIONS)
    has_content_path = any(kw in src_lower for kw in CONTENT_PATH_KEYWORDS)
    has_image_keyword = any(kw in src_lower for kw in ["image", "photo", "picture", "foto"])

    return has_valid_ext or has_content_path or has_image_keyword


def get_image_priority(src: str, alt: str) -> int:
    """Gibt eine Prioritätszahl zurück — höher = wichtiger"""
    src_lower = src.lower()
    alt_lower = alt.lower()
    combined = src_lower + " " + alt_lower

    priority = 0

    # Hohe Priorität für Hero/Banner/Product Bilder
    for kw in PRIORITY_KEYWORDS:
        if kw in combined:
            priority += 2

    # Bonus für Bilder mit ALT-Text (zeigt dass sie wichtig sind)
    if alt and len(alt) > 3:
        priority += 1

    # Bonus für Bilder in Content-Pfaden
    if any(kw in src_lower for kw in CONTENT_PATH_KEYWORDS):
        priority += 1

    return priority


async def crawl_website(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "de-CH,de;q=0.9,en;q=0.8",
    }

    async with httpx.AsyncClient(timeout=20, verify=False, follow_redirects=True) as client:
        response = await client.get(url, headers=headers)
        html = response.text

    soup = BeautifulSoup(html, "html.parser")
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # ── TITEL ──
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    # Fallback: erste H1
    if not title:
        h1 = soup.find("h1")
        if h1:
            title = h1.get_text(strip=True)

    # ── META DESCRIPTION ──
    meta_desc = ""
    for selector in [
        {"name": "description"},
        {"property": "og:description"},
        {"name": "twitter:description"}
    ]:
        meta = soup.find("meta", attrs=selector)
        if meta:
            meta_desc = meta.get("content", "").strip()
            if meta_desc:
                break

    # ── TEXTE ──
    texts = []
    seen_texts = set()

    # Navigations-Text überspringen
    nav_elements = soup.find_all(["nav", "header", "footer"])
    nav_texts = set()
    for nav in nav_elements:
        for tag in nav.find_all(["a", "li"]):
            nav_texts.add(tag.get_text(strip=True))

    for tag in soup.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        text = tag.get_text(strip=True)

        # Zu kurz oder leer
        if not text or len(text) < 25:
            continue

        # Navigation überspringen
        if text in nav_texts:
            continue

        # Duplikate
        if text in seen_texts:
            continue

        # Cookie/DSGVO Texte überspringen
        if any(kw in text.lower() for kw in ["cookie", "datenschutz", "privacy", "gdpr", "newsletter abonnieren"]):
            continue

        seen_texts.add(text)
        texts.append(text)

    # ── BILDER ──
    images = []
    seen_srcs = set()
    image_candidates = []

    # 1. Normale img Tags
    for img in soup.find_all("img"):
        # Verschiedene src Attribute prüfen (lazy loading)
        src = (
            img.get("src") or
            img.get("data-src") or
            img.get("data-lazy-src") or
            img.get("data-original") or
            img.get("data-lazy") or
            ""
        )
        alt = img.get("alt", "")
        width = img.get("width", "")
        height = img.get("height", "")

        if not src:
            continue

        # Relative zu absoluter URL
        if not src.startswith("http"):
            src = urljoin(base_url, src)

        if src in seen_srcs:
            continue
        seen_srcs.add(src)

        if is_valid_image_url(src, width, height):
            priority = get_image_priority(src, alt)
            image_candidates.append({"src": src, "alt": alt, "priority": priority})

    # 2. Background-Images aus inline styles
    for tag in soup.find_all(style=True):
        style = tag.get("style", "")
        bg_matches = re.findall(r'url\(["\']?(https?://[^"\')\s]+)["\']?\)', style)
        for bg_url in bg_matches:
            if bg_url in seen_srcs:
                continue
            seen_srcs.add(bg_url)
            if is_valid_image_url(bg_url):
                image_candidates.append({"src": bg_url, "alt": "", "priority": 1})

    # 3. OG Image (oft das beste Bild der Seite)
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image:
        og_src = og_image.get("content", "")
        if og_src and og_src not in seen_srcs:
            if not og_src.startswith("http"):
                og_src = urljoin(base_url, og_src)
            seen_srcs.add(og_src)
            image_candidates.insert(0, {"src": og_src, "alt": "main", "priority": 10})

    # Nach Priorität sortieren
    image_candidates.sort(key=lambda x: x["priority"], reverse=True)

    # Priority und interne Felder entfernen
    images = [{"src": img["src"], "alt": img["alt"]} for img in image_candidates]

    # ── FARBEN AUS CSS ──
    css_colors = []
    for style_tag in soup.find_all("style"):
        found = re.findall(r'#[0-9a-fA-F]{6}', style_tag.string or "")
        css_colors.extend(found)

    # Häufigste Farben bevorzugen
    from collections import Counter
    color_counts = Counter(css_colors)
    top_colors = [color for color, _ in color_counts.most_common(10)]

    return {
        "title": title,
        "meta_description": meta_desc,
        "texts": texts[:30],
        "images": images[:12],
        "css_colors": top_colors[:5],
        "base_url": base_url
    }
