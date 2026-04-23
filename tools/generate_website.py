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
MODEL_FAST = "claude-sonnet-4-6"   # analysis + hero (cheap, fast)
MODEL_FULL = "claude-opus-4-6"     # full website generation (best quality)


# ── Helpers ──────────────────────────────────────────────────────────────────

def extract_brand_colors(html: str) -> list[str]:
    """Deterministically extract the dominant brand colors from a page's CSS, skipping neutrals."""
    import re
    from collections import Counter

    css_blocks = re.findall(r'<style[^>]*>(.*?)</style>', html, re.DOTALL | re.IGNORECASE)
    css_text = '\n'.join(css_blocks)
    inline = re.findall(r'style="([^"]*)"', html, re.IGNORECASE)
    css_text += '\n' + '\n'.join(inline)

    def is_neutral(h6: str) -> bool:
        r, g, b = int(h6[0:2],16), int(h6[2:4],16), int(h6[4:6],16)
        if r > 238 and g > 238 and b > 238: return True   # near-white
        if r < 25  and g < 25  and b < 25:  return True   # near-black
        if abs(r-g) < 18 and abs(g-b) < 18: return True   # grey
        return False

    def norm(h: str) -> str:
        h = h.upper()
        if len(h) == 3:
            h = h[0]*2 + h[1]*2 + h[2]*2
        return '#' + h

    # 1. CSS custom properties with color-related names get highest priority
    var_colors = []
    for name, value in re.findall(r'--([\w-]+)\s*:\s*([^;}\n]+)', css_text):
        if not re.search(r'color|primary|accent|brand|main|cta|button|link|highlight', name, re.I):
            continue
        m = re.search(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', value)
        if m and not is_neutral(norm(m.group(1))[1:]):
            var_colors.append(norm(m.group(1)))

    # 2. Most frequent non-neutral hex colors across all CSS
    all_hex = re.findall(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b', css_text)
    counter = Counter(norm(h) for h in all_hex if not is_neutral(norm(h)[1:]))
    freq_colors = [c for c, _ in counter.most_common(10)]

    seen, result = set(), []
    for c in var_colors + freq_colors:
        if c not in seen:
            seen.add(c)
            result.append(c)
        if len(result) >= 5:
            break

    print(f"[colors] Brand colors extracted: {result or '(none — Claude will derive from industry)'}")
    return result


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
    # Filter out large images to avoid API payload limits
    images = [img for img in images if img.stat().st_size < 800_000]
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
    # CSS background-image (inline styles and <style> blocks) — often contains hero images
    raw_urls += re.findall(r'background(?:-image)?\s*:\s*url\(["\']?([^)"\']+)["\']?\)', html, re.IGNORECASE)

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


# ── Step 1b: Hero-only generation (cheap preview) ────────────────────────────

def generate_hero_only(analysis: dict, reference_images: list[dict], site_image_urls: list[str] = None, raw_html: str = None) -> str:
    """Generate ONLY nav + hero. Fast and cheap — used before token unlock."""
    print("\n[hero] Generating hero preview...")

    def _s(lst):
        if not lst: return "—"
        return ", ".join(i if isinstance(i, str) else (i.get("name") or str(i)) for i in lst)

    business_name = analysis.get("business_name", "Business")
    industry      = analysis.get("industry", "")
    tone          = analysis.get("tone", "professional")
    tagline       = analysis.get("tagline", "")
    services      = analysis.get("main_services", [])
    key_content   = analysis.get("key_content", {})
    pages_analyzed = analysis.get("pages_content", [])
    brand_colors  = extract_brand_colors(raw_html) if raw_html else analysis.get("current_colors", [])

    import re as _re, html as _html_mod
    def _slugify(s):
        return _re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')
    def _decode(s):
        return _html_mod.unescape(s).strip()

    # Build nav topics (same logic as full generation)
    nav_topics = []
    seen = set()
    for pc in pages_analyzed[1:]:
        if len(nav_topics) >= 5: break
        label = _decode(pc.get("label", ""))
        if not label or label.lower() in seen: continue
        nav_topics.append({"label": label, "href": f"#{_slugify(label)}", "cta": False})
        seen.add(label.lower())
    for svc in services[:8]:
        if len(nav_topics) >= 5: break
        label = svc if isinstance(svc, str) else (svc.get("name") or str(svc))
        label = label.strip()
        if not label or label.lower() in seen: continue
        nav_topics.append({"label": label, "href": f"#{_slugify(label)}", "cta": False})
        seen.add(label.lower())
    if not any(t["label"].lower() in ["kontakt", "contact"] for t in nav_topics):
        nav_topics.append({"label": "Kontakt", "href": "#kontakt", "cta": False})
    nav_topics = nav_topics[:6]
    cta_kw = ["reservier", "buchen", "book", "termin", "anfrage", "kontakt", "contact"]
    marked = False
    for t in reversed(nav_topics):
        if any(k in t["label"].lower() for k in cta_kw):
            t["cta"] = True; marked = True; break
    if not marked and nav_topics:
        nav_topics[-1]["cta"] = True

    nav_str = "\n".join(
        f"  {'[CTA-BUTTON] ' if t['cta'] else ''}{t['label']} → {t['href']}"
        for t in nav_topics
    )
    colors_note = f"Use these exact brand colors: {', '.join(brand_colors[:4])}" if brand_colors else "Derive colors from industry/tone."

    tech_kw = ["saas","software","erp","crm","app","platform","cloud","api","tech","digital","it ","ai ","data","informatik","entwicklung"]
    food_kw = ["restaurant","pizza","lieferung","delivery","essen","food","café","cafe","bäckerei","bakery","catering","kebab","burger","sushi","bistro","gastro","küche","kitchen","bar ","wirt","gasthaus","speise"]
    is_tech = any(k in industry.lower() for k in tech_kw)
    is_food = any(k in industry.lower() for k in food_kw)

    import random as _rnd
    if is_tech:
        images_note = "Tech/software business — use a dark CSS gradient for hero, no real image."
    elif site_image_urls:
        # Food always gets fullcover — a split layout looks wrong for restaurants/pizza
        _layout = "fullcover" if is_food else _rnd.choice(["fullcover", "split-right", "split-left"])
        _food_hint = (
            "PRIORITY: pick a food/dish image (pizza, pasta, burger, food platter) over interior shots.\n"
            if is_food else ""
        )
        _img_quality_rules = (
            "IMAGE QUALITY CHECK — before using any image, judge it strictly:\n"
            "  ✓ USE if: clearly shows food, product, interior, people, landscape — high resolution, well-lit, professional\n"
            "  ✗ SKIP if: blurry, pixelated, logo, icon, banner text overlay, screenshot, tiny (thumb/small/icon in URL)\n"
            "  ✗ SKIP if URL contains: thumb, small, icon, logo, avatar, banner, 50x, 100x, 150x, sprite, pixel\n"
            "  ✗ SKIP if the image looks generic, low quality, or does not match the business\n"
            "  → If no image passes this check: use a CSS gradient hero instead. Do NOT use a bad image.\n\n"
        )
        if _layout == "fullcover":
            images_note = (
                "HERO IMAGE LAYOUT: FULL-COVER BACKGROUND\n"
                + _food_hint
                + _img_quality_rules
                + "Pick ONE image that passes the quality check above.\n\n"
                "  ✓ CSS on #hero: background-image:url('...'); background-size:cover; background-position:center;\n"
                "  ✓ Dark overlay div inside #hero: position:absolute;inset:0;background:rgba(0,0,0,0.52);z-index:0;\n"
                "  ✓ All content in a child div: position:relative;z-index:1; text-align:center;\n"
                "  ✓ ALL text color:#ffffff\n"
                "  ✗ No <img> tag inside the hero\n"
                "  ✗ NEVER zoom, stretch or crop any image — hero background-size:cover is the only exception\n"
                "If no suitable image: use a dark CSS gradient instead.\n\n"
                "Available images:\n" + "\n".join(f"- {u}" for u in site_image_urls[:8])
            )
        elif _layout == "split-right":
            images_note = (
                "HERO IMAGE LAYOUT: SPLIT — text left 55%, image right 45%\n"
                + _food_hint
                + _img_quality_rules
                + "Pick ONE image that passes the quality check above.\n\n"
                "  ✓ Left side: dark background (dark gradient or solid dark brand color), text + CTA\n"
                "  ✓ Right side: <img src='...' style='width:100%;height:auto;max-width:100%;border-radius:12px;display:block;'>\n"
                "  ✓ Left text: color:#ffffff (dark left side)\n"
                "  ✗ No background-image on the hero container itself\n"
                "  ✗ NEVER zoom or stretch the image — use height:auto, not object-fit:cover\n"
                "If no suitable image: use a dark CSS gradient instead.\n\n"
                "Available images:\n" + "\n".join(f"- {u}" for u in site_image_urls[:6])
            )
        else:  # split-left
            images_note = (
                "HERO IMAGE LAYOUT: SPLIT — image left 45%, text right 55%\n"
                + _food_hint
                + _img_quality_rules
                + "Pick ONE image that passes the quality check above.\n\n"
                "  ✓ Left side: <img src='...' style='width:100%;height:auto;max-width:100%;border-radius:12px;display:block;'>\n"
                "  ✓ Right side: dark background (dark gradient or solid dark brand color), text + CTA\n"
                "  ✓ Right text: color:#ffffff (dark right side)\n"
                "  ✗ No background-image on the hero container itself\n"
                "  ✗ NEVER zoom or stretch the image — use height:auto, not object-fit:cover\n"
                "If no suitable image: use a dark CSS gradient instead.\n\n"
                "Available images:\n" + "\n".join(f"- {u}" for u in site_image_urls[:6])
            )
    else:
        images_note = "No site images — use a dark gradient hero."

    msg_content = []
    if reference_images:
        msg_content.append({"type": "text", "text": (
            f"You have {len(reference_images)} reference hero designs below. "
            "Study them carefully — their layout, typography scale, spacing, depth, and visual polish. "
            "If no real image is available for the hero, build a CSS-only hero that matches this quality level exactly."
        )})
        for ref in reference_images:
            msg_content.append({"type": "image", "source": {"type": "base64", "media_type": ref["media_type"], "data": ref["data"]}})

    msg_content.append({"type": "text", "text": f"""You are an elite web designer. Generate a complete HTML page with ONLY a nav bar and hero section — this must look like it came from a top design studio.

BUSINESS:
Name:     {business_name}
Industry: {industry}
Tone:     {tone}
Tagline:  {tagline or '—'}
Headline: {key_content.get('hero_headline') or '—'}
Subtext:  {key_content.get('hero_subtext') or '—'}
CTA:      {key_content.get('cta_text') or 'Kontakt'}
Phone:    {key_content.get('phone') or '—'}
Services: {_s(services)}
{colors_note}

NAV: logo far left, links right. ALWAYS dark background: background:#111111; Never transparent.
USE EXACTLY THESE LINKS:
  Home → #
{nav_str}
[CTA-BUTTON] = pill button, accent color bg, color:#ffffff, border-radius:100px.
ALL nav links and logo: color:#ffffff !important — nav is always #111111, text always white. Min 48px gap between logo and first link.

── HERO IMAGE DECISION ──────────────────────────────────────────────────
{images_note}

── HERO DESIGN ──────────────────────────────────────────────────────────
✓ min-height:100svh, full viewport, immersive
✓ ALWAYS set an explicit background on #hero — never leave it unset
✓ overflow:hidden on #hero — no content may be cut off on any screen size
✓ All text: max-width:100%; word-break:break-word; no element wider than viewport

CONTRAST LAW — most important rule, no exceptions ever:
  • Dark background (image overlay OR dark gradient) → ALL text color:#ffffff — set on EVERY element individually
  • Light background → ALL text color:#111111 — set on EVERY element individually
  • NEVER rely on color inheritance — set color explicitly on h1,h2,p,span,a,button each

IF using a real image:
  ✓ CSS background-image on #hero with background-size:cover;background-position:center
  ✓ Dark overlay div (position:absolute;inset:0;background:rgba(0,0,0,0.55);z-index:0)
  ✓ Content div: position:relative;z-index:1
  ✓ ALL text color:#ffffff (overlay makes bg dark)
  ✗ No <img> tag in the hero

IF no image (CSS-only):
  ✓ Dark gradient hero — match reference design quality
  ✗ No random symbols, decorative dots, or ornamental characters

✓ Headline: clamp(2.5rem,7vw,6rem), bold, line-height:0.95–1.1, explicit color
✓ Subtext: clamp(1rem,2vw,1.25rem), max-width:600px, explicit color
✓ CTA: pill, accent color bg, color:#fff, padding:14px 40px
✓ Google Fonts: 2 fonts matching the tone

Add <!-- HERO_END --> immediately after the closing </section> of the hero.

Mobile-first. All CSS+JS inline.
OUTPUT: Complete HTML <!DOCTYPE html> to </html>. Nothing below the hero. No markdown."""})

    for attempt in range(3):
        try:
            with CLIENT.messages.stream(
                model=MODEL_FAST, max_tokens=8000,
                messages=[{"role": "user", "content": msg_content}]
            ) as stream:
                html = stream.get_final_text().strip()
            break
        except anthropic.APIStatusError as e:
            if e.status_code in (529, 500) and attempt < 2:
                wait = (attempt + 1) * 15
                print(f"[hero] API {e.status_code} — retrying in {wait}s")
                import time; time.sleep(wait)
            else:
                raise

    if html.startswith("```"):
        html = html.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    print(f"[hero] ✓ Generated {len(html)} chars")
    return html


# ── Step 1: Analyze ───────────────────────────────────────────────────────────

def analyze_website(url: str, html: str, business_name: str, full_text: str = "", pages: list = None) -> dict:
    """Send HTML + all scraped pages to Claude for deep analysis. Returns structured brand/content data."""
    print("\n[analyze] Sending to Claude for analysis...")

    # Build full pages context — all pages, not just homepage
    pages_context = ""
    if pages:
        for pg in pages:
            pages_context += f"\n\n=== PAGE: {pg['label'].upper()} ===\n{pg['text'][:5000]}"
    elif full_text:
        pages_context = f"\n\nFull site text:\n{full_text[:20000]}"

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
      "key_paragraphs": ["5-8 important text paragraphs from this page, copied VERBATIM — do not shorten or paraphrase"],
      "services_or_items": [
        {{"name": "item/service name", "description": "full exact description from page", "price": "price if shown or null"}}
      ],
      "specific_facts": ["every specific fact, number, statistic, or claim found on this page — only what is explicitly written"]
    }}
  ],
  "weaknesses": ["3-5 design or content weaknesses of the original site"],
  "improvement_focus": "the single most important improvement"
}}

