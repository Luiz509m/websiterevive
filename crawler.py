import httpx
from bs4 import BeautifulSoup

async def crawl_website(url: str) -> dict:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    async with httpx.AsyncClient(timeout=10, verify=False) as client:
        response = await client.get(url, headers=headers)
        html = response.text
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Titel
    title = soup.title.string if soup.title else ""
    
    # Alle Texte
    texts = []
    for tag in soup.find_all(["h1", "h2", "h3", "p"]):
        text = tag.get_text(strip=True)
        if text:
            texts.append(text)
    
    # Bilder
    images = []
    for img in soup.find_all("img"):
        src = img.get("src", "")
        alt = img.get("alt", "")
        if src:
            images.append({"src": src, "alt": alt})
    
    return {
        "title": title,
        "texts": texts[:20],  # max 20 Textelemente
        "images": images[:10]  # max 10 Bilder
    }