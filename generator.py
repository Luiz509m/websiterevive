import os
import re
import httpx
import anthropic


def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_css(template: str) -> tuple:
    """Trennt CSS vom HTML. Claude bekommt nur HTML-Struktur — spart ~70% Tokens."""
    css_match = re.search(r'(<style>[\s\S]*?</style>)', template)
    css = css_match.group(1) if css_match else ''
    stripped = re.sub(r'<style>[\s\S]*?</style>', '___CSS___', template)
    stripped = re.sub(r'<!--[\s\S]*?-->', '', stripped)
    stripped = re.sub(r'\n{3,}', '\n\n', stripped)
    return stripped.strip(), css


def inject_uploaded_images(html: str, uploaded_images: list) -> str:
    """Ersetzt img src mit hochgeladenen Bildern — NACH der API, kein Base64 nötig."""
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
    """Erkennt eine von 4 Branchen."""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=15,
        messages=[{
            "role": "user",
            "content": f"""Welche Kategorie passt am besten? Antworte NUR mit einem Wort.
Kategorien: restaurant, luxury, tech, handwerk
Firma: {title}
Beschreibung: {meta_description}
Beispiele: Restaurant/Café/Bar → restaurant | Anwalt/Immobilien/Beratung/Luxus → luxury | Software/IT/Digital/App → tech | Schreiner/Elektriker/Sanitär/Bäcker/Salon → handwerk"""
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
    """Spezifischer Unsplash-Query je nach Branche und Firmeninhalt."""
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
        if "finance" in combined or "finanz" in combined: return "corporate finance professional office"
        if "beauty" in combined or "wellness" in combined: return "luxury wellness spa beauty"
        return "luxury professional services elegant interior"

    elif branch == "tech":
        if "web" in combined or "app" in combined: return "modern tech office software development"
        if "ai" in combined or "künstlich" in combined: return "artificial intelligence technology modern"
        if "cloud" in combined: return "cloud computing server technology"
        return "technology startup modern office workspace"

    elif branch == "handwerk":
        if "schreiner" in combined or "holz" in combined or "möbel" in combined: return "woodworking craftsman workshop furniture"
        if "elektr" in combined: return "electrician professional work tools"
        if "sanitär" in combined or "heizung" in combined or "installation" in combined: return "plumbing professional craftsman work"
        if "bäcker" in combined or "bakery" in combined or "konditor" in combined: return "artisan bakery bread pastry"
        if "salon" in combined or "friseur" in combined or "coiffeur" in combined: return "hair salon beauty professional"
        if "maler" in combined or "paint" in combined: return "professional painter craftsman work"
        if "garten" in combined or "landschaft" in combined: return "landscaping garden professional"
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

    # 2. Template laden, Farben direkt ersetzen, dann CSS trennen
    template_map = {
        "restaurant": "template_restaurant.html",
        "luxury": "template_luxury.html",
        "tech": "template_tech.html",
        "handwerk": "template_handwerk.html"
    }
    full_template = load_template(template_map[branch])

    # Farben VOR CSS-Strip ersetzen (sie sind im CSS Block)
    full_template = full_template.replace('{{PRIMARY_COLOR}}', primary_color)
    full_template = full_template.replace('{{SECONDARY_COLOR}}', secondary_color)
    full_template = full_template.replace('{{YEAR}}', '2025')

    template_stripped, css = strip_css(full_template)

    # 3. Bilder zusammenstellen
    unsplash_query = build_unsplash_query(branch, title, meta_description)
    unsplash_images = await get_unsplash_images(unsplash_query, count=8)

    crawled_urls = [img.get("src", "") for img in images[:8] if img.get("src", "").startswith("http")]

    all_images = crawled_urls[:4]
    remaining = 8 - len(all_images)
    all_images.extend(unsplash_images[:remaining])

    images_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(all_images)]) if all_images else "Keine Bilder — Gradient-Hintergründe verwenden"

    # 4. Texte
    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    # 5. Template-spezifische Hinweise
    branch_hints = {
        "restaurant": "HERO_IMAGE_1=bestes Food-Foto | PRODUCT_x_IMAGE=verschiedene Gerichte | Texte: appetitlich und einladend",
        "luxury": "HERO_IMAGE_1=bestes hochwertiges Foto | Texte: exklusiv, vertrauenswürdig, professionell | CRAFT_QUOTE=inspirierendes Zitat",
        "tech": "HERO_FLOAT_TITLE=ein kurzer Vorteil (z.B. '98% Kundenzufriedenheit') | CLIENT_x=Kundennahmen oder Branchen | Texte: klar, kompetent, modern",
        "handwerk": "TRUST_x=kurze Vertrauensaussagen (z.B. '20 Jahre Erfahrung', 'Schweizer Qualität') | VALUE_x_NUM=Zahlen (z.B. '500+', '20', '100%') | Texte: verlässlich, lokal, kompetent"
    }

    prompt = f"""Du bist ein professioneller Webdesigner. Fülle ALLE {{{{PLATZHALTER}}}} im Template mit echten, überzeugenden Inhalten aus.

FIRMA: {title}
KATEGORIE: {branch}
BESCHREIBUNG: {meta_description}

ORIGINAL-INHALTE DER WEBSITE:
{texts_formatted}

VERFÜGBARE BILDER (diese URLs direkt als src einsetzen):
{images_text}

BILD-STRATEGIE:
- HERO_IMAGE_1 → Bestes, eindrucksvollstes Bild (Bild 1 oder 2)
- GALLERY/PRODUCT Bilder → verschiedene Bilder, keine Wiederholungen
- INTRO_IMAGE, BANNER_IMAGE → weitere Bilder der Reihe nach

BRANCHEN-HINWEISE: {branch_hints[branch]}

PFLICHT-REGELN:
1. JEDEN {{{{PLATZHALTER}}}} ersetzen — keinen einzigen auslassen
2. PRIMARY_COLOR, SECONDARY_COLOR, YEAR sind bereits ersetzt — nicht nochmals einsetzen
3. ___CSS___ EXAKT so lassen — niemals verändern
4. Nur fertiges HTML zurückgeben — kein Markdown, keine Erklärungen
5. Beginnt mit <!DOCTYPE html>, endet mit </html>
6. Texte auf Deutsch — authentisch und überzeugend, basierend auf Original-Inhalten
7. Für unbekannte Werte (Telefon, Email, Adresse) sinnvolle Platzhalter einsetzen wie "+41 XX XXX XX XX"

TEMPLATE:
{template_stripped}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=7000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text.strip()
    result = re.sub(r'^```html\s*', '', result)
    result = re.sub(r'^```\s*', '', result)
    result = re.sub(r'\s*```$', '', result)

    # CSS wieder einfügen
    result = result.replace('___CSS___', css)

    # Hochgeladene Bilder nachträglich einsetzen
    if uploaded_images:
        result = inject_uploaded_images(result, uploaded_images)

    return result
