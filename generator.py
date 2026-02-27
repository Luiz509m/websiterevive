import os
import re
import httpx
import anthropic


def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def strip_css(template: str) -> tuple:
    """
    Trennt CSS vom HTML-Template.
    Claude bekommt nur die HTML-Struktur — spart ~70% Tokens.
    CSS wird nach der Generierung wieder eingefügt.
    """
    css_match = re.search(r'(<style>[\s\S]*?</style>)', template)
    css = css_match.group(1) if css_match else ''

    # CSS durch Platzhalter ersetzen
    stripped = re.sub(r'<style>[\s\S]*?</style>', '___CSS___', template)

    # HTML-Kommentare entfernen
    stripped = re.sub(r'<!--[\s\S]*?-->', '', stripped)

    # Mehrfache Leerzeilen reduzieren
    stripped = re.sub(r'\n{3,}', '\n\n', stripped)

    return stripped.strip(), css


def inject_uploaded_images(html: str, uploaded_images: list) -> str:
    """
    Ersetzt die ersten N img src-Attribute mit hochgeladenen Bildern.
    So müssen die Base64-Daten nicht durch die API — spart massiv Tokens.
    """
    if not uploaded_images:
        return html

    count = 0
    max_replacements = min(len(uploaded_images), 4)

    def replace_src(match):
        nonlocal count
        if count >= max_replacements:
            return match.group(0)

        full_tag = match.group(0)

        # Logo und kleine Bilder überspringen
        tag_lower = full_tag.lower()
        if any(kw in tag_lower for kw in ['logo', 'icon', 'favicon', 'avatar']):
            return full_tag

        new_src = uploaded_images[count]
        count += 1

        # src Attribut ersetzen
        result = re.sub(r'src=["\'][^"\']*["\']', f'src="{new_src}"', full_tag)
        return result

    # Alle img Tags mit src ersetzen
    html = re.sub(r'<img[^>]+src=["\'][^"\']*["\'][^>]*>', replace_src, html)
    return html


async def get_unsplash_images(query: str, count: int = 8) -> list:
    """Holt hochwertige Bilder von Unsplash passend zur Branche"""
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
    """Erkennt die Branche des Unternehmens"""
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=15,
        messages=[{
            "role": "user",
            "content": f"Welche Branche? Antworte NUR mit einem Wort: luxury, tech, oder handwerk.\nFirma: {title}\nBeschreibung: {meta_description}"
        }]
    )
    branch = response.content[0].text.strip().lower()
    if "tech" in branch:
        return "tech"
    elif "handwerk" in branch:
        return "handwerk"
    else:
        return "luxury"


