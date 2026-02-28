import os
import re
import httpx
import anthropic


def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def inject_uploaded_images(html: str, uploaded_images: list) -> str:
    if not uploaded_images:
        return html
    count = 0
    max_replacements = min(len(uploaded_images), 4)

    def replace_src(match):
        nonlocal count
        if count >= max_replacements:
            return match.group(0)
        full_tag = match.group(0)
        if any(kw in full_tag.lower() for kw in ['logo', 'icon', 'favicon']):
            return full_tag
        new_src = uploaded_images[count]
        count += 1
        return re.sub(r'src=["\'][^"\']*["\']', f'src="{new_src}"', full_tag)

    return re.sub(r'<img[^>]+src=["\'][^"\']*["\'][^>]*>', replace_src, html)


async def get_unsplash_images(query: str, count: int = 8) -> list:
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not access_key:
        return []
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                "https://api.unsplash.com/search/photos",
                params={"query": query, "per_page": count, "orientation": "landscape", "content_filter": "high"},
                headers={"Authorization": f"Client-ID {access_key}"},
                timeout=10
            )
            data = res.json()
            return [photo["urls"]["regular"] for photo in data.get("results", [])]
    except Exception:
        return []


async def detect_branch(client, title: str, meta_description: str) -> str:
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=15,
        messages=[{
            "role": "user",
            "content": f"""Welche Kategorie passt am besten? Antworte NUR mit einem Wort.
Kategorien: restaurant, luxury, tech, handwerk
Firma: {title}
Beschreibung: {meta_description}
Beispiele: Restaurant/Café/Bar → restaurant | Anwalt/Immobilien/Beratung → luxury | Software/IT/Digital → tech | Schreiner/Elektriker/Sanitär/Bäcker/Salon → handwerk"""
        }]
    )
    branch = response.content[0].text.strip().lower()
    if "restaurant" in branch or "café" in branch or "cafe" in branch:
        return "restaurant"
    elif "tech" in branch or "software" in branch or "it" in branch:
        return "tech"
    elif "handwerk" in branch or "hand" in branch:
        return "handwerk"
    else:
        return "luxury"


def build_unsplash_query(branch: str, title: str, meta_description: str) -> str:
    combined = (title + " " + meta_description).lower()
    if branch == "restaurant":
        if "sushi" in combined or "japan" in combined: return "japanese sushi restaurant food"
        if "pizza" in combined or "itali" in combined: return "italian pizza restaurant pasta"
        if "burger" in combined: return "gourmet burger restaurant"
        if "café" in combined or "cafe" in combined or "kaffee" in combined: return "cafe coffee interior cozy"
        if "bar" in combined or "cocktail" in combined: return "cocktail bar restaurant interior"
        return "restaurant fine dining food photography"
    elif branch == "luxury":
        if "immobilien" in combined or "real estate" in combined: return "luxury real estate interior design"
        if "hotel" in combined: return "luxury hotel interior architecture"
        if "anwalt" in combined or "law" in combined: return "professional law office corporate"
        if "beauty" in combined or "wellness" in combined: return "luxury wellness spa beauty"
        return "luxury professional services elegant interior"
    elif branch == "tech":
        return "technology startup modern office workspace"
    elif branch == "handwerk":
        if "schreiner" in combined or "holz" in combined: return "woodworking craftsman workshop furniture"
        if "elektr" in combined: return "electrician professional work tools"
        if "sanitär" in combined or "heizung" in combined: return "plumbing professional craftsman work"
        if "bäcker" in combined or "bakery" in combined: return "artisan bakery bread pastry"
        if "salon" in combined or "friseur" in combined or "coiffeur" in combined: return "hair salon beauty professional"
        if "bau" in combined or "construction" in combined: return "construction professional building craftsman"
        return "craftsman workshop professional trade"
    return f"{title} professional service quality"


