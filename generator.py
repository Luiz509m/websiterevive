import os
import re
import random
import anthropic

GITHUB_BASE = "https://raw.githubusercontent.com/Luiz509m/websiterevive/main/images"

HERO_IMAGES = {
    "fine_dining":  [f"{GITHUB_BASE}/fine_dining.jpg"],
    "cafe":         [f"{GITHUB_BASE}/cafe.jpg"],
    "sushi":        [f"{GITHUB_BASE}/sushi.jpg", f"{GITHUB_BASE}/sushi%20(2).jpg"],
    "pizza":        [f"{GITHUB_BASE}/pizza.jpg", f"{GITHUB_BASE}/pizza%20(2).jpg", f"{GITHUB_BASE}/pizza%20(3).jpg"],
    "burger":       [f"{GITHUB_BASE}/burger.jpg"],
    "bbq":          [f"{GITHUB_BASE}/bbq.jpg"],
    "salad":        [f"{GITHUB_BASE}/salad.jpg", f"{GITHUB_BASE}/salad%20(2).jpg", f"{GITHUB_BASE}/salad%20(3).jpg"],
    "bakery":       [f"{GITHUB_BASE}/bakery.jpg"],
    "vegetarian":   [f"{GITHUB_BASE}/salad.jpg"],
    "restaurant":   [f"{GITHUB_BASE}/fine_dining.jpg"],
    "handwerk":     [f"{GITHUB_BASE}/fine_dining.jpg"],
    "luxury":       [f"{GITHUB_BASE}/fine_dining.jpg"],
    "tech":         [f"{GITHUB_BASE}/fine_dining.jpg"],
}

def get_hero_image(food_type: str) -> str:
    options = HERO_IMAGES.get(food_type, HERO_IMAGES["restaurant"])
    return random.choice(options)

def load_template(name: str) -> str:
    path = os.path.join(os.path.dirname(__file__), name)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def inject_uploaded_images(html: str, uploaded_images: list) -> str:
    if not uploaded_images:
        return html
    count = 0
    max_replacements = min(len(uploaded_images), 4)

    def replace_src(match):
        nonlocal count
        if count >= max_replacements:
            return match.group(0)
        full_tag = match.group(0)
        if any(kw in full_tag.lower() for kw in ['logo', 'icon', 'favicon']):
            return full_tag
        new_src = uploaded_images[count]
        count += 1
        return re.sub(r'src=["\'][^"\']*["\']', f'src="{new_src}"', full_tag)

    return re.sub(r'<img[^>]+src=["\'][^"\']*["\'][^>]*>', replace_src, html)

async def generate_website(
    title: str,
    texts: list,
    colors: list,
    images: list = [],
    meta_description: str = "",
    uploaded_images: list = [],
    business_type: str = "restaurant",
    food_type: str = "fine_dining"
) -> str:

    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    primary_color = colors[0] if len(colors) > 0 else "#1a1a1a"
    secondary_color = colors[1] if len(colors) > 1 else "#c9a84c"

    template_map = {
        "restaurant": "template_restaurant.html",
        "cafe":       "template_restaurant.html",
        "bakery":     "template_restaurant.html",
        "luxury":     "template_luxury.html",
        "tech":       "template_tech.html",
        "handwerk":   "template_handwerk.html",
    }
    template_file = template_map.get(business_type, "template_restaurant.html")
    template = load_template(template_file)

    # Hero-Bild fest einsetzen
    hero_image = get_hero_image(food_type)
    template = template.replace('{{PRIMARY_COLOR}}', primary_color)
    template = template.replace('{{SECONDARY_COLOR}}', secondary_color)
    template = template.replace('{{YEAR}}', '2025')
    template = template.replace('{{HERO_IMAGE_1}}', hero_image)

    # Gecrawlte Bilder für Rest
    crawled_urls = [img.get("src", "") for img in images[:6] if img.get("src", "").startswith("http")]
    images_text = "\n".join([f"{i+1}. {url}" for i, url in enumerate(crawled_urls)]) if crawled_urls else "Keine Bilder verfügbar"
    texts_formatted = "\n".join([f"- {t}" for t in texts[:20]])

    placeholders = sorted(set(re.findall(r'\{\{([A-Z_0-9]+)\}\}', template)))
    remaining = [p for p in placeholders if p not in ('PRIMARY_COLOR', 'SECONDARY_COLOR', 'YEAR', 'HERO_IMAGE_1')]

    prompt = f"""Du bist ein Webdesigner. Fülle alle verbleibenden {{{{PLATZHALTER}}}} im HTML-Template mit echten, überzeugenden deutschen Inhalten.

FIRMA: {title}
BESCHREIBUNG: {meta_description}

ORIGINAL-TEXTE DER WEBSITE:
{texts_formatted}

BILDER FÜR MENU/GALERIE (diese URLs als src einsetzen):
{images_text}

ZU FÜLLENDE PLATZHALTER:
{', '.join(remaining)}

REGELN:
- Jeden Platzhalter ersetzen — keinen auslassen
- Texte basieren auf Original-Inhalten der Website
- Für fehlende Kontaktdaten: "+41 XX XXX XX XX" verwenden
- Das gesamte HTML komplett zurückgeben
- Beginnt mit <!DOCTYPE html>, endet mit </html>

TEMPLATE:
{template}"""

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=9000,
        messages=[{"role": "user", "content": prompt}]
    )

    result = message.content[0].text.strip()
    result = re.sub(r'^```html\s*', '', result)
    result = re.sub(r'^```\s*', '', result)
    result = re.sub(r'\s*```$', '', result)

    if uploaded_images:
        result = inject_uploaded_images(result, uploaded_images)

    return result