CRITICAL RULES:
- pages_content must include an entry for EVERY page listed above
- key_paragraphs must be copied VERBATIM — never shorten, paraphrase, or summarize
- specific_facts: ONLY include numbers/claims that are LITERALLY written on the page — never round up or invent (e.g. if page says "4 languages" write "4 languages", never "7+" or "many")
- If information is not on the page, use null or empty list — never guess

Return ONLY valid JSON, no explanation."""

    for attempt in range(3):
        try:
            response = CLIENT.messages.create(
                model=MODEL_FAST,
                max_tokens=8000,
                messages=[{"role": "user", "content": prompt}]
            )
            break
        except anthropic.APIStatusError as e:
            if e.status_code in (529, 500) and attempt < 2:
                wait = (attempt + 1) * 15
                print(f"[analyze] API {e.status_code} — retrying in {wait}s (attempt {attempt+1}/3)")
                import time; time.sleep(wait)
            else:
                raise

    raw = response.content[0].text.strip()

    # Robustly extract JSON: strip markdown fences, find first { ... }
    import re as _re
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    # Find outermost JSON object in case there's extra text
    match = _re.search(r'\{[\s\S]*\}', raw)
    if match:
        raw = match.group(0)

    try:
        analysis = json.loads(raw)
        pages_content = analysis.get("pages_content", [])
        print(f"[analyze] ✓ Business: {analysis.get('business_name')} | Industry: {analysis.get('industry')} | pages_content: {len(pages_content)} entries")
        return analysis
    except json.JSONDecodeError as e:
        print(f"[analyze] Warning: Could not parse JSON ({e}) — using raw text fallback")
        print(f"[analyze] Raw response start: {raw[:200]}")
        return {"raw": raw, "business_name": business_name or "Business"}


# ── Section design hints ─────────────────────────────────────────────────────

def _section_design_hints(industry: str, nav_topics: list) -> str:
    """
    Returns design instructions specific to recognized section types.
    Injected into the full-site generation prompt so Claude knows exactly
    how to build a menu, team, gallery, services, or contact section.
    """
    ind = industry.lower()

    is_food    = any(k in ind for k in ["restaurant","pizza","café","cafe","bäckerei","bakery",
                                         "catering","kebab","burger","sushi","bistro","gastro",
                                         "speise","pizzeria","trattoria","diner","küche","kitchen"])
    is_medical = any(k in ind for k in ["zahnarzt","arzt","klinik","dental","medizin","praxis",
                                         "therapie","physio","optiker","apotheke","gesundheit",
                                         "orthopäd","chirurg","psych"])
    is_handwerk= any(k in ind for k in ["handwerk","bau","maler","elektriker","sanitär","garten",
                                         "reinigung","schreiner","dachdecker","zimmermann",
                                         "installateur","maurer","schlosser","umzug"])
    is_beauty  = any(k in ind for k in ["beauty","kosmetik","friseur","nail","massage","spa",
                                         "wellness","coiffeur","tatoo","piercing","lash","brow"])
    is_legal   = any(k in ind for k in ["anwalt","rechtsanwalt","kanzlei","notar","steuer",
                                         "buchhalter","treuhand","finanzen","beratung"])

    hints = []

    for topic in nav_topics:
        label = topic["label"].lower()
        slug  = topic["href"].lstrip("#")

        # ── MENU / SPEISEKARTE ────────────────────────────────────────────────
        if any(k in label for k in ["menu","speisekarte","karte","gerichte","speisen"]):
            hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — MENU DESIGN:
  Layout: 2-column grid (desktop), 1 column (mobile). Dark or warm parchment background.
  Group dishes by category (e.g. Vorspeisen / Hauptgerichte / Desserts / Getränke).
  Each dish row:
    <div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:10px 0;border-bottom:1px solid rgba(255,255,255,0.08);">
      <div>
        <span style="font-weight:700;font-size:1.05rem;">Dish Name</span>
        <p style="font-size:0.875rem;color:rgba(255,255,255,0.55);margin:2px 0 0;">Short description</p>
      </div>
      <span style="font-weight:600;color:var(--clr-primary);white-space:nowrap;">CHF XX.–</span>
    </div>
  Category header: uppercase letter-spacing label with a short divider line.
  Only use actual dish names and prices from the scraped content — NEVER invent items.""")

        # ── LEISTUNGEN / BEHANDLUNGEN / SERVICES ─────────────────────────────
        elif any(k in label for k in ["leistungen","behandlungen","services","angebote","therapien","eingriffe"]):
            if is_medical:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — MEDICAL SERVICES DESIGN:
  Layout: 2-column card grid. Each card:
    • Service/treatment name — bold, 1.1rem
    • Short description (1-2 sentences from scraped content)
    • Price if available — accent color
    • Icon: use a simple inline SVG (tooth, heart, eye, etc.) or a number circle
  Card style: white bg, subtle shadow, 1px border, border-radius:12px, padding:24px.
  Section ends with a CTA button: "Termin vereinbaren" → booking URL or tel link.
  NEVER use generic icons — numbers (01, 02…) are better than random icons.""")
            elif is_beauty:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — BEAUTY SERVICES DESIGN:
  Layout: elegant 3-column card grid.
  Each card: service name + duration + price. Soft, warm styling.
  One "HIGHLIGHT" card with slightly larger styling and accent border.
  Bottom CTA: "Jetzt buchen" → booking URL.""")
            else:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — SERVICES DESIGN:
  Layout: 2-3 column card grid ONLY if 3+ services exist, otherwise 2-column split with text.
  Each service: name (bold) + 2-3 sentence description from scraped content.
  One card can have a dark/accent background as visual highlight.
  Use numbers (01 / 02 / 03) instead of generic icons.
  Include actual prices if scraped.""")

        # ── TEAM / ÜBER UNS (people-focused) ─────────────────────────────────
        elif any(k in label for k in ["team","mitarbeiter","ärzte","doktoren","staff","personal","therapeuten"]):
            if is_medical:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — MEDICAL TEAM DESIGN:
  Layout: 3-column grid (desktop), 1 column (mobile).
  Each team member card:
    • Photo: circular or rounded-square (200x200px, object-fit:cover). If no photo: initials avatar with accent bg.
    • Name: bold, 1.15rem
    • Title/Specialty: accent color, smaller
    • 1-2 sentence bio from scraped content — only real people, never invented
    • Credentials or years of experience if mentioned
  Card: white bg, subtle shadow, padding:28px, text-align:center.""")
            else:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — TEAM DESIGN:
  Layout: 3-column grid (desktop), 1 column (mobile).
  Each card: photo or initials avatar + Name (bold) + Role + 1 short sentence.
  Clean, professional. Only real team members from scraped content — never invent people.""")

        # ── GALERIE / PROJEKTE / PORTFOLIO ────────────────────────────────────
        elif any(k in label for k in ["galerie","gallery","projekte","portfolio","arbeiten","referenzen","fotos","bilder"]):
            if is_handwerk:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — PROJECT GALLERY DESIGN:
  Layout: CSS masonry grid, 3 columns desktop, gap:12px.
  Each image: width:100%; border-radius:8px; aspect-ratio auto.
  On hover: subtle dark overlay with project type text (transform + opacity transition).
  If before/after content exists: side-by-side comparison cards with a divider.
  Let the work images dominate — minimal text, no cards around images.""")
            else:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — GALLERY DESIGN:
  Layout: 3-column grid, gap:16px. Images: width:100%; height:260px; object-fit:cover; border-radius:10px.
  Hover: scale(1.03) with transition:transform 0.3s.
  Minimal framing — images are the hero. No captions needed unless content provides them.""")

        # ── PREISE / PRICING ──────────────────────────────────────────────────
        elif any(k in label for k in ["preise","pricing","tarife","pakete","kosten"]):
            hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — PRICING DESIGN:
  Layout: 3-column pricing cards (or 2 if only 2 packages).
  Each card: package name + price (large, bold) + feature list with ✓ checkmarks + CTA button.
  One card is "featured": accent border, slightly larger, "Empfohlen" badge.
  Only use actual prices from scraped content. If no prices: omit this layout, use a simple list.""")

        # ── ÜBER UNS / ABOUT (non-people) ────────────────────────────────────
        elif any(k in label for k in ["über uns","about","geschichte","unternehmen","wir sind","our story"]):
            if is_food:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — ABOUT/STORY DESIGN:
  Layout: 2-column split — large atmospheric image left, story text right. Warm, personal tone.
  Include founding year or story highlight as a large typographic number.
  Text: verbatim from scraped content. Warm background (cream or dark).
  One highlighted quote or key fact in larger font.""")
            elif is_legal:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — ABOUT DESIGN:
  Layout: full-width editorial. Lead with a strong statement sentence in large type.
  Followed by 2-column text split. Dark, authoritative tone.
  Key numbers (years of experience, cases won, clients) as large typographic stats.""")
            else:
                hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — ABOUT DESIGN:
  Layout: 2-column — image or stat block left, story text right.
  Include a key number (founding year, years of experience) as a large typographic element.
  Text verbatim from scraped content.""")

        # ── BEWERTUNGEN / TESTIMONIALS ────────────────────────────────────────
        elif any(k in label for k in ["bewertungen","reviews","kundenstimmen","testimonials","meinungen","feedback"]):
            hints.append(f"""
