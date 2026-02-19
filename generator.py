import os
import anthropic

async def generate_website(title: str, texts: list, colors: list) -> str:
import anthropic

async def generate_website(title: str, texts: list, colors: list) -> str:
  client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    
    primary_color = colors[0] if len(colors) > 0 else "#4F46E5"
    secondary_color = colors[1] if len(colors) > 1 else "#10B981"
    
    prompt = f"""Erstelle eine einfache aber moderne HTML-Website. Wichtig: Der HTML Code muss vollständig sein mit </body> und </html> am Ende.

Firmenname: {title}
Inhalte: {' | '.join(texts[:5])}
Primärfarbe: {primary_color}
Sekundärfarbe: {secondary_color}

Erstelle NUR eine einzelne HTML-Datei mit inline CSS. Halte es einfach:
- Navigation oben
- Hero Section mit Titel und kurzer Beschreibung  
- Eine Sektion mit 3 Karten
- Footer mit Kontakt
- Kein JavaScript
- Keine externen Ressourcen
- Muss mit <!DOCTYPE html> beginnen und mit </html> enden"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}]
    )
    

    return message.content[0].text


