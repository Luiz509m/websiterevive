import os
import re
import httpx
import anthropic

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def strip_template(template: str):
    """Entfernt CSS um Tokens zu sparen — Claude braucht nur die HTML Struktur"""
    css_match = re.search(r'(<style>[\s\S]*?</style>)', template)
    css = css_match.group(1) if css_match else ''
    stripped = re.sub(r'<style>[\s\S]*?</style>', '___CSS_PLACEHOLDER___', template)
    stripped = re.sub(r'<!--.*?-->', '', stripped, flags=re.DOTALL)
    stripped = re.sub(r'\n\s*\n\s*\n', '\n\n', stripped)
    return stripped.strip(), css

async def get_unsplash_images(query: str, count: int = 8) -> list:
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

async def generate_website(title: str, texts: list, colors: list, images: list = [], meta_description: str = "", uploaded_images: list = []) -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    # Branche erkennen
    branch = await detect_branch(client, title, meta_description)

    # Template laden und CSS trennen
    if branch == "tech":
        full_template = load_template("template_tech.html")
    elif branch == "handwerk":
        full_template = load_template("template_handwerk.html")
    else:
        full_template = load_template("template_rolex.html")

    template_stripped, css = strip_template(full_template)

    # Unsplash Bilder holen
    unsplash_queries = {
        "luxury": f"{title} luxury lifestyle premium",
        "tech": f"{title} technology modern digital",
        "handwerk": f"{title} handcraft local artisan"
    }
    unsplash_images = await get_unsplash_images(unsplash_queries.get(branch, title), count=8)

    # Bilder zusammenstellen (Priorität: hochgeladen > gecrawlt > unsplash)
    all_images = []
    if uploaded_images:
        all_images.extend(uploaded_images[:3])
    crawled_urls = [img["src"] for img in images[:3]] if images else []
    all_images.extend(crawled_urls)
    all_images.extend(unsplash_images)

    images_list = "\n".join([f"{i+1}. {url}" for i, url in enumerate(all_images[:8])]) if all_images else "Keine Bilder — CSS Gradienten verwenden"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:15]])

    prompt = f"""Du bist ein weltklasse Webdesigner. Fülle alle {{{{PLATZHALTER}}}} im HTML-Template mit echten, überzeugenden Inhalten aus.

UNTERNEHMEN: {title}
BRANCHE: {branch}
BESCHREIBUNG: {meta_description}

ORIGINAL-INHALTE:
{texts_formatted}

DESIGN:
- {{{{PRIMARY_COLOR}}}} = {primary_color}
- {{{{SECONDARY_COLOR}}}} = {secondary_color}
- {{{{YEAR}}}} = 2025

VERFÜGBARE BILDER (direkt als src verwenden):
{images_list}

REGELN:
- Jeden {{{{PLATZHALTER}}}} ersetzen
- Bilder-URLs sinnvoll verteilen
- Verkaufsstarke deutsche Texte
- ___CSS_PLACEHOLDER___ NICHT ersetzen — exakt so lassen
- Nur HTML zurückgeben, keine Erklärungen
- Beginnt mit <!DOCTYPE html>, endet mit </html>

TEMPLATE:
{template_stripped}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=7000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text.strip()
    result = re.sub(r'^```html\s*', '', result)
    result = re.sub(r'^```\s*', '', result)
    result = re.sub(r'\s*```$', '', result)

    # CSS wieder einfügen
    result = result.replace('___CSS_PLACEHOLDER___', css)

    return result

