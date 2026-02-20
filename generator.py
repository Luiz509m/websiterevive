import os
import anthropic

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

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

async def generate_website(title: str, texts: list, colors: list, images: list = [], meta_description: str = "") -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    # Branche erkennen
    branch = await detect_branch(client, title, meta_description)

    # Nur das passende Template laden
    if branch == "tech":
        template = load_template("template_tech.html")
    elif branch == "handwerk":
        template = load_template("template_handwerk.html")
    else:
        template = load_template("template_rolex.html")

    # Bilder aufbereiten
    image_urls = [img["src"] for img in images[:6]] if images else []
    images_text = "\n".join(image_urls) if image_urls else "Keine Bilder verfügbar"
    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    prompt = f"""Du bist ein weltklasse Webdesigner. Fülle alle {{{{PLATZHALTER}}}} im folgenden HTML-Template mit echten, überzeugenden Inhalten aus.

UNTERNEHMEN: {title}
BESCHREIBUNG: {meta_description}
INHALTE:
{texts_formatted}

PRIMÄRFARBE: {primary_color}
SEKUNDÄRFARBE: {secondary_color}

VERFÜGBARE BILDER (diese URLs direkt einsetzen wo Bilder gefragt sind):
{images_text}

BILDER-REGEL:
Falls keine echten Produktbilder verfügbar sind, erstelle schöne CSS-Platzhalter 
mit Farbverläufen die zur Marke passen. Verwende KEINE kaputten img-Tags.
Nutze div-Elemente mit background-gradient als Ersatz.

REGELN:
- Ersetze JEDEN {{{{PLATZHALTER}}}} mit echtem Inhalt
- {{{{PRIMARY_COLOR}}}} = {primary_color}
- {{{{SECONDARY_COLOR}}}} = {secondary_color}
- {{{{YEAR}}}} = 2025
- Verwende die echten Bilder-URLs
- Schreibe verkaufsstarke, überzeugende Texte
- Gib NUR den fertigen HTML-Code zurück, keine Erklärungen
- Der Code muss mit <!DOCTYPE html> beginnen und mit </html> enden

TEMPLATE:
{template}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text

