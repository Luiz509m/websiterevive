import os
import re
import anthropic

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def shorten_template(template: str) -> str:
    template = re.sub(r'/\*[\s\S]*?\*/', '', template)
    template = re.sub(r'<!--.*?-->', '', template, flags=re.DOTALL)
    template = re.sub(r'\n\s*\n', '\n', template)
    return template.strip()

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
        template = shorten_template(load_template("template_tech.html"))
    elif branch == "handwerk":
        template = shorten_template(load_template("template_handwerk.html"))
    else:
        template = shorten_template(load_template("template_rolex.html"))

    crawled_urls = [img["src"] for img in images[:6]] if images else []
    crawled_text = "\n".join(crawled_urls) if crawled_urls else "Keine gecrawlten Bilder"

    uploaded_text = ""
    if uploaded_images:
        uploaded_text = f"\n\nVOM KUNDEN HOCHGELADENE BILDER — höchste Priorität:"
        for i, img_data in enumerate(uploaded_images[:5]):
            uploaded_text += f"\nBild {i+1}: {img_data}"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:15]])

    prompt = f"""Du bist ein weltklasse Webdesigner. Fülle alle {{{{PLATZHALTER}}}} im Template mit echten Inhalten aus.

UNTERNEHMEN: {title}
BESCHREIBUNG: {meta_description}
INHALTE:
{texts_formatted}

FARBEN: Primär={primary_color}, Sekundär={secondary_color}

BILDER (gecrawlt): {crawled_text}
{uploaded_text}

REGELN:
- Ersetze JEDEN {{{{PLATZHALTER}}}} mit echtem Inhalt
- {{{{PRIMARY_COLOR}}}}={primary_color}, {{{{SECONDARY_COLOR}}}}={secondary_color}, {{{{YEAR}}}}=2025
- Bevorzuge hochgeladene Bilder, dann gecrawlte, sonst CSS-Gradient
- Niemals leere src="" verwenden
- Nur HTML zurückgeben, keine Erklärungen
- Beginnt mit <!DOCTYPE html> und endet mit </html>

TEMPLATE:
{template}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text
