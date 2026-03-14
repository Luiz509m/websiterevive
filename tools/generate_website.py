"""
generate_website.py
Full pipeline: URL → scrape → Claude analyzes → Claude generates website HTML.

Usage:
    python tools/generate_website.py <customer_url> [--name "Business Name"]

Output:
    .tmp/<slug>_generated.html
"""

import sys
import os
import json
import random
import base64
import argparse
from pathlib import Path

# Load .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

import anthropic
from scrape_site import scrape, slugify

REFERENCE_DIR = Path(__file__).parent.parent / "reference_designs"
TMP = Path(__file__).parent.parent / ".tmp"
TMP.mkdir(exist_ok=True)

CLIENT = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-opus-4-6"


# ── Helpers ──────────────────────────────────────────────────────────────────

def compress_image(img_path: Path, max_bytes: int = 4_500_000) -> tuple[bytes, str]:
    """Resize and compress image to stay under max_bytes. Returns (bytes, media_type)."""
    try:
        from PIL import Image
        import io
        img = Image.open(img_path).convert("RGB")
        # Cap dimensions at 2000px max
        max_dim = 2000
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.LANCZOS)
        # Compress until under max_bytes
        quality = 85
        while True:
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            if buf.tell() <= max_bytes or quality <= 40:
                break
            quality -= 15
        return buf.getvalue(), "image/jpeg"
    except ImportError:
        # No Pillow — just read raw and skip if too large
        raw = img_path.read_bytes()
        if len(raw) > max_bytes:
            return None, None
        media_type = "image/png" if img_path.suffix == ".png" else "image/jpeg"
        return raw, media_type


def load_reference_images(n: int = 3) -> list[dict]:
    """Pick n random reference design screenshots, compress if needed, encode as base64."""
    images = list(REFERENCE_DIR.glob("*.png")) + list(REFERENCE_DIR.glob("*.jpg"))
    if not images:
        return []
    chosen = random.sample(images, min(n, len(images)))
    result = []
    for img_path in chosen:
        raw, media_type = compress_image(img_path)
        if raw is None:
            print(f"[refs] Skipping {img_path.name} (too large, install Pillow to compress)")
            continue
        data = base64.standard_b64encode(raw).decode("utf-8")
        size_kb = len(raw) // 1024
        result.append({"path": str(img_path), "data": data, "media_type": media_type})
        print(f"[refs] Using reference: {img_path.name} ({size_kb}KB)")
    return result


def truncate_html(html: str, max_chars: int = 40000) -> str:
    """Truncate HTML to stay within token limits."""
    if len(html) <= max_chars:
        return html
    print(f"[truncate] HTML truncated from {len(html)} to {max_chars} chars")
    return html[:max_chars] + "\n<!-- truncated -->"


def extract_text_content(html: str, max_chars: int = 12000) -> str:
    """Strip HTML tags and extract clean readable text from the page."""
    import re
    # Remove script, style, nav, footer blocks
    html = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode common HTML entities
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>') \
               .replace('&nbsp;', ' ').replace('&#39;', "'").replace('&quot;', '"')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > max_chars:
        text = text[:max_chars] + '...'
    return text


def extract_image_urls(html: str, base_url: str, max_images: int = 12) -> list[str]:
    """Extract content image URLs from HTML, resolved to absolute URLs."""
    from urllib.parse import urljoin, urlparse
    import re

    # Find all src attributes in img tags
    raw_urls = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)

    # Also find srcset
    srcsets = re.findall(r'srcset=["\']([^"\']+)["\']', html, re.IGNORECASE)
    for srcset in srcsets:
        for part in srcset.split(","):
            url = part.strip().split(" ")[0]
            if url:
                raw_urls.append(url)

    seen = set()
    result = []
    skip_patterns = [
        "logo", "icon", "favicon", "sprite", "pixel", "tracking",
        "1x1", "blank", "placeholder", "avatar", "badge", "flag",
        ".svg", "data:image", "javascript"
    ]

    for url in raw_urls:
        url = url.strip()
        if not url or url in seen:
            continue
        # Skip obvious non-content images
        if any(p in url.lower() for p in skip_patterns):
            continue
        # Resolve relative URLs
        absolute = urljoin(base_url, url)
        # Only keep http(s) URLs
        if not absolute.startswith("http"):
            continue
        seen.add(absolute)
        result.append(absolute)
        if len(result) >= max_images:
            break

    print(f"[images] Extracted {len(result)} image URLs from original site")
    return result


# ── Step 1: Analyze ───────────────────────────────────────────────────────────

