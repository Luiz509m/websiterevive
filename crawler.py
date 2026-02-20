import httpx
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
    
    # Alle Texte
    texts = []
    for tag in soup.find_all(["h1", "h2", "h3", "p", "li"]):
        text = tag.get_text(strip=True)
        if text and len(text) > 20:
            texts.append(text)
    
    # Bilder mit absoluten URLs
    images = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if not src:
            continue
        # Relative zu absoluten URLs
        src = urljoin(base_url, src)
        # Nur sinnvolle Bilder (keine Icons, Tracking Pixel)
        if any(x in src.lower() for x in ["logo", "hero", "banner", "product", "bg", "main"]):
            images.insert(0, {"src": src, "alt": alt})
        elif src.endswith((".jpg", ".jpeg", ".png", ".webp")):
            images.append({"src": src, "alt": alt})
    
    # Farben aus CSS extrahieren
    css_colors = []
    for style in soup.find_all("style"):
        import re
        found = re.findall(r'#[0-9a-fA-F]{6}', style.string or "")
        css_colors.extend(found[:5])
    
    return {
        "title": title,
        "meta_description": meta_desc,
        "texts": texts[:30],
        "images": images[:8],
        "css_colors": css_colors[:5],
        "base_url": base_url
    }