SECTION "{topic['label']}" (id="{slug}") — TESTIMONIALS DESIGN:
  Layout: 3-column card grid (or 1 large quote if only 1 review).
  Each card: ★★★★★ stars (accent color) + quote text in italics + author name + role/location.
  Large opening quotation mark (") as decorative element behind the text.
  ONLY use actual reviews from scraped content — NEVER invent testimonials.""")

    if not hints:
        return ""

    return (
        "\n── SECTION-SPECIFIC DESIGN RULES ───────────────────────────────────────\n"
        "Apply the following layout rules to matching sections exactly as specified:\n"
        + "\n".join(hints)
        + "\n"
    )


# ── Step 2: Generate ──────────────────────────────────────────────────────────

def generate_website(analysis: dict, reference_images: list[dict], site_image_urls: list[str] = None, full_text: str = None, pages: list[dict] = None, important_links: list[dict] = None, raw_html: str = None) -> str:
    """Send analysis + reference images to Claude. Returns generated HTML."""
    print("\n[generate] Sending to Claude for website generation...")

    def _s(lst):
        """Safely convert a list of strings or dicts to a comma-joined string."""
        if not lst:
            return "—"
        parts = []
        for item in lst:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(item.get("name") or item.get("label") or str(item))
        return ", ".join(parts) if parts else "—"

    def _join(lst, sep="\n"):
        """Safely join a list of strings or dicts."""
        if not lst:
            return ""
        return sep.join(
            item if isinstance(item, str) else (item.get("name") or str(item))
            for item in lst
        )

    business_name = analysis.get("business_name", "Business")
    industry      = analysis.get("industry", "")
    tone          = analysis.get("tone", "professional")
    tagline       = analysis.get("tagline", "")
    services      = analysis.get("main_services", [])
    audience      = analysis.get("target_audience", "")
    key_content   = analysis.get("key_content", {})
    features      = key_content.get("features", key_content.get("unique_selling_points", []))

    # Deterministic color extraction beats Claude's analysis (more reliable)
    brand_colors = extract_brand_colors(raw_html) if raw_html else []
    if not brand_colors:
        brand_colors = analysis.get("current_colors", [])

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
    food_keywords = ["restaurant", "pizza", "lieferung", "delivery", "essen", "food",
                     "café", "cafe", "bäckerei", "bakery", "catering", "kebab", "burger",
                     "sushi", "bistro", "gastro", "küche", "kitchen", "bar ", "wirt",
                     "gasthaus", "speise", "trattoria", "pizzeria", "ristorante", "diner"]
    is_tech = any(kw in industry.lower() for kw in tech_keywords)
    is_food = any(kw in industry.lower() for kw in food_keywords)

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
            images_block += (
                f"\n\nOTHER SECTION IMAGES (use in features/about/gallery, NOT hero):\n{images_list}\n"
                "Use <img> tags only. Every <img>: style=\"max-width:100%;height:auto;\" — no zooming, no cropping."
            )
    elif site_image_urls:
        import random as _rnd2
        _layout2 = "fullcover" if is_food else _rnd2.choice(["fullcover", "split-right", "split-left"])
        _food_hint2 = "PRIORITY: pick a food/dish image (pizza, pasta, burger, food platter, dishes) over interior or logo shots.\n" if is_food else ""
        images_list  = "\n".join(f"- {u}" for u in site_image_urls[:10])
        _img_quality_rules2 = """IMAGE QUALITY CHECK — before using any image, judge it strictly:
  ✓ USE if: clearly shows food, product, people, interior, landscape — high resolution, well-lit, professional
  ✗ SKIP if: blurry, pixelated, logo, icon, banner text overlay, screenshot, or tiny
  ✗ SKIP if URL contains: thumb, small, icon, logo, avatar, banner, 50x, 100x, 150x, sprite, pixel
  ✗ SKIP if the image looks generic, low-quality, or does not match the business
  → If no image passes this check: use a CSS gradient hero. Do NOT use a bad image."""

        if _layout2 == "fullcover":
            _hero_layout_rule = f"""HERO IMAGE LAYOUT: FULL-COVER BACKGROUND
{_img_quality_rules2}
Pick ONE image that passes the quality check.
  ✓ CSS on #hero: background-image:url('URL'); background-size:cover; background-position:center;
  ✓ Dark overlay div inside #hero: <div style="position:absolute;inset:0;background:rgba(0,0,0,0.55);z-index:0;"></div>
  ✓ Content wrapper: position:relative;z-index:1; text-align:center;
  ✓ ALL text: color:#ffffff
  ✗ No <img> tag inside the hero
  ✗ background-size:cover is only allowed on the hero background — NEVER on any other element"""
        elif _layout2 == "split-right":
            _hero_layout_rule = f"""HERO IMAGE LAYOUT: SPLIT — text left 55%, image right 45%
{_img_quality_rules2}
Pick ONE image that passes the quality check.
  ✓ Left side: dark background (dark gradient or solid dark brand color), headline + subtext + CTA
  ✓ Right side: <img src='URL' style='width:100%;height:auto;max-width:100%;border-radius:12px;display:block;'>
  ✓ Left text: color:#ffffff
  ✗ No background-image on the hero container
  ✗ NEVER use object-fit:cover or zoom on the image — use height:auto"""
        else:
            _hero_layout_rule = f"""HERO IMAGE LAYOUT: SPLIT — image left 45%, text right 55%
{_img_quality_rules2}
Pick ONE image that passes the quality check.
  ✓ Left side: <img src='URL' style='width:100%;height:auto;max-width:100%;border-radius:12px;display:block;'>
  ✓ Right side: dark background (dark gradient or solid dark brand color), headline + subtext + CTA
  ✓ Right text: color:#ffffff
  ✗ No background-image on the hero container
  ✗ NEVER use object-fit:cover or zoom on the image — use height:auto"""
        images_block = f"""
ORIGINAL SITE IMAGES:
{_food_hint2}{images_list}

IMAGE RULES (all sections):
  ✓ Always use <img> tags with: style="max-width:100%;height:auto;display:block;"
  ✓ Images show exactly as-is — never zoomed, never cropped, never stretched
  ✗ NEVER use object-fit:cover, object-fit:fill, background-size:cover on non-hero images
  ✗ NEVER scale up or zoom an image to fill a container

{_hero_layout_rule}

If no suitable image — CSS-only dark gradient:
  - Restaurant/Food: deep burgundy → warm amber gradient
  - Dental/Medical: deep navy → teal gradient
  - Legal/Finance: dark navy/charcoal, gold accent
  - Beauty/Wellness: blush → mauve gradient
  - Handwerk/Construction: dark slate, diagonal accent lines
  - Generic: dark brand-color gradient, geometric accent
  Hero must look like it came from a top design agency — not a placeholder.

Either way: hero min-height:100svh, overflow:hidden, ALL text color:#ffffff.

GALLERY / ABOUT: use remaining images with <img> tags (max-width:100%;height:auto;display:block;)"""

    # ── Build nav topics (anchor links — single page) ────────────────────────
    pages_analyzed = analysis.get("pages_content", [])
    import re as _re2

    def _slugify(s):
        return _re2.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')

    import html as _html_mod

    def _decode(s):
        """Decode HTML entities like &amp; &AMP; → & """
        return _html_mod.unescape(s).strip()

    nav_topics = []
    seen_nav = set()

    # Pull topic names from scraped subpages first (richest content)
    for pc in pages_analyzed[1:]:
        if len(nav_topics) >= 5:
            break
        label = _decode(pc.get("label", ""))
        if not label or label.lower() in seen_nav:
            continue
        slug = _slugify(label)
        nav_topics.append({"label": label, "href": f"#{slug}", "cta": False})
        seen_nav.add(label.lower())

    # Fill remaining slots from main_services
    for svc in services[:12]:
        if len(nav_topics) >= 5:
            break
        label = svc if isinstance(svc, str) else (svc.get("name") or str(svc))
        label = label.strip()
        if not label or label.lower() in seen_nav:
            continue
        slug = _slugify(label)
        nav_topics.append({"label": label, "href": f"#{slug}", "cta": False})
        seen_nav.add(label.lower())

    # Always include Kontakt
    if not any(t["label"].lower() in ["kontakt", "contact", "kontaktieren"] for t in nav_topics):
        nav_topics.append({"label": "Kontakt", "href": "#kontakt", "cta": False})

    nav_topics = nav_topics[:6]

    # Mark CTA button
    cta_keywords = ["reservier", "buchen", "book", "termin", "anfrage", "kontakt", "contact"]
    marked = False
    for t in reversed(nav_topics):
        if any(k in t["label"].lower() for k in cta_keywords):
            t["cta"] = True; marked = True; break
    if not marked and nav_topics:
        nav_topics[-1]["cta"] = True

    print(f"[generate] Nav topics ({len(nav_topics)}): {[t['label'] for t in nav_topics]}")

    nav_topics_str = "\n".join(
        f"  {'[CTA-BUTTON] ' if t['cta'] else ''}{t['label']} → {t['href']}"
        for t in nav_topics
    )

    # ── Build per-section content blocks from scraped data ────────────────────
    pages_by_label = {}
    if pages:
        for pg in pages[1:]:
            pages_by_label[pg.get("label", "").lower()] = pg.get("text", "")

    section_content_blocks = ""
    for t in nav_topics:
        if t["href"] in ["#kontakt", "#contact"]:
            continue
        slug = t["href"].lstrip("#")
        label = t["label"]
        label_lower = label.lower()

        content_parts = []
        for pc in pages_analyzed:
            if _slugify(pc.get("label", "")) == slug or pc.get("label", "").lower() == label_lower:
                for para in pc.get("key_paragraphs", []):
                    content_parts.append(str(para) if not isinstance(para, dict) else para.get("text", str(para)))
                svcs = pc.get("services_or_items", [])
                if svcs:
                    item_lines = []
                    for s in svcs:
                        if isinstance(s, dict):
                            line = s.get("name", "")
                            if s.get("description"): line += f": {s['description']}"
                            if s.get("price"):       line += f" — {s['price']}"
                        else:
                            line = str(s)
                        item_lines.append(f"  • {line}")
                    content_parts.append("Items:\n" + "\n".join(item_lines))
                for f in pc.get("specific_facts", []):
                    content_parts.append(f"  • {f}" if isinstance(f, str) else f"  • {f.get('name', str(f))}")
                break

        raw_text = "\n\n".join(content_parts)[:4000]
        if len(raw_text) < 400:
            raw_text += "\n\n" + pages_by_label.get(label_lower, "")[:2000]

        section_content_blocks += f"""
━━ SECTION id="{slug}" — "{label}" ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{raw_text.strip() or f'Write content about {label} using the business data above.'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    # ── Build important links block ────────────────────────────────────────────
    links_block = ""
    if important_links:
        LABELS = {
            "google_maps":   "Google Maps",
            "phone":         "Telefon",
            "email":         "E-Mail",
            "pdf":           "PDF",
            "facebook":      "Facebook",
            "instagram":     "Instagram",
            "linkedin":      "LinkedIn",
            "twitter":       "Twitter/X",
            "youtube":       "YouTube",
            "tiktok":        "TikTok",
            "whatsapp":      "WhatsApp",
            "booking":       "Buchungssystem",
            "tripadvisor":   "TripAdvisor",
            "google_review": "Google Bewertungen",
        }
        link_lines = [
            f"  {LABELS.get(l['category'], l['category'])}: {l['href']}" + (f" ({l['text']})" if l['text'] else "")
            for l in important_links
        ]
        links_block = "\nORIGINAL LINKS — use these exact URLs:\n" + "\n".join(link_lines) + "\n"
        links_block += (
            "  • Phone numbers → <a href=\"tel:...\">...</a>\n"
            "  • Emails → <a href=\"mailto:...\">...</a>\n"
            "  • Google Maps → link in footer\n"
            "  • Booking URL → primary CTA button\n"
            "  • Social icons → in footer\n"
            "  • NEVER use href=\"#\" for any real link\n"
        )

    # ── Precompute colors block ────────────────────────────────────────────────
    if brand_colors:
        colors_block = (
            "BRAND COLORS — extracted from the original site. Use these EXACTLY.\n"
            "Do NOT replace them with different colors. Set them as CSS custom properties on :root:\n"
            + "\n".join(f"  {c}" for c in brand_colors)
            + "\nApply the most prominent color as --clr-primary (buttons, links, accents, borders).\n"
            "Use the others as --clr-secondary, --clr-accent etc.\n"
            "The background should match the site's overall feel (light if site is light, dark if dark)."
        )
    else:
        colors_block = "Colors: derive a cohesive palette from the industry and tone — no generic blues or greys."

    # ── Claude prompt ──────────────────────────────────────────────────────────
    content.append({
        "type": "text",
        "text": f"""You are an elite web designer. Study the reference screenshots above carefully — your output must match their quality: typographic scale, whitespace, visual depth, section variety, and overall polish. Build something that looks like it came from a top design studio.

── BUSINESS DATA (never invent — only use what is listed here) ──────────
{links_block}
Name:        {business_name}
Industry:    {industry}
Tone:        {tone}
Tagline:     {tagline or '—'}
Services:    {_s(services)}
Audience:    {audience}
{colors_block}
Headline:    {key_content.get('hero_headline') or '—'}
Subtext:     {key_content.get('hero_subtext') or '—'}
CTA:         {key_content.get('cta_text') or 'Kontakt'}
About:       {key_content.get('about_summary') or '—'}
USPs:        {_s(features)}
Prices:      {_s(key_content.get('prices', []))}
Hours:       {_s(key_content.get('opening_hours', []))}
Phone:       {key_content.get('phone') or '—'}
Email:       {key_content.get('email') or '—'}
Address:     {key_content.get('address') or '—'}
{images_block}

── NAV ──────────────────────────────────────────────────────────────────
ALWAYS dark background: background:#111111 — never transparent, never light.
Logo far left, links right. Min 48px gap. Hamburger on mobile.

USE EXACTLY THESE LINKS — no additions, no removals:
  Home → #
{nav_topics_str}

[CTA-BUTTON] = filled pill, accent color bg, color:#ffffff, border-radius:100px.
All nav links and logo text: color:#ffffff !important — always, without exception.
NEVER use a light background for nav. NEVER use dark text in nav.

── HERO ──────────────────────────────────────────────────────────────────
Full viewport height (min-height:100svh). overflow:hidden on #hero.
All hero text: max-width:100%; word-break:break-word — nothing may overflow the screen.

CONTRAST LAW — most important rule, no exceptions ever:
  • Dark background (image+overlay OR dark gradient) → ALL text: color:#ffffff — set on EVERY element
  • Light background → ALL text: color:#111111 — set on EVERY element
  • NEVER rely on color inheritance — set color explicitly on every h1,h2,p,span,a,button

BACKGROUND:
{images_block}

HEADLINE: Exact text from data above. clamp(3rem,8vw,7rem), bold, line-height:0.95–1.1, explicit color.
SUBTEXT: clamp(1rem,2vw,1.25rem), max 2 lines, explicit color matching contrast law.
LAYOUT: Choose best for industry — centered, split 50/50, or offset headline over image.

CTA BUTTON: pill shape, accent color background, color:#fff, padding:14px 40px, no box-shadow.

Add <!-- HERO_END --> on its own line immediately after the closing </section> of the hero.

── SECTIONS ──────────────────────────────────────────────────────────────
Build one <section> per nav topic (Kontakt goes in the footer, not a section).
Each section's id attribute MUST exactly match the href from the nav (e.g. href="#events" → id="events").

CONTENT — use the scraped text below verbatim, do not invent or paraphrase:
{section_content_blocks}
{_section_design_hints(industry, nav_topics)}
LAYOUT — vary each section's design. Never repeat the same layout twice:
✓ Full-width editorial text with a large pull quote or number
✓ 2-column split: image left + text right (or reversed)
✓ Card grid (2–3 cols desktop, 1 col mobile) — only when items/services exist
✓ Timeline or numbered steps — for process-based content
✓ One dark-background section for contrast (max one per page)
✓ Large stat, year, or metric as a typographic design element

NEVER:
✗ Three identical icon-cards in a row — this is the #1 AI tell
✗ Generic headings like "Our Services", "About Us", "Why Choose Us"
✗ All sections with identical padding and background
✗ Placeholder or invented text
✗ position:absolute or position:fixed on section containers — causes overlapping
✗ Elements that float outside their parent container

── FOOTER / KONTAKT ──────────────────────────────────────────────────────
id="kontakt". Show all contact details (phone, email, address, hours).
Include all nav links. Social media icons (SVG inline) if found in links.
Copyright line. Dark background preferred.

── COPY RULES ────────────────────────────────────────────────────────────
• Use EXACT scraped text — never shorten, paraphrase, or rewrite
• NEVER invent any fact: no numbers, no stats, no prices not in the data
• Missing info → omit entirely. Empty is better than made up.

── TECHNICAL ─────────────────────────────────────────────────────────────
• Single HTML file, all CSS and JS inline
• Google Fonts: pick the proven pair for the industry below — do not deviate:
    Restaurant/Food    → "Playfair Display" (headings) + "Lato" (body)
    Café/Bakery        → "Cormorant Garamond" (headings) + "Nunito" (body)
    Medical/Dental     → "DM Serif Display" (headings) + "DM Sans" (body)
    Beauty/Wellness    → "Cormorant Garamond" (headings) + "Montserrat" (body)
    Handwerk/Trade     → "Oswald" (headings) + "Open Sans" (body)
    Legal/Finance      → "Libre Baskerville" (headings) + "Source Sans 3" (body)
    Tech/SaaS          → "Inter" (headings + body, different weights)
    Luxury/Hotel       → "Cormorant Garamond" (headings) + "Jost" (body)
    Generic/Other      → "Syne" (headings) + "Inter" (body)
• CSS custom properties on :root for all brand colors
• Mobile-first:
  - Nav: hamburger (☰) on mobile, JS toggles .open class
  - Hero headline: clamp(2.5rem,7vw,6rem)
  - Grids: CSS grid, auto-fit or @media(max-width:768px) → 1 column
  - Images: always <img> tags, never background-image with URL; max-width:100%; height:auto; object-fit:contain — no zooming, no cropping
  - Padding: clamp(40px,8vw,120px) vertically, clamp(20px,5vw,80px) horizontally
  - Buttons: min-height:48px
  - No horizontal scroll
• Smooth scroll: <html style="scroll-behavior:smooth">
• Every <img>: onerror="this.style.display='none'"

SCROLL ANIMATIONS — add exactly this JS before </body>:
<script>
(function(){{
  const io=new IntersectionObserver((e)=>{{e.forEach(x=>{{if(x.isIntersecting){{x.target.classList.add('visible');io.unobserve(x.target);}}}});}},{{threshold:0.1}});
  document.querySelectorAll('section,.fade').forEach(el=>{{el.classList.add('fade-up');io.observe(el);}});
}})();
</script>

And this CSS inside <style>:
.fade-up{{opacity:0;transform:translateY(28px);transition:opacity 0.65s ease,transform 0.65s ease;}}
.fade-up.visible{{opacity:1;transform:none;}}

SEO — in <head>:
<meta name="description" content="2-sentence description from scraped content">
<meta property="og:title" content="Business name — main service">
<meta property="og:type" content="website">
<meta name="robots" content="index,follow">

CTA LINKS — every button must have a real href:
1. Booking URL (from links above) → all primary CTAs
2. mailto:email → if no booking URL
3. tel:phone → if no email
4. #kontakt → last resort
NEVER use href="#"

OUTPUT: One complete HTML file from <!DOCTYPE html> to </html>. No markdown fences. No explanation. Just the HTML."""
    })

    for attempt in range(3):
        try:
            with CLIENT.messages.stream(
                model=MODEL_FULL,
                max_tokens=32000,
                extra_headers={"anthropic-beta": "output-128k-2025-02-19"},
                messages=[{"role": "user", "content": content}]
            ) as stream:
                html = stream.get_final_text().strip()
            break
        except anthropic.APIStatusError as e:
            if e.status_code in (529, 500) and attempt < 2:
                wait = (attempt + 1) * 15
                print(f"[generate] API {e.status_code} — retrying in {wait}s (attempt {attempt+1}/3)")
                import time; time.sleep(wait)
            else:
                raise
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
    generated_html = generate_website(analysis, references, site_images, full_text, raw_html=scraped["html"])

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