def analyze_website(url: str, html: str, business_name: str) -> dict:
    """Send HTML to Claude for analysis. Returns structured brand/content data."""
    print("\n[analyze] Sending to Claude for analysis...")

    prompt = f"""You are a web design analyst. Analyze this website and extract key information.

Website URL: {url}
Business Name: {business_name or "Unknown"}

HTML Content:
```html
{truncate_html(html)}
```

STRICT RULE: Only extract information that is EXPLICITLY present in the HTML. If something is not found, use null or an empty list. NEVER invent or guess prices, phone numbers, opening hours, addresses, or any factual details.

Extract and return a JSON object with these fields:
{{
  "business_name": "string — the actual business name from the HTML",
  "industry": "string — what industry/niche",
  "tagline": "string — their exact tagline if present, else null",
  "main_services": ["list of services/products explicitly mentioned"],
  "target_audience": "string — who they serve, based on page content",
  "tone": "string — brand tone (e.g. professional, playful, luxury, technical)",
  "current_colors": ["list of hex colors found in CSS/styles, if any"],
  "current_fonts": ["list of fonts found, if any"],
  "key_content": {{
    "hero_headline": "string — exact headline text if found",
    "hero_subtext": "string — exact subheadline/description if found",
    "cta_text": "string — exact CTA button text if found",
    "about_summary": "string — text about the business found on the page",
    "features": ["features/benefits explicitly listed on the page"],
    "prices": ["any prices explicitly shown, e.g. 'Lunch CHF 23.50'"],
    "opening_hours": ["opening hours if mentioned"],
    "phone": "phone number if present",
    "email": "email if present",
    "address": "address if present"
  }},
  "weaknesses": ["list of 3-5 design or content weaknesses"],
  "improvement_focus": "string — the single most important improvement"
}}

Return ONLY the JSON, no explanation."""

    response = CLIENT.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text.strip()
    # Strip markdown code fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]

    try:
        analysis = json.loads(raw)
        print(f"[analyze] ✓ Business: {analysis.get('business_name')} | Industry: {analysis.get('industry')}")
        return analysis
    except json.JSONDecodeError:
        print("[analyze] Warning: Could not parse JSON, using raw text")
        return {"raw": raw, "business_name": business_name or "Business"}


# ── Step 2: Generate ──────────────────────────────────────────────────────────