def build_unsplash_query(branch: str, title: str, meta_description: str) -> str:
    """Baut einen spezifischen Unsplash-Suchbegriff"""
    # Wichtige Keywords aus dem Titel extrahieren
    # Z.B. "Restaurant Zum Löwen" -> "restaurant food dining"
    title_lower = title.lower()
    desc_lower = meta_description.lower()
    combined = title_lower + " " + desc_lower

    # Branchenspezifische Queries
    if "restaurant" in combined or "gastro" in combined or "küche" in combined:
        return "restaurant food gourmet dining"
    elif "hotel" in combined or "lodge" in combined:
        return "hotel luxury interior design"
    elif "bäcker" in combined or "konditor" in combined or "bakery" in combined:
        return "bakery bread pastry artisan"
    elif "salon" in combined or "friseur" in combined or "beauty" in combined:
        return "beauty salon hair styling"
    elif "fitnes" in combined or "sport" in combined or "gym" in combined:
        return "fitness gym workout modern"
    elif "immobilien" in combined or "real estate" in combined:
        return "real estate luxury property interior"
    elif "zahnarzt" in combined or "arzt" in combined or "klinik" in combined:
        return "medical clinic professional healthcare"
    elif "software" in combined or "app" in combined or "digital" in combined:
        return "technology software modern office"
    elif "handwerk" in combined or "bau" in combined or "sanitär" in combined:
        return "craftsman workshop professional tools"
    elif branch == "luxury":
        return f"{title} luxury premium lifestyle elegant"
    elif branch == "tech":
        return f"{title} technology innovation digital modern"
    else:
        return f"{title} professional business quality"


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

    # Schritt 1: Branche erkennen
    branch = await detect_branch(client, title, meta_description)

    # Schritt 2: Template laden und CSS trennen
    template_map = {
        "tech": "template_tech.html",
        "handwerk": "template_handwerk.html",
        "luxury": "template_rolex.html"
    }
    full_template = load_template(template_map[branch])
    template_stripped, css = strip_css(full_template)

    # Schritt 3: Bilder zusammenstellen
    # Priorität: gecrawlte Bilder > Unsplash
    # Hochgeladene Bilder werden NACH der Generierung eingefügt (kein Base64 in API)

    unsplash_query = build_unsplash_query(branch, title, meta_description)
    unsplash_images = await get_unsplash_images(unsplash_query, count=8)

    # Gecrawlte Bilder von der Original-Website
    crawled_urls = []
    for img in images[:8]:
        src = img.get("src", "")
        if src and src.startswith("http"):
            crawled_urls.append(src)

    # Finale Bildliste: gecrawlte zuerst, dann Unsplash als Ergänzung
    all_images = []
    all_images.extend(crawled_urls)
    # Unsplash nur bis max 8 total
    remaining = 8 - len(all_images)
    if remaining > 0:
        all_images.extend(unsplash_images[:remaining])

    images_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(all_images)]) if all_images else "Keine Bilder verfügbar — CSS Gradienten verwenden"

    # Schritt 4: Texte formatieren
    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    # Schritt 5: Prompt aufbauen
    prompt = f"""Du bist ein weltklasse Webdesigner. Fülle alle {{{{PLATZHALTER}}}} im HTML-Template mit professionellen, überzeugenden Inhalten aus.

━━━ UNTERNEHMEN ━━━
Name: {title}
Branche: {branch}
Beschreibung: {meta_description}

━━━ ORIGINAL-INHALTE DER WEBSITE ━━━
{texts_formatted}

━━━ DESIGN ━━━
{{{{PRIMARY_COLOR}}}} → {primary_color}
{{{{SECONDARY_COLOR}}}} → {secondary_color}
{{{{YEAR}}}} → 2025

━━━ VERFÜGBARE BILDER ━━━
Verwende diese URLs direkt als img src — verteile sie sinnvoll:
{images_text}

━━━ REGELN ━━━
1. Jeden einzelnen {{{{PLATZHALTER}}}} ersetzen — keinen auslassen
2. Bilder sinnvoll zuweisen: Hero-Bild = bestes Bild, Galerie = verschiedene Bilder
3. Texte basieren auf den Original-Inhalten — nicht erfinden was nicht da ist
4. Verkaufsstarke, professionelle Formulierungen auf Deutsch
5. ___CSS___ NICHT ersetzen — exakt so lassen
6. Nur HTML zurückgeben — keine Erklärungen, kein Markdown
7. Beginnt mit <!DOCTYPE html> und endet mit </html>

TEMPLATE:
{template_stripped}"""

    # Schritt 6: Website generieren
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=7000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text.strip()

    # Markdown Code-Blöcke entfernen
    result = re.sub(r'^```html\s*', '', result)
    result = re.sub(r'^```\s*', '', result)
    result = re.sub(r'\s*```$', '', result)

    # Schritt 7: CSS wieder einfügen
    result = result.replace('___CSS___', css)

    # Schritt 8: Hochgeladene Bilder nachträglich einfügen
    # (Base64 geht NICHT durch die API — wird direkt im HTML ersetzt)
    if uploaded_images:
        result = inject_uploaded_images(result, uploaded_images)

    return result
