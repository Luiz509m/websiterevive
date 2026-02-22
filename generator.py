import os
import re
import anthropic

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def split_template(template: str):
    # CSS extrahieren
    css_match = re.search(r'<style>([\s\S]*?)</style>', template)
    css = css_match.group(0) if css_match else ''
    # Template ohne CSS (Claude braucht nur HTML-Struktur)
    html_only = re.sub(r'<style>[\s\S]*?</style>', '<STYLE_PLACEHOLDER>', template)
    # Kommentare und Leerzeilen entfernen
    html_only = re.sub(r'<!--.*?-->', '', html_only, flags=re.DOTALL)
    html_only = re.sub(r'\n\s*\n', '\n', html_only)
    return html_only.strip(), css

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

    branch = await detect_branch(client, title, meta_description)

    if branch == "tech":
        html_only, css = split_template(load_template("template_tech.html"))
    elif branch == "handwerk":
        html_only, css = split_template(load_template("template_handwerk.html"))
    else:
        html_only, css = split_template(load_template("template_rolex.html"))

    crawled_urls = [img["src"] for img in images[:4]] if images else []
    crawled_text = "\n".join(crawled_urls) if crawled_urls else "Keine Bilder"

    uploaded_text = ""
    if uploaded_images:
        uploaded_text = "\nHOCHGELADENE BILDER (Priorität):"
        for i, img_data in enumerate(uploaded_images[:3]):
            uploaded_text += f"\nBild {i+1}: {img_data}"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:12]])

    prompt = f"""Fülle alle {{{{PLATZHALTER}}}} im HTML-Template aus.

FIRMA: {title}
BESCHREIBUNG: {meta_description}
TEXTE: {texts_formatted}
PRIMÄRFARBE: {primary_color}
SEKUNDÄRFARBE: {secondary_color}
BILDER: {crawled_text}{uploaded_text}

REGELN:
- Jeden {{{{PLATZHALTER}}}} ersetzen
- {{{{PRIMARY_COLOR}}}}={primary_color}, {{{{SECONDARY_COLOR}}}}={secondary_color}, {{{{YEAR}}}}=2025
- Keine leeren src="" verwenden
- Nur HTML zurückgeben, keine Erklärungen

TEMPLATE:
{html_only}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text
    # CSS wieder einfügen
    result = result.replace('<STYLE_PLACEHOLDER>', css)
    return result
