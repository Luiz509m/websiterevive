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

async def generate_website(title: str, texts: list, colors: list, images: list = [], meta_description: str = "", uploaded_images: list = []) -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    # Branche erkennen
    branch = await detect_branch(client, title, meta_description)

    # Passendes Template laden
    if branch == "tech":
        template = load_template("template_tech.html")
    elif branch == "handwerk":
        template = load_template("template_handwerk.html")
    else:
        template = load_template("template_rolex.html")

    # Gecrawlte Bilder
    crawled_urls = [img["src"] for img in images[:6]] if images else []
    crawled_text = "\n".join(crawled_urls) if crawled_urls else "Keine gecrawlten Bilder"

    # Hochgeladene Bilder als Base64
    uploaded_text = ""
    if uploaded_images:
        uploaded_text = f"\n\nVOM KUNDEN HOCHGELADENE BILDER ({len(uploaded_images)} Stück) — diese haben höchste Priorität und sollen bevorzugt verwendet werden:"
        for i, img_data in enumerate(uploaded_images[:5]):
            uploaded_text += f"\nBild {i+1}: {img_data[:80]}... [base64 data URL — direkt als src verwenden]"
        # Vollständige Base64 URLs für den HTML Code
        uploaded_text += "\n\nVollständige Base64 URLs (direkt als img src einsetzen):"
        for i, img_data in enumerate(uploaded_images[:5]):
            uploaded_text += f"\nBild {i+1} src: {img_data}"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    prompt = f"""Du bist ein weltklasse Webdesigner. Fülle alle {{{{PLATZHALTER}}}} im folgenden HTML-Template mit echten, überzeugenden Inhalten aus.

UNTERNEHMEN: {title}
BESCHREIBUNG: {meta_description}

INHALTE:
{texts_formatted}

FARBEN:
- Primärfarbe: {primary_color}
- Sekundärfarbe: {secondary_color}

BILDER VON DER ORIGINAL-WEBSITE (als Fallback verwenden):
{crawled_text}
{uploaded_text}

BILD-PRIORITÄT:
1. Vom Kunden hochgeladene Bilder (Base64) — immer bevorzugen
2. Gecrawlte Bilder von der Original-Website
3. CSS-Gradienten als letzter Fallback — niemals leere src="" verwenden

REGELN:
- Ersetze JEDEN {{{{PLATZHALTER}}}} mit echtem Inhalt
- {{{{PRIMARY_COLOR}}}} = {primary_color}
- {{{{SECONDARY_COLOR}}}} = {secondary_color}
- {{{{YEAR}}}} = 2025
- Schreibe verkaufsstarke, überzeugende Texte
- Gib NUR den fertigen HTML-Code zurück, keine Erklärungen
- Muss mit <!DOCTYPE html> beginnen und mit </html> enden

BILDER-REGEL:
Falls keine echten Bilder verfügbar sind, erstelle schöne CSS-Gradienten als Platzhalter.
Verwende NIEMALS leere src="" Attribute.

TEMPLATE:
{template}"""

    message = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=8000,
    messages=[{"role": "user", "content": prompt}]
)

    return message.content[0].text