async def generate_website(
    title: str,
    texts: list,
    colors: list,
    images: list = [],
    meta_description: str = "",
    uploaded_images: list = []
) -> str:

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    # 1. Branche erkennen
    branch = await detect_branch(client, title, meta_description)

    # 2. Template laden und Farben direkt ersetzen
    template_map = {
        "restaurant": "template_restaurant.html",
        "luxury": "template_luxury.html",
        "tech": "template_tech.html",
        "handwerk": "template_handwerk.html"
    }
    template = load_template(template_map[branch])

    # Farben und Jahr direkt ersetzen — diese sind im CSS und müssen NICHT von Claude gemacht werden
    template = template.replace('{{PRIMARY_COLOR}}', primary_color)
    template = template.replace('{{SECONDARY_COLOR}}', secondary_color)
    template = template.replace('{{YEAR}}', '2025')

    # 3. Bilder zusammenstellen
    unsplash_query = build_unsplash_query(branch, title, meta_description)
    unsplash_images = await get_unsplash_images(unsplash_query, count=8)

    crawled_urls = [img.get("src", "") for img in images[:8] if img.get("src", "").startswith("http")]
    all_images = crawled_urls[:4]
    all_images.extend(unsplash_images[:max(0, 8 - len(all_images))])

    images_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(all_images)]) if all_images else "Keine Bilder verfügbar"

    # 4. Texte
    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    # 5. Nur die Platzhalter-Liste extrahieren damit Claude weiss was zu tun ist
    placeholders = sorted(set(re.findall(r'\{\{([A-Z_0-9]+)\}\}', template)))
    # PRIMARY_COLOR, SECONDARY_COLOR, YEAR sind bereits ersetzt
    remaining = [p for p in placeholders if p not in ('PRIMARY_COLOR', 'SECONDARY_COLOR', 'YEAR')]

    branch_hints = {
        "restaurant": "Texte: appetitlich und einladend | HERO_IMAGE_1=bestes Food-Foto | TICKER_x=kurze Stichworte wie 'Frische Zutaten' | VALUE_x_NUM=Zahlen wie '150+', '15', '4.8'",
        "luxury": "Texte: exklusiv und professionell | CRAFT_QUOTE=inspirierendes Zitat zur Firmenphilosophie | PORTFOLIO_ITEM_x=Referenzprojekt-Namen",
        "tech": "HERO_FLOAT_TITLE=kurzer Vorteil | HERO_FLOAT_BADGE='Neu' oder '✓ Aktiv' | CLIENT_x=Branchenbezeichnungen | PROCESS_x=Arbeitsschritte",
        "handwerk": "TRUST_x=kurze Vertrauensaussagen (max 4 Wörter) | VALUE_x_NUM=Zahlen wie '20+', '500', '100%' | PROCESS_x=Arbeitsschritte"
    }

    prompt = f"""Du bist ein Webdesigner. Fülle alle verbleibenden {{{{PLATZHALTER}}}} im HTML-Template mit echten, überzeugenden deutschen Inhalten.

FIRMA: {title}
KATEGORIE: {branch}
BESCHREIBUNG: {meta_description}

ORIGINAL-TEXTE DER WEBSITE:
{texts_formatted}

BILDER (diese URLs direkt als src einsetzen, Bild 1 = bestes):
{images_text}

HINWEISE: {branch_hints[branch]}

ZU FÜLLENDE PLATZHALTER:
{', '.join(remaining)}

REGELN:
- Jeden Platzhalter ersetzen — keinen auslassen
- Bilder sinnvoll verteilen, nicht wiederholen
- Texte basieren auf Original-Inhalten
- Für fehlende Kontaktdaten: "+41 XX XXX XX XX" verwenden
- Das gesamte HTML komplett zurückgeben
- Beginnt mit <!DOCTYPE html>, endet mit </html>

TEMPLATE:
{template}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=9000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text.strip()
    result = re.sub(r'^```html\s*', '', result)
    result = re.sub(r'^```\s*', '', result)
    result = re.sub(r'\s*```$', '', result)

    # Hochgeladene Bilder nachträglich einsetzen
    if uploaded_images:
        result = inject_uploaded_images(result, uploaded_images)

    return result
