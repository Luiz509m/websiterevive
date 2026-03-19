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
    # Also pick up lazy-loaded images (data-src, data-lazy-src, data-original, etc.)
    raw_urls += re.findall(r'data-(?:src|lazy-src|original|lazy|bg)=["\']([^"\']+)["\']', html, re.IGNORECASE)

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

def analyze_website(url: str, html: str, business_name: str, full_text: str = "", pages: list = None) -> dict:
    """Send HTML + all scraped pages to Claude for deep analysis. Returns structured brand/content data."""
    print("\n[analyze] Sending to Claude for analysis...")

    # Build full pages context — all pages, not just homepage
    pages_context = ""
    if pages:
        for pg in pages:
            pages_context += f"\n\n=== PAGE: {pg['label'].upper()} ===\n{pg['text'][:3000]}"
    elif full_text:
        pages_context = f"\n\nFull site text:\n{full_text[:8000]}"

    prompt = f"""You are a professional website content analyst. Thoroughly read and extract ALL important information from this website.

Website URL: {url}
Business Name: {business_name or "Unknown"}

Homepage HTML:
```html
{truncate_html(html, 25000)}
```

All scraped pages (verbatim text):
{pages_context}

STRICT RULE: Only extract information EXPLICITLY present above. NEVER invent prices, phone numbers, addresses, hours, names, or any factual details.

Extract and return a JSON object with ALL of the following:
{{
  "business_name": "exact business name from the site",
  "industry": "specific industry/niche",
  "tagline": "exact tagline if present, else null",
  "main_services": ["all services/products explicitly mentioned"],
  "target_audience": "who they serve",
  "tone": "brand tone (professional, friendly, luxury, clinical, etc.)",
  "current_colors": ["hex colors from CSS if found"],
  "current_fonts": ["font names from CSS/Google Fonts if found"],
  "key_content": {{
    "hero_headline": "exact main headline from homepage",
    "hero_subtext": "exact subheadline/description",
    "cta_text": "exact CTA button text",
    "about_summary": "key text about the business (2-4 sentences verbatim)",
    "unique_selling_points": ["specific USPs mentioned"],
    "team_members": [{{"name": "...", "role": "..."}}],
    "prices": ["exact prices as shown, e.g. 'Bleaching ab CHF 490'"],
    "opening_hours": ["exact opening hours as listed"],
    "phone": "exact phone number",
    "email": "exact email address",
    "address": "exact physical address"
  }},
  "pages_content": [
    {{
      "label": "page label exactly as given above",
      "id": "url-friendly id (lowercase, hyphens)",
      "key_paragraphs": ["2-4 most important text paragraphs from this page, verbatim"],
      "services_or_items": [
        {{"name": "item/service name", "description": "exact description from page", "price": "price if shown or null"}}
      ],
      "specific_facts": ["specific facts, numbers, statistics, or key claims from this page"]
    }}
  ],
  "weaknesses": ["3-5 design or content weaknesses of the original site"],
  "improvement_focus": "the single most important improvement"
}}

IMPORTANT: pages_content must include an entry for EVERY page listed above. Extract real verbatim text — do not paraphrase or summarize.

Return ONLY valid JSON, no explanation."""

    response = CLIENT.messages.create(
        model=MODEL,
        max_tokens=4000,
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

def generate_website(analysis: dict, reference_images: list[dict], site_image_urls: list[str] = None, full_text: str = None, pages: list[dict] = None) -> str:
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

    # Detect tech/SaaS industries where a designed hero looks better than a real image
    tech_keywords = ["saas", "software", "erp", "crm", "app", "platform", "cloud",
                     "api", "tech", "digital", "it ", "iot", "ai ", "data", "code",
                     "developer", "entwicklung", "informatik"]
    is_tech = any(kw in industry.lower() for kw in tech_keywords)

    # Build images section for prompt
    images_block = ""
    if is_tech:
        images_block = """
HERO BACKGROUND — this is a tech/software company:
- Do NOT use real images as the hero background. Use a modern CSS gradient or dark abstract design.
- Example: background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); or similar dark tech gradient
- Add geometric shapes, grid lines, or subtle dot patterns via CSS (no external assets)
- Hero must be full viewport height (min-height:100vh), dark, immersive, all text white and centered
- You may use real images in other sections (features, about, etc.)"""
        if site_image_urls:
            images_list = "\n".join(f"- {u}" for u in site_image_urls[:8])
            images_block += f"\n\nOTHER SECTION IMAGES (use in features/about/gallery, NOT hero):\n{images_list}"
    elif site_image_urls:
        images_list  = "\n".join(f"- {u}" for u in site_image_urls[:10])
        images_block = f"""
ORIGINAL SITE IMAGES (from the real website):
{images_list}

HERO BACKGROUND — use your judgement:
- Look at the image URLs above. If they appear to be real content images (food, products, people, places, spaces), use the best one as a full-screen hero background.
- CSS: background-image: url('IMAGE_URL'); background-size: cover; background-position: center; min-height: 100vh;
- Add a dark overlay (position:absolute; inset:0; background:rgba(0,0,0,0.45)) for text readability
- If images look like icons/logos/thumbnails, use a CSS gradient with brand colours instead
- Either way: the hero must be full viewport height (min-height:100vh), spacious, immersive, all text white and centered

GALLERY / ABOUT: use the remaining images from the list"""

    # Build explicit per-page section requirements
    # Prefer rich structured content from analysis (pages_content), fall back to raw text
    pages_block = ""
    pages_analyzed = analysis.get("pages_content", [])

    if pages_analyzed and len(pages_analyzed) > 1:
        # Use rich structured content extracted by analysis step
        section_list = []
        for i, pc in enumerate(pages_analyzed[1:], start=1):  # skip homepage
            label   = pc.get("label", f"Page {i}")
            sec_id  = pc.get("id", label.lower().replace(" ", "-"))

            content_parts = []
            key_paragraphs = pc.get("key_paragraphs", [])
            services       = pc.get("services_or_items", [])
            facts          = pc.get("specific_facts", [])

            if key_paragraphs:
                content_parts.append("Key text (use verbatim):\n" + "\n\n".join(key_paragraphs))
            if services:
                svc_lines = "\n".join(
                    f"  • {s.get('name','')}: {s.get('description','')}"
                    + (f" — {s.get('price')}" if s.get('price') else "")
                    for s in services
                )
                content_parts.append(f"Services / items:\n{svc_lines}")
            if facts:
                content_parts.append("Key facts:\n" + "\n".join(f"  • {f}" for f in facts))

            content_text = ("\n\n".join(content_parts) or "(see business data above)")[:2500]

            section_list.append(
                f"SECTION {i}: id=\"{sec_id}\" — heading: \"{label}\"\n"
                f"EXACT CONTENT TO INCLUDE:\n{content_text}"
            )

        pages_block = (
            f"\n\n══ REQUIRED CONTENT SECTIONS ({len(section_list)} pages) ═══════════\n"
            f"You MUST output one <section> for EACH entry below. Do NOT skip. Do NOT merge.\n"
            f"Use the EXACT content provided — do not invent anything. Do not paraphrase.\n\n"
            + "\n\n".join(section_list)
            + "\n══════════════════════════════════════════════════════════════"
        )
        print(f"[generate] Built pages_block from rich analysis ({len(section_list)} sections)")

    elif pages and len(pages) > 1:
        # Fallback: raw scraped text per page
        section_list = []
        for i, pg in enumerate(pages[1:], start=1):
            label  = pg.get("label", f"Page {i}")
            sec_id = pg.get("id", label.lower().replace(" ", "-"))
            text   = pg.get("text", "")[:3000]
            section_list.append(
                f"SECTION {i}: id=\"{sec_id}\" — heading: \"{label}\"\n"
                f"Content to use:\n{text}"
            )
        pages_block = (
            f"\n\n══ REQUIRED CONTENT SECTIONS ({len(section_list)} pages scraped) ═══════════\n"
            f"You MUST output one <section> for each. Do NOT skip any.\n\n"
            + "\n\n".join(section_list)
            + "\n══════════════════════════════════════════════════════════════"
        )
        print(f"[generate] Built pages_block with {len(section_list)} raw-text sections")

    elif full_text:
        pages_block = (
            f"\n\n── FULL SITE TEXT ─────────────────────────────────────────\n"
            f"Each '--- PAGE: NAME ---' label = one required <section>. Do not skip any.\n"
            f"{full_text[:6000]}\n"
            f"──────────────────────────────────────────────────────────"
        )

    # Count sections for the mandatory structure instruction
    n_content_sections = len(pages) - 1 if pages and len(pages) > 1 else (
        full_text.count("--- PAGE:") if full_text else 0
    )
    total_sections_min = 2 + n_content_sections + 2  # nav+hero + content + cta+footer
    section_count_note = (
        f"You scraped {n_content_sections} sub-pages → output MUST have "
        f"at least {total_sections_min} sections (nav + hero + "
        f"{n_content_sections} content + cta + footer)."
        if n_content_sections > 0
        else "Output at minimum: nav, hero, 2–3 content sections, cta, footer."
    )

    content.append({
        "type": "text",
        "text": f"""You are a senior web designer at a top agency. Redesign this business's website so it looks like it was built by a professional studio — NOT by AI.

── BUSINESS DATA (use ONLY this — never invent facts) ──────────────────
Name:           {business_name}
Industry:       {industry}
Tone:           {tone}
Tagline:        {tagline or '—'}
Services:       {', '.join(services) if services else '—'}
Audience:       {audience}
Brand colors:   {', '.join(brand_colors) if brand_colors else 'derive from industry/tone'}
Headline:       {key_content.get('hero_headline') or '—'}
Subtext:        {key_content.get('hero_subtext') or '—'}
CTA text:       {key_content.get('cta_text') or 'Contact'}
About:          {key_content.get('about_summary') or '—'}
Features:       {', '.join(features) if features else '—'}
Prices:         {', '.join(key_content.get('prices', [])) or '—'}
Hours:          {', '.join(key_content.get('opening_hours', [])) or '—'}
Phone:          {key_content.get('phone') or '—'}
Email:          {key_content.get('email') or '—'}
Address:        {key_content.get('address') or '—'}
{images_block}

══ HERO — THIS IS THE MOST IMPORTANT SECTION ══════════════════════════
The hero must be jaw-dropping. Follow these rules exactly:

TYPOGRAPHY:
- Main headline: 5–9rem on desktop, bold or black weight, tight line-height (0.95–1.1)
- Use the actual business headline/tagline from the data above — NOT a generic one
- Max 6 words on the first line. If headline is long, break it with a <br> at a natural point
- Subtext: 1.1–1.3rem, max 2 lines, light/regular weight, 60% opacity

LAYOUT — pick the one that fits best:
A) Full-bleed background (image or gradient) + centered text + single CTA button
B) Split: left half text, right half image — dark left side, image right
C) Large headline top-left, small descriptor bottom-right, diagonal accent

VISUAL:
- If background image: use it at full opacity with a gradient overlay (not just rgba black)
  e.g. linear-gradient(to right, rgba(0,0,0,0.8) 40%, rgba(0,0,0,0.2) 100%)
- If gradient: use 3 colors min, include a subtle CSS mesh or noise texture via SVG filter
- Add one decorative element: a thin horizontal line, a large outlined letter, a geometric shape — in the accent color
- CTA button: pill shape (border-radius:100px), solid accent color, padding 14px 36px, no shadow

NAV:
- Transparent on load, dark/blurred on scroll (use JS scroll listener)
- Logo left, links right — links are the actual section names from the content
- One highlight button (e.g. "Contact") in the accent color

══ REQUIRED SECTIONS — READ THIS BEFORE WRITING ANY HTML ══════════════
{section_count_note}
{pages_block}

You MUST produce EVERY section listed above. This is non-negotiable.
Before you write </body>, count your <section> tags. If any section from the list above is missing, add it immediately.

══ MANDATORY OUTPUT STRUCTURE ════════════════════════════════════════
Build in this exact order:
1. <nav> — transparent on load, links to every section below by id
2. <section id="hero"> — full-viewport hero (HERO MARKER required, see below)
3. One <section> per REQUIRED SECTION listed above
   → id and heading = the section id/label specified above
   → content = the scraped text provided for that section
   → DO NOT skip any, DO NOT merge any two into one
4. <section id="cta"> — dark background, one headline, one CTA button
5. <footer> — contact info, nav links, copyright

══ SECTION LAYOUT — NO AI PATTERNS ════════════════════════════════════
DO NOT use these AI clichés:
✗ Three equal cards in a row with icon + title + description
✗ "Our Services", "About Us", "Why Choose Us" as headings
✗ Alternating light/dark sections all with the same padding
✗ Stock-looking placeholder text

DO use these human patterns:
✓ Use the actual section names from the scraped pages
✓ Vary the layout: full-width text → split image/text → grid → quote → form
✓ Pull quotes, large numbers (e.g. "12+ years"), subtle background textures
✓ One section with a dark/colored background, the rest light — creates rhythm
✓ Let sections breathe differently: some compact, some very spacious

Count your <section> tags before finishing. If you are missing any, add them.

══ COPY RULES ══════════════════════════════════════════════════════════
- Use the EXACT text from the scraped content — do not rewrite or summarise
- Section headings: use the page names listed in REQUIRED CONTENT SECTIONS
- NEVER invent prices, phone numbers, addresses, hours, or service names
- Contact info from the scraped text → show in footer AND contact section

══ TECHNICAL ═══════════════════════════════════════════════════════════
- Single HTML file, all CSS and JS inline
- Google Fonts: pick 2 that match the tone (e.g. a serif + a sans for luxury; two sans for tech)
- Fully responsive — mobile nav hamburger, stacked sections on mobile
- Smooth scroll: <html style="scroll-behavior:smooth">
- Nav links: href="#sectionid" matching actual section IDs
- Brand colors as CSS custom properties on :root

BUTTON LINKS — CRITICAL, follow exactly:
- Every CTA button MUST have a working href. Priority order:
  1. mailto:EMAIL if email found in the data
  2. tel:PHONE if phone found in the data
  3. href="#contact" if a contact section exists on the page
  4. href="#" is FORBIDDEN — never use it
- "Contact" nav button → mailto: or tel: or #contact
- "Book", "Reservieren", "Anfrage" buttons → mailto: or tel:
- Double-check every single <a> and <button> before finishing

SCROLL ANIMATIONS — REQUIRED:
Add this exact JS block before </body>. Do not modify it:
<script>
(function(){{
  const els = document.querySelectorAll('section, .animate');
  const io = new IntersectionObserver((entries) => {{
    entries.forEach(e => {{
      if(e.isIntersecting){{ e.target.classList.add('visible'); io.unobserve(e.target); }}
    }});
  }}, {{threshold: 0.12}});
  els.forEach(el => {{ el.classList.add('fade-up'); io.observe(el); }});
}})();
</script>

Add this CSS in the <style> block:
.fade-up{{opacity:0;transform:translateY(32px);transition:opacity 0.7s ease,transform 0.7s ease;}}
.fade-up.visible{{opacity:1;transform:none;}}
.fade-up:nth-child(2){{transition-delay:0.1s;}}
.fade-up:nth-child(3){{transition-delay:0.2s;}}

IMAGE FALLBACKS — REQUIRED:
Every <img> tag must have: onerror="this.style.display='none'"

HERO MARKER — REQUIRED:
After the closing </section> or </header> of the hero, add on its own line:
<!-- HERO_END -->

OUTPUT RULES:
- Complete HTML from <!DOCTYPE html> to </html> — do not truncate
- No markdown fences, no explanation — just the HTML
- Concise CSS (no comments, no redundant rules)"""
    })

    with CLIENT.messages.stream(
        model=MODEL,
        max_tokens=32000,
        extra_headers={"anthropic-beta": "output-128k-2025-02-19"},
        messages=[{"role": "user", "content": content}]
    ) as stream:
        html = stream.get_final_text().strip()
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
