import os
import re
import json
import httpx
import anthropic

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def extract_placeholders(template: str) -> list:
    return list(set(re.findall(r'\{\{([^}]+)\}\}', template)))

def fill_template(template: str, values: dict) -> str:
    for key, value in values.items():
        template = template.replace(f'{{{{{key}}}}}', str(value))
    return template

async def get_unsplash_images(query: str, count: int = 6) -> list:
    """Holt hochwertige Bilder von Unsplash basierend auf dem Suchbegriff"""
    access_key = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not access_key:
        return []
    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                "https://api.unsplash.com/search/photos",
                params={
                    "query": query,
                    "per_page": count,
                    "orientation": "landscape",
                    "content_filter": "high"
                },
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
        max_tokens=10,
        messages=[{"role": "user", "content": f"Welche Branche? Antworte NUR mit einem dieser Wörter: luxury, tech, handwerk. Unternehmen: {title}. Beschreibung: {meta_description}"}]
    )
    branch = response.content[0].text.strip().lower()
    if "tech" in branch:
        return "tech"
    elif "handwerk" in branch:
        return "handwerk"
    else:
        return "luxury"

async def get_unsplash_query(branch: str, title: str, meta_description: str) -> str:
    """Erstellt einen passenden Unsplash-Suchbegriff basierend auf der Branche"""
    queries = {
        "luxury": f"{title} luxury premium brand lifestyle",
        "tech": f"{title} technology software modern office",
        "handwerk": f"{title} craft local business artisan"
    }
    return queries.get(branch, title)

async def generate_website(title: str, texts: list, colors: list, images: list = [], meta_description: str = "", uploaded_images: list = []) -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    # Branche erkennen
    branch = await detect_branch(client, title, meta_description)

    # Template laden
    if branch == "tech":
        template = load_template("template_tech.html")
    elif branch == "handwerk":
        template = load_template("template_handwerk.html")
    else:
        template = load_template("template_rolex.html")

    # Platzhalter extrahieren
    placeholders = extract_placeholders(template)

    # Unsplash Bilder holen
    unsplash_query = await get_unsplash_query(branch, title, meta_description)
    unsplash_images = await get_unsplash_images(unsplash_query, count=8)

    # Alle verfügbaren Bilder zusammenstellen (Priorität: hochgeladen > gecrawlt > unsplash)
    all_images = []
    if uploaded_images:
        all_images.extend(uploaded_images[:3])
    crawled_urls = [img["src"] for img in images[:3]] if images else []
    all_images.extend(crawled_urls)
    all_images.extend(unsplash_images)

    images_text = "\n".join([f"- {url}" for url in all_images[:8]]) if all_images else "Keine Bilder verfügbar"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:15]])

    # Platzhalter Kategorien für bessere Anweisungen
    image_placeholders = [p for p in placeholders if "IMAGE" in p.upper() or "IMG" in p.upper()]
    text_placeholders = [p for p in placeholders if "IMAGE" not in p.upper() and "IMG" not in p.upper()]

    prompt = f"""Du bist ein weltklasse Webdesigner und Texter. Erstelle professionelle, verkaufsstarke Inhalte für diese Website.

UNTERNEHMEN: {title}
BRANCHE: {branch}
BESCHREIBUNG: {meta_description}

ORIGINALE WEBSITE-INHALTE:
{texts_formatted}

DESIGN:
- Primärfarbe: {primary_color}
- Sekundärfarbe: {secondary_color}

VERFÜGBARE BILDER (verwende diese URLs direkt):
{images_text}

AUFGABE:
Erstelle ein JSON-Objekt mit Werten für alle Platzhalter.

TEXT-PLATZHALTER (überzeugend, professionell, zur Marke passend):
{json.dumps(text_placeholders, ensure_ascii=False, indent=2)}

BILD-PLATZHALTER (verteile die verfügbaren Bilder-URLs sinnvoll):
{json.dumps(image_placeholders, ensure_ascii=False, indent=2)}

WICHTIGE REGELN:
- PRIMARY_COLOR = "{primary_color}"
- SECONDARY_COLOR = "{secondary_color}"
- YEAR = "2025"
- Texte müssen zur echten Firma passen — verwende die originalen Inhalte als Basis
- Jeden Bild-Platzhalter mit einer echten URL aus der Bilderliste füllen
- Niemals leere Strings für Bilder verwenden
- Verkaufsstarke Headlines, überzeugende Beschreibungen
- Auf Deutsch schreiben ausser die Firma ist klar englischsprachig
- NUR gültiges JSON zurückgeben, absolut kein anderer Text"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    try:
        values = json.loads(raw)
    except json.JSONDecodeError:
        lines = raw.split('\n')
        while lines and not lines[-1].strip().endswith((',', '{')):
            lines.pop()
        raw = '\n'.join(lines)
        if not raw.endswith('}'):
            raw += '\n}'
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            values = {}

    result = fill_template(template, values)
    return result
