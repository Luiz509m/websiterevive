import os
import re
import json
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
        template = load_template("template_tech.html")
    elif branch == "handwerk":
        template = load_template("template_handwerk.html")
    else:
        template = load_template("template_rolex.html")

    placeholders = extract_placeholders(template)

    crawled_urls = [img["src"] for img in images[:4]] if images else []
    images_text = "\n".join(crawled_urls) if crawled_urls else "Keine Bilder"

    uploaded_text = ""
    if uploaded_images:
        uploaded_text = "\nHOCHGELADENE BILDER (als src verwenden):"
        for i, img_data in enumerate(uploaded_images[:3]):
            uploaded_text += f"\nBild {i+1}: {img_data}"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:12]])

    prompt = f"""Du bist ein Webdesigner. Erstelle Inhalte für eine Website.

FIRMA: {title}
BESCHREIBUNG: {meta_description}
TEXTE: {texts_formatted}
PRIMÄRFARBE: {primary_color}
SEKUNDÄRFARBE: {secondary_color}
BILDER: {images_text}{uploaded_text}

Gib ein JSON-Objekt zurück mit Werten für diese Platzhalter:
{json.dumps(placeholders, ensure_ascii=False)}

REGELN:
- PRIMARY_COLOR = {primary_color}
- SECONDARY_COLOR = {secondary_color}  
- YEAR = 2025
- Für Bilder: echte URLs oder Base64 aus hochgeladenen Bildern verwenden
- Verkaufsstarke, überzeugende Texte schreiben
- NUR JSON zurückgeben, kein anderer Text"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)

    values = json.loads(raw)
    return fill_template(template, values)
