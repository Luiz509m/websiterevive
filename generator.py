import os
import anthropic

async def generate_website(title: str, texts: list, colors: list, images: list = [], meta_description: str = "") -> str:
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    primary_color = colors[0] if len(colors) > 0 else "#4F46E5"
    secondary_color = colors[1] if len(colors) > 1 else "#10B981"
    
    # Bilder für den Prompt aufbereiten
    image_urls = "\n".join([f'<img src="{img["src"]}" alt="{img["alt"]}">' for img in images[:6]])
    image_hint = f"""
Verwende diese echten Bilder von der Original-Website (direkt als src einbinden):
{image_urls}
Platziere die Bilder sinnvoll im Hero, in Produkt-Sektionen oder als Hintergrund.
""" if images else "Verwende CSS-Gradienten und Formen statt Bilder."

    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    prompt = f"""Du bist ein weltklasse Webdesigner mit dem Stil von Apple, Stripe und Linear. 
Erstelle eine atemberaubende, moderne Website als einzelne HTML-Datei.

UNTERNEHMEN: {title}
BESCHREIBUNG: {meta_description}
INHALTE:
{texts_formatted}

FARBEN:
- Primärfarbe: {primary_color}
- Sekundärfarbe: {secondary_color}

BILDER:
{image_hint}

DESIGN-ANFORDERUNGEN (sehr wichtig):
1. Apple/Stripe Style: viel Weissraum, grosse mutige Typografie, cleane Linien
2. Erkenne die Branche automatisch und passe Layout entsprechend an
3. Moderne Scroll-Animationen mit CSS (fade-in, slide-up beim Scrollen via IntersectionObserver)
4. Hover-Effekte auf allen Buttons und Karten
5. Glassmorphism oder Gradient-Elemente für moderne Optik
6. Mobile-first, vollständig responsive
7. Fixierte Navigation mit Blur-Effekt beim Scrollen

SEITENSTRUKTUR (branchenspezifisch anpassen):
- Hero: Grosses, mutiges Statement mit Animation, CTA Button
- Social Proof oder Key Facts (3 Zahlen/Fakten)
- Hauptleistungen oder Produkte (3-6 Karten mit hover)
- Über uns / Story Sektion
- Testimonials oder Vorteile
- Call-to-Action Sektion mit starkem Gradient
- Footer mit Kontakt

TECHNISCH:
- Alles inline in einer HTML-Datei (CSS + JS)
- Keine externen Libraries oder Fonts
- System-Fonts verwenden: -apple-system, BlinkMacSystemFont, "Segoe UI"
- IntersectionObserver für Scroll-Animationen
- Smooth Scroll
- Muss mit <!DOCTYPE html> beginnen und mit </html> enden
- NUR reinen HTML Code zurückgeben, keine Erklärungen"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text
