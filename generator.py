import os
import anthropic

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

async def generate_website(title: str, texts: list, colors: list, images: list = [], meta_description: str = "") -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    # Templates laden
    template_rolex = load_template("template_rolex.html")
    template_tech = load_template("template_tech.html")
    template_handwerk = load_template("template_handwerk.html")

    # Bilder aufbereiten
    image_urls = [img["src"] for img in images[:6]] if images else []
    images_text = "\n".join(image_urls) if image_urls else "Keine Bilder verfügbar"

    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    prompt = f"""Du bist ein weltklasse Webdesigner. Du bekommst Informationen über ein Unternehmen und drei fertige HTML-Templates. Deine Aufgabe:

1. Erkenne die Branche des Unternehmens
2. Wähle das passende Template:
   - template_rolex: Für Premium/Luxury Marken, Getränke, Lifestyle, Konsumgüter
   - template_tech: Für Technologie, Software, Startups, digitale Dienstleistungen
   - template_handwerk: Für lokale Betriebe, Handwerk, Restaurants, persönliche Dienstleistungen
3. Fülle ALLE {{{{PLATZHALTER}}}} mit passenden, überzeugenden Inhalten aus

UNTERNEHMEN: {title}
BESCHREIBUNG: {meta_description}
INHALTE:
{texts_formatted}

PRIMÄRFARBE: {primary_color}
SEKUNDÄRFARBE: {secondary_color}

VERFÜGBARE BILDER (diese URLs direkt verwenden):
{images_text}

TEMPLATE 1 - template_rolex (Luxury/Premium):
{template_rolex}

TEMPLATE 2 - template_tech (Technologie/Startup):
{template_tech}

TEMPLATE 3 - template_handwerk (Lokal/Handwerk):
{template_handwerk}

WICHTIGE REGELN:
- Wähle NUR EINES der drei Templates
- Ersetze JEDEN {{{{PLATZHALTER}}}} mit echtem, passendem Inhalt
- Verwende die echten Bilder-URLs wo Bilder gefragt sind
- Setze {{{{PRIMARY_COLOR}}}} = {primary_color} und {{{{SECONDARY_COLOR}}}} = {secondary_color}
- Schreibe überzeugende, verkaufsstarke Texte basierend auf den Unternehmensinhalten
- {{{{YEAR}}}} = 2025
- Gib NUR den fertigen HTML-Code zurück, keine Erklärungen"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text