def generate_website(analysis: dict, reference_images: list[dict], site_image_urls: list[str] = None, full_text: str = None) -> str:
    """Send analysis + reference images to Claude. Returns generated HTML."""
    print("\n[generate] Sending to Claude for website generation...")

    business_name = analysis.get("business_name", "Business")
    industry      = analysis.get("industry", "")
    tone          = analysis.get("tone", "professional")
    tagline       = analysis.get("tagline", "")
    services      = analysis.get("main_services", [])
    audience      = analysis.get("target_audience", "")
    key_content   = analysis.get("key_content", {})
    features      = key_content.get("features", [])
    brand_colors  = analysis.get("current_colors", [])

    # Build message content with reference images
    content = []

    if reference_images:
        content.append({
            "type": "text",
            "text": f"Here are {len(reference_images)} reference websites for design inspiration. Study their layout, typography, spacing, and visual style:"
        })
        for ref in reference_images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": ref["media_type"],
                    "data": ref["data"]
                }
            })

    # Build images section for prompt
    images_block = ""
    if site_image_urls:
        images_list  = "\n".join(f"- {u}" for u in site_image_urls[:10])
        images_block = f"""
ORIGINAL SITE IMAGES (from the real website):
{images_list}

HERO BACKGROUND — use your judgement:
- Look at the image URLs above. If they appear to be real content images (food, products, people, places, spaces — recognisable from the URL or path), use the best one as a full-screen hero background.
- CSS when using a real image: background-image: url('IMAGE_URL'); background-size: cover; background-position: center; min-height: 100vh;
- Add a dark overlay (position:absolute; inset:0; background:rgba(0,0,0,0.45)) for text readability
- If the images look like icons, logos, thumbnails, or low-quality assets (e.g. contain "icon", "logo", "thumb", "sprite", "1x1" in URL), skip them for the hero and use a strong CSS gradient with the brand colours instead
- Either way: the hero must be full viewport height (min-height:100vh), spacious, immersive, all text white and centered

GALLERY / ABOUT: use the remaining images from the list"""

    content.append({
        "type": "text",
        "text": f"""You are an expert web designer. Create a complete, modern, professional website for this business.

BUSINESS ANALYSIS (extracted from the real website — use ONLY this information, do NOT invent anything):
- Name: {business_name}
- Industry: {industry}
- Tagline: {tagline or 'none found'}
- Services: {', '.join(services) if services else 'see features below'}
- Target audience: {audience}
- Tone: {tone}
- Brand colors: {', '.join(brand_colors) if brand_colors else 'none found — infer from tone/industry'}
- Hero headline: {key_content.get('hero_headline') or 'none found'}
- Hero subtext: {key_content.get('hero_subtext') or 'none found'}
- CTA: {key_content.get('cta_text') or 'Contact Us'}
- About: {key_content.get('about_summary') or 'none found'}
- Features: {', '.join(features) if features else 'none found'}
- Prices: {', '.join(key_content.get('prices', [])) or 'none found'}
- Opening hours: {', '.join(key_content.get('opening_hours', [])) or 'none found'}
- Phone: {key_content.get('phone') or 'none found'}
- Email: {key_content.get('email') or 'none found'}
- Address: {key_content.get('address') or 'none found'}
{images_block}

FULL PAGE TEXT CONTENT (use ALL of this — this is the actual text from the website, use it to fill every section):
---
{full_text or 'not available'}
---

CONTENT RULES:
- Use the full page text above to populate ALL sections — services, about, contact, features, etc.
- Extract contact info (phone, email, address, contact form URL) from the text above and use it
- NEVER invent content — only use what appears in the analysis or full page text above
- If contact details appear in the text, show them prominently in footer AND CTA section

BUTTON RULES — every CTA button must have a real working link:
- Contact/inquiry buttons → use email (mailto:) or phone (tel:) found in the page text
- If a contact page URL is found → use it as href
- Never use href="#" for CTA buttons — only for nav smooth-scroll anchors

COLOUR RULES (IMPORTANT):
- If brand colors are provided above, use them as the primary palette — they define this brand's identity
- Integrate them into backgrounds, buttons, accents, and highlights naturally
- If no brand colors found, derive a fitting palette from the industry and tone

DESIGN REQUIREMENTS:
- Draw inspiration from the reference screenshots above (layout, style, spacing)
- Create a UNIQUE design that fits this specific business
- Modern, clean, professional — sections must be SPACIOUS with generous padding (min 80px top/bottom)
- Fully responsive (mobile-first)
- Use Google Fonts (pick 2 that match the tone)
- Include: sticky nav, hero section (full viewport height), services/features section, about section, CTA section, footer
- Inline all CSS and JS in a single HTML file
- NO cramped layouts — every section needs room to breathe

HERO MARKER (CRITICAL):
- After the closing tag of the hero section (</section> or </header>), add this exact comment on its own line:
  <!-- HERO_END -->
- This is required — do not omit it

CRITICAL RULES:
- Write CONCISE CSS — no verbose comments, no redundant rules
- You MUST complete the FULL HTML page including </body> and </html>
- Prioritize completeness over CSS detail
- Keep total output under 15000 characters

OUTPUT: Return ONLY the complete HTML file, starting with <!DOCTYPE html>. No explanation, no markdown fences."""
    })

    response = CLIENT.messages.create(
        model=MODEL,
        max_tokens=16000,
        extra_headers={"anthropic-beta": "output-128k-2025-02-19"},
        messages=[{"role": "user", "content": content}]
    )

    html = response.content[0].text.strip()
    # Strip markdown fences if model wraps output
    if html.startswith("```"):
        html = html.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    print(f"[generate] ✓ Generated {len(html)} chars of HTML")
    return html


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate a website from a URL")
    parser.add_argument("url", help="Customer website URL")
    parser.add_argument("--name", default="", help="Business name (optional override)")
    parser.add_argument("--refs", type=int, default=3, help="Number of reference images to use (default: 3)")
    args = parser.parse_args()

    url = args.url
    if not url.startswith("http"):
        url = "https://" + url

    print(f"\n{'='*60}")
    print(f"WebsiteRevive Pipeline")
    print(f"{'='*60}")
    print(f"Input URL: {url}")

    # Step 1: Scrape
    scraped = scrape(url)

    # Step 2: Load reference images + extract site images + full text
    references = load_reference_images(n=args.refs)
    site_images = extract_image_urls(scraped["html"], url)
    full_text = extract_text_content(scraped["html"])
    print(f"[text] Extracted {len(full_text)} chars of page text")

    # Step 3: Analyze (use cached result if available)
    slug = scraped["slug"]
    analysis_path = TMP / f"{slug}_analysis.json"
    if analysis_path.exists():
        print(f"\n[analyze] Using cached analysis → {analysis_path}")
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        print(f"[analyze] ✓ Business: {analysis.get('business_name')} | Industry: {analysis.get('industry')}")
    else:
        analysis = analyze_website(url, scraped["html"], args.name)

    # Step 4: Generate
    generated_html = generate_website(analysis, references, site_images, full_text)

    # Save output
    output_path = TMP / f"{slug}_generated.html"
    output_path.write_text(generated_html, encoding="utf-8")

    # Save analysis (if not already cached)
    if not analysis_path.exists():
        analysis_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"✓ Done!")
    print(f"  Generated site → {output_path}")
    print(f"  Analysis       → {analysis_path}")
    print(f"{'='*60}\n")
    print(f"Open in browser: file:///{output_path.as_posix()}")


if __name__ == "__main__":
    main()
