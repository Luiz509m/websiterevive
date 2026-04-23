"""
server.py
WebsiteRevive — Flask API backend.

Endpoints:
    GET  /health                  → {"status": "ok"}
    POST /auth/register           → {"token": "...", "user": {...}}
    POST /auth/login              → {"token": "...", "user": {...}}
    GET  /auth/me                 → {"id": "...", "email": "...", "tokens": n}
    POST /generate                → {"generation_id": "...", "hero_html": "...", "business_name": "..."}
    POST /unlock                  → {"html": "...", "slug": "..."}
    POST /checkout                → {"checkout_url": "..."}
    POST /checkout/verify         → {"tokens_added": n, "new_balance": n}
    POST /deploy                  → {"url": "https://xyz.netlify.app"}
"""

import sys
import os
import io
import json
import zipfile
import traceback
import requests
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

# ── Environment ───────────────────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ── Flask ─────────────────────────────────────────────────────────────────────
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
# Allow all origins — origin restriction is handled by auth tokens, not CORS
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    return response

@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    print(f"[server] Unhandled exception: {e}\n{traceback.format_exc()}")
    from flask import jsonify
    resp = jsonify({"error": str(e)})
    resp.status_code = 500
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp

# ── Internal imports ──────────────────────────────────────────────────────────
import stripe
from auth import (
    hash_password, verify_password,
    create_token, get_current_user_id, require_auth,
)
import db
from generate_website import (
    analyze_website, generate_website, generate_hero_only, load_reference_images,
    extract_image_urls, extract_text_content, validate_image_urls,
)
from scrape_site import scrape, scrape_subpages, extract_important_links

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

TMP = ROOT / ".tmp"
TMP.mkdir(exist_ok=True)

# ── Token packages ────────────────────────────────────────────────────────────
PACKAGES = {
    "test": {"tokens": 1,  "amount_chf": 19.90, "price_id": os.environ.get("STRIPE_PRICE_TEST")},  # beta offer, expires 2026-04-10
    "1":    {"tokens": 1,  "amount_chf": 29.00, "price_id": os.environ.get("STRIPE_PRICE_1")},
    "5":    {"tokens": 5,  "amount_chf": 49.00, "price_id": os.environ.get("STRIPE_PRICE_5")},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

import re as _re
import base64 as _b64

def _css_lum(hex_or_rgb: str) -> float:
    """Return perceived luminance (0–255) from a CSS color string. -1 if unparseable."""
    import re as _r
    h = _r.search(r'#([0-9a-fA-F]{3,6})', hex_or_rgb)
    if h:
        v = h.group(1)
        if len(v) == 3:
            v = v[0]*2 + v[1]*2 + v[2]*2
        r, g, b = int(v[0:2],16), int(v[2:4],16), int(v[4:6],16)
        return 0.299*r + 0.587*g + 0.114*b
    m = _r.search(r'rgba?\s*\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)', hex_or_rgb)
    if m:
        return 0.299*float(m.group(1)) + 0.587*float(m.group(2)) + 0.114*float(m.group(3))
    return -1

def _fix_nav_contrast(html: str) -> str:
    """Force nav/header to always be dark with white text — no detection needed."""
    override = (
        '<style id="revive-nav-fix">'
        'nav,header,.navbar,.nav-wrapper,.site-header{'
        'background:#111111 !important;backdrop-filter:blur(12px) !important;'
        'border-bottom:none !important;}'
        'nav a,nav a *,header a,header a *,.nav-link,.navbar a,'
        'nav li a,nav li a *,nav span,header span,nav button.cta,'
        'header .logo,header .logo *,nav .logo,nav .logo *{'
        'color:#ffffff !important;}'
        '</style>'
    )
    return html.replace('</head>', override + '\n</head>', 1)


def _build_safety_css() -> str:
    # CSS: structural safety rules that don't depend on JS timing
    css = (
        '<style id="revive-safety">'
        # Prevent horizontal overflow — text never cut off
        'html,body{overflow-x:hidden !important;max-width:100vw !important;}'
        'body *{box-sizing:border-box !important;max-width:100% !important;word-break:break-word !important;}'
        # Prevent sections (not nav/header) from stacking on top of each other
        'body>section,body>main,body>div:not(nav):not(header){position:relative !important;z-index:auto !important;}'
        # Nav always dark background + white text — unconditional
        'nav,header,.navbar,.nav-wrapper,.site-header{background:#111111 !important;}'
        'nav a,nav a *,header a,header a *,nav li a,nav li a *,nav span,header span,.nav-link{color:#ffffff !important;}'
        # Nav spacing
        'nav .nav-inner,nav>div,.navbar-inner{gap:clamp(32px,4vw,64px);}'
        '</style>'
    )
    # JS: runs AFTER full page load (window.onload) so computed styles are accurate
    js = r"""<script id="revive-contrast-fix">
(function(){
  function lum(el){
    var m=(getComputedStyle(el).backgroundColor||'').match(/[\d.]+/g);
    if(!m||m.length<3)return -1;
    var a=m[3]!==undefined?+m[3]:1;
    if(a<0.05)return -1;
    return 0.299*+m[0]+0.587*+m[1]+0.114*+m[2];
  }
  function col(el,c){el.style.setProperty('color',c,'important');}
  function textColor(el,c){
    col(el,c);
    el.querySelectorAll('h1,h2,h3,h4,h5,h6,p,span,a,li,button,label,small').forEach(function(t){
      if(lum(t)===-1)col(t,c);
    });
  }
  function fixSection(el){
    var cs=getComputedStyle(el);
    var hasBgImg=cs.backgroundImage&&cs.backgroundImage!=='none';
    var l=lum(el);
    if(hasBgImg){
      // background image: overlay + white text
      if(!el.querySelector('.rv-overlay')){
        var ov=document.createElement('div');
        ov.className='rv-overlay';
        ov.style.cssText='position:absolute;inset:0;background:rgba(0,0,0,0.5);z-index:0;pointer-events:none;';
        el.style.setProperty('position','relative','important');
        el.insertBefore(ov,el.firstChild);
        Array.from(el.children).forEach(function(c){
          if(c!==ov){c.style.setProperty('position','relative','important');c.style.setProperty('z-index','1','important');}
        });
      }
      textColor(el,'#fff');
    } else if(l>160){
      textColor(el,'#111');  // light bg → dark text
    } else if(l===-1){
      // transparent → force dark gradient + white text
      el.style.setProperty('background','linear-gradient(135deg,#0d1117 0%,#1a2236 60%,#0d1117 100%)','important');
      textColor(el,'#fff');
    } else {
      textColor(el,'#fff');  // dark bg → white text
    }
  }
  function fixNav(){
    var nav=document.querySelector('nav,header');
    if(!nav)return;
    var l=lum(nav);
    var c=l>160?'#111':'#fff';
    nav.querySelectorAll('a').forEach(function(a){col(a,c);});
  }
  // Run on DOMContentLoaded AND load to catch both early and late rendering
  function runFixes(){
    var hero=document.getElementById('hero');
    if(hero)fixSection(hero);
    fixNav();
  }
  document.addEventListener('DOMContentLoaded',runFixes);
  window.addEventListener('load',runFixes);
  window.addEventListener('scroll',fixNav,{passive:true});
})();
</script>"""
    return css + js

def parse_multifile_html(full_html: str) -> dict:
    """Split homepage HTML from hidden subpage sections and create separate page files."""

    # Extract shared CSS, nav, footer
    css_m    = _re.search(r'<style[^>]*>(.*?)</style>', full_html, _re.DOTALL)
    nav_m    = _re.search(r'<nav\b[^>]*>.*?</nav>', full_html, _re.DOTALL | _re.IGNORECASE)
    footer_m = _re.search(r'<footer\b[^>]*>.*?</footer>', full_html, _re.DOTALL | _re.IGNORECASE)
    css         = css_m.group(1)    if css_m    else ""
    nav_html    = nav_m.group(0)    if nav_m    else ""
    footer_html = footer_m.group(0) if footer_m else ""
    # Only convert #anchor → anchor.html if it matches a real subpage ID; keep other anchors as-is
    # (run after subpage_ids is collected — defined inline via closure after ids are known below)
    nav_fixed_placeholder = nav_html

    # Collect all subpage IDs first so the intercept script knows which anchors to catch
    subpage_ids = [m.group(1).strip() for m in _re.finditer(r'<!-- SUBPAGE:([^-]+?) -->', full_html)]
    known_files_js = '[' + ','.join(f'"{sid}.html"' for sid in subpage_ids) + ']'
    subpage_ids_set = set(subpage_ids)
    # Build nav_fixed: only convert #anchor → anchor.html for real subpage IDs
    nav_fixed = _re.sub(
        r'href="#([^"]+)"',
        lambda m: f'href="{m.group(1)}.html"' if m.group(1) in subpage_ids_set else f'href="#{m.group(1)}"',
        nav_fixed_placeholder
    )

    def make_intercept(known_js):
        return f"""<script>
(function(){{
  var known={known_js};
  function intercept(){{
    document.querySelectorAll('a[href$=".html"]').forEach(function(a){{
      if(a._wi)return; a._wi=1;
      a.addEventListener('click',function(e){{
        var f=this.getAttribute('href');
        if(f&&f!=='index.html'){{e.preventDefault();window.parent.postMessage({{action:'loadPage',file:f}},'*');}}
      }});
    }});
    document.querySelectorAll('a[href^="#"]').forEach(function(a){{
      if(a._wi)return; a._wi=1;
      a.addEventListener('click',function(e){{
        var id=this.getAttribute('href').slice(1);
        var file=id+'.html';
        if(known.indexOf(file)!==-1){{e.preventDefault();window.parent.postMessage({{action:'loadPage',file:file}},'*');}}
      }});
    }});
  }}
  document.addEventListener('DOMContentLoaded',intercept);
  setTimeout(intercept,500);
}})();
</script>"""

    index_html_raw = _re.sub(r'<!-- SUBPAGE:[^>]+ -->.*?<!-- /SUBPAGE:[^\-]+ -->', '', full_html, flags=_re.DOTALL).strip()
    # Repair broken .html links: known subpages kept, unknown converted to #topic-slug anchor (stays clickable)
    known_set = set(f"{sid}.html" for sid in subpage_ids)
    def repair_html_link(m):
        href = m.group(1)
        if href == "index.html" or href in known_set:
            return f'href="{href}"'
        slug = href[:-5]  # strip .html
        return f'href="#topic-{slug}"'
    index_html_raw = _re.sub(r'href="([^"#][^"]*\.html)"', repair_html_link, index_html_raw)
    index_html = index_html_raw.replace('</body>', make_intercept(known_files_js) + '\n</body>')
    files = {"index.html": index_html}
    subpage_intercept = make_intercept(known_files_js)

    for m in _re.finditer(r'<!-- SUBPAGE:([^-]+?) -->(.*?)<!-- /SUBPAGE:\1 -->', full_html, _re.DOTALL):
        sec_id  = m.group(1).strip()
        content = m.group(2).strip()
        filename = f"{sec_id}.html"
        title = sec_id.replace("-", " ").title()
        subpage_css = (css + """
main.subpage-main{padding:120px 40px 60px;max-width:900px;margin:0 auto;}
main.subpage-main h1{font-size:2.5rem;font-weight:800;margin-bottom:1rem;line-height:1.1;}
main.subpage-main h2{font-size:1.6rem;font-weight:700;margin:2.5rem 0 1rem;}
main.subpage-main h3{font-size:1.2rem;font-weight:600;margin:1.5rem 0 0.5rem;}
main.subpage-main p{line-height:1.75;margin-bottom:1.2rem;font-size:1.05rem;}
main.subpage-main ul,main.subpage-main ol{padding-left:1.5rem;margin-bottom:1.2rem;}
main.subpage-main li{margin-bottom:0.5rem;line-height:1.6;}
main.subpage-main img.sp-img,main.subpage-main img{max-width:100%;height:auto;object-fit:contain;border-radius:10px;margin:2rem 0;display:block;}
.sp-card{background:#f8f8f8;border-radius:12px;padding:1.5rem 2rem;margin-bottom:1.5rem;border-left:4px solid var(--clr-primary,#2d6be4);}
.sp-card h3{margin-top:0;}
.sp-highlight{background:var(--clr-primary,#2d6be4);color:#fff;border-radius:12px;padding:1.5rem 2rem;margin:2rem 0;font-size:1.15rem;font-weight:600;line-height:1.5;}
.sp-steps{counter-reset:step;margin:2rem 0;}
.sp-steps .sp-step{display:flex;gap:1.2rem;margin-bottom:1.5rem;align-items:flex-start;}
.sp-steps .sp-step::before{counter-increment:step;content:counter(step);background:var(--clr-primary,#2d6be4);color:#fff;width:2rem;height:2rem;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:700;flex-shrink:0;}
.sp-cta{display:inline-block;margin-top:2rem;padding:.9rem 2rem;background:var(--clr-primary,#2d6be4);color:#fff;border-radius:8px;text-decoration:none;font-weight:600;font-size:1rem;}""")
        page_parts = [
            "<!DOCTYPE html>",
            '<html lang="de"><head>',
            '<meta charset="UTF-8">',
            '<meta name="viewport" content="width=device-width,initial-scale=1.0">',
            f"<title>{title}</title>",
            f"<style>{subpage_css}</style>",
            "</head><body>",
            nav_fixed,
            '<main class="subpage-main">',
            content,
            "</main>",
            footer_html,
            subpage_intercept,
            "</body></html>",
        ]
        files[filename] = "\n".join(page_parts)
        print(f"[unlock] Created subpage: {filename}")

    return files

def create_zip(files: dict) -> bytes:
    """Package {filename: html} dict into a ZIP archive."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for name, html in files.items():
            zf.writestr(name, html.encode("utf-8"))
    return buf.getvalue()

def extract_hero_html(full_html: str) -> str:
    """Extract everything up to and including <!-- HERO_END --> as a standalone HTML doc."""
    # For multi-file format, extract from first file (index.html)
    if "<!-- FILE:" in full_html:
        files = parse_multifile_html(full_html)
        first = files.get("index.html") or next(iter(files.values()), full_html)
    else:
        first = full_html

    marker = "<!-- HERO_END -->"
    if marker not in first:
        body_start = first.find("<body")
        if body_start == -1:
            return first[:4000] + "</body></html>"
        cutoff = body_start + (len(first) - body_start) // 3
        return first[:cutoff] + "\n</body></html>"

    idx = first.index(marker) + len(marker)
    return first[:idx] + "\n</body>\n</html>"


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the frontend for local development. Vercel handles this in production."""
    return send_from_directory(str(ROOT), "index.html")


@app.route("/brand_assets/<path:filename>")
def brand_assets(filename):
    return send_from_directory(str(ROOT / "brand_assets"), filename)


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.route("/auth/register", methods=["POST"])
def register():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    if not email or not password:
        return jsonify({"error": "Email and password are required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    if db.get_user_by_email(email):
        return jsonify({"error": "An account with this email already exists"}), 409

    user  = db.create_user(email, hash_password(password))
    token = create_token(user["id"], user["email"])
    return jsonify({
        "token": token,
        "user":  {"id": user["id"], "email": user["email"], "tokens": user["tokens"]},
    }), 201


@app.route("/auth/login", methods=["POST"])
def login():
    data     = request.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    password = (data.get("password") or "").strip()

    user = db.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        return jsonify({"error": "Invalid email or password"}), 401

    token = create_token(user["id"], user["email"])
    return jsonify({
        "token": token,
        "user":  {"id": user["id"], "email": user["email"], "tokens": user["tokens"]},
    })


@app.route("/auth/me")
@require_auth
def me(user_id):
    user = db.get_user_by_id(user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"id": user["id"], "email": user["email"], "tokens": user["tokens"]})


# ── Generation ────────────────────────────────────────────────────────────────

@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400
    if not url.startswith("http"):
        url = "https://" + url

    # Strip tracking params, fragments, and redirect deep pages to homepage
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qs
    _p = urlparse(url)
    # Remove utm/ad tracking query params
    clean_qs = {k: v for k, v in parse_qs(_p.query).items()
                if not any(k.startswith(t) for t in
                           ("utm_", "hsa_", "gad_", "gclid", "fbclid", "msclkid"))}
    url = urlunparse(_p._replace(
        query=urlencode(clean_qs, doseq=True),
        fragment=""
    ))
    print(f"[server] Cleaned URL: {url}")

    # Optional: attach generation to logged-in user
    user_id = get_current_user_id()

    try:
        print(f"\n[server] Generating for: {url}")

        try:
            scraped = scrape(url)
        except ValueError as scrape_err:
            return jsonify({"error": str(scrape_err)}), 422
        slug      = scraped["slug"]
        subpages  = scrape_subpages(url, scraped["html"], max_pages=6)

        # references loaded after analysis so we can pass industry
        references = None  # filled after analysis below

        # Collect images from homepage + all sub-pages
        site_images = extract_image_urls(scraped["html"], url)
        for sp in subpages:
            for img in extract_image_urls(sp["html"], sp["url"], max_images=6):
                if img not in site_images:
                    site_images.append(img)
        site_images = site_images[:20]  # collect more before filtering

        # Validate image dimensions — remove tiny/broken images, sort largest first
        site_images = validate_image_urls(site_images, min_dim=350)
        site_images = site_images[:15]  # keep best 15 after filtering

        # Build structured pages list: homepage + each sub-page
        import re as _re
        def _make_id(label: str) -> str:
            return _re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")

        homepage_text = extract_text_content(scraped["html"], max_chars=4000)
        pages = [{"label": "Homepage", "id": "home", "text": homepage_text}]
        full_text_parts = [homepage_text]
        for sp in subpages:
            sp_text = extract_text_content(sp["html"], max_chars=3500)
            label   = sp["label"]
            pages.append({"label": label, "id": _make_id(label), "text": sp_text})
            full_text_parts.append(f"--- PAGE: {label.upper()} ---\n{sp_text}")

        full_text = "\n\n".join(full_text_parts)
        print(f"[server] Pages: {[p['label'] for p in pages]} | Total text: {len(full_text):,} chars")

        # Collect important links from homepage + all subpages
        seen_hrefs = set()
        important_links = []
        for html_source, source_url in [(scraped["html"], url)] + [(sp["html"], sp["url"]) for sp in subpages]:
            for lnk in extract_important_links(html_source, source_url):
                if lnk["href"] not in seen_hrefs:
                    seen_hrefs.add(lnk["href"])
                    important_links.append(lnk)
        print(f"[server] Important links found: {len(important_links)} — {[l['category'] for l in important_links]}")

        # Use cached analysis if available
        analysis_path = TMP / f"{slug}_analysis.json"
        if analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            # If cached analysis lacks pages_content (old format), re-analyze
            if not analysis.get("pages_content"):
                print("[analyze] Cached analysis missing pages_content — re-analyzing")
                analysis_path.unlink()
        if not analysis_path.exists():
            analysis = analyze_website(url, scraped["html"], "", full_text, pages)
            analysis_path.write_text(
                json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        # Now that we have industry from analysis, load matched reference designs
        _industry = analysis.get("industry", "")
        references = load_reference_images(n=4, industry=_industry)

        # Step 1: Generate hero only (cheap — full site generated later on unlock)
        hero_html_full = generate_hero_only(analysis, references, site_images, raw_html=scraped["html"])

        # Apply safety CSS to hero preview
        safety_css = _build_safety_css()
        hero_html_full = hero_html_full.replace('</head>', safety_css + '\n</head>', 1)
        hero_html_full = _fix_nav_contrast(hero_html_full)

        hero_html = extract_hero_html(hero_html_full)

        # Store generation context as JSON in full_html field (full site generated on unlock)
        # analysis + raw_html + hero_html_full stored inline so Render restarts don't break unlock
        pending_context = json.dumps({
            "url":             url,
            "slug":            slug,
            "site_images":     site_images,
            "important_links": important_links,
            "pages":           pages,
            "full_text":       full_text,
            "analysis":        analysis,
            "raw_html":        scraped["html"][:200_000],  # cap at 200KB to stay within DB limits
            "hero_html_full":  hero_html_full,             # complete hero with <head> CSS/fonts
        }, ensure_ascii=False)

        generation = db.save_generation(user_id, url, slug, hero_html, "##PENDING##:" + pending_context)

        print(f"[server] Done — generation {generation['id']}")
        return jsonify({
            "generation_id": generation["id"],
            "hero_html":     hero_html,
            "business_name": analysis.get("business_name", ""),
        })

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ── Unlock (async) ───────────────────────────────────────────────────────────

import threading as _threading

def _build_full_site(generation_id: str, ctx: dict) -> None:
    """Background thread: generate full site HTML and save to DB."""
    try:
        slug            = ctx["slug"]
        site_images     = ctx.get("site_images", [])
        important_links = ctx.get("important_links", [])
        pages           = ctx.get("pages", [])
        full_text       = ctx.get("full_text", "")
        raw_html        = ctx.get("raw_html") or None

        analysis = ctx.get("analysis")
        if not analysis:
            analysis_path = TMP / f"{slug}_analysis.json"
            if analysis_path.exists():
                analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
            else:
                db.update_full_html(generation_id, "##ERROR##:Analysis expired — please paste the URL again to regenerate")
                return

        _industry2 = analysis.get("industry", "")
        references = load_reference_images(n=4, industry=_industry2)
        full_html  = generate_website(
            analysis, references, site_images, full_text, pages, important_links,
            raw_html=raw_html
        )

        # ── Reuse the existing hero so it matches the preview exactly ──────────
        hero_html_full = ctx.get("hero_html_full", "")
        hero_marker    = "<!-- HERO_END -->"
        if hero_html_full and hero_marker in hero_html_full and hero_marker in full_html:
            import re as _re_hero
            hero_styles = "\n".join(
                m.group(0) for m in _re_hero.finditer(r'<style[^>]*>.*?</style>', hero_html_full, _re_hero.DOTALL)
            )
            hero_body_start   = hero_html_full.find("<body")
            hero_body_tag_end = hero_html_full.find(">", hero_body_start) + 1
            hero_end_idx      = hero_html_full.index(hero_marker) + len(hero_marker)
            preserved_body    = hero_html_full[hero_body_tag_end:hero_end_idx]
            if hero_styles:
                full_html = full_html.replace("</head>", hero_styles + "\n</head>", 1)
            full_body_start   = full_html.find("<body")
            full_body_tag_end = full_html.find(">", full_body_start) + 1
            full_hero_end     = full_html.index(hero_marker) + len(hero_marker)
            full_html = full_html[:full_body_tag_end] + preserved_body + full_html[full_hero_end:]
            print("[unlock] ✓ Reused hero preview")

        # Apply safety CSS + watermark
        full_html = full_html.replace('</head>', _build_safety_css() + '\n</head>', 1)
        full_html = _fix_nav_contrast(full_html)
        watermark = (
            '<div style="text-align:center;padding:18px 20px;font-size:11px;'
            'color:rgba(150,150,150,0.7);font-family:sans-serif;letter-spacing:0.3px;'
            'border-top:1px solid rgba(150,150,150,0.15);margin-top:0;">'
            'Website made with '
            '<a href="https://webisterevive.xyz" target="_blank" '
            'style="color:inherit;text-decoration:underline;">WebsiteRevive</a>'
            '</div>'
        )
        full_html = full_html.replace('</body>', watermark + '\n</body>')

        db.update_full_html(generation_id, full_html)
        print(f"[unlock] ✓ Job done — {len(full_html):,} chars saved")

    except Exception as e:
        traceback.print_exc()
        db.update_full_html(generation_id, f"##ERROR##:{e}")
        print(f"[unlock] ✗ Job failed: {e}")


def _package_result(generation: dict, full_html: str) -> dict:
    files      = parse_multifile_html(full_html)
    index_html = files.get("index.html") or next(iter(files.values()), full_html)
    zip_bytes  = create_zip(files)
    zip_b64    = _b64.b64encode(zip_bytes).decode()
    return {"status": "done", "html": index_html, "zip": zip_b64, "slug": generation["slug"]}


@app.route("/unlock", methods=["POST"])
@require_auth
def unlock(user_id):
    data          = request.get_json(silent=True) or {}
    generation_id = (data.get("generation_id") or "").strip()

    if not generation_id:
        return jsonify({"error": "generation_id is required"}), 400

    generation = db.get_generation(generation_id)
    if not generation:
        return jsonify({"error": "Generation not found"}), 404

    full_html = generation["full_html"]

    # ── Already fully generated → return immediately (e.g. re-unlock) ────────
    if not any(full_html.startswith(p) for p in ("##PENDING##:", "##GENERATING##", "##ERROR##:")):
        return jsonify(_package_result(generation, full_html))

    # ── Already generating in background → just return job_id ────────────────
    if full_html.startswith("##GENERATING##"):
        return jsonify({"status": "generating", "job_id": generation_id})

    # ── Error from previous attempt → surface it ─────────────────────────────
    if full_html.startswith("##ERROR##:"):
        return jsonify({"error": full_html[len("##ERROR##:"):]}), 500

    # ── Pending: deduct token and start background job ────────────────────────
    if not db.deduct_token(user_id):
        return jsonify({"error": "Not enough tokens"}), 402

    db.mark_unlocked(generation_id)

    ctx = json.loads(full_html[len("##PENDING##:"):])

    # Mark as generating with Unix timestamp so /status can detect stuck jobs
    import time as _time
    db.update_full_html(generation_id, f"##GENERATING##:{int(_time.time())}")

    _threading.Thread(target=_build_full_site, args=(generation_id, ctx), daemon=True).start()
    print(f"[unlock] Background job started for {generation_id}")

    return jsonify({"status": "generating", "job_id": generation_id})


@app.route("/status/<generation_id>", methods=["GET"])
@require_auth
def job_status(user_id, generation_id):
    import time as _time
    generation = db.get_generation(generation_id)
    if not generation:
        return jsonify({"error": "Not found"}), 404

    full_html = generation["full_html"]

    if full_html.startswith("##GENERATING##"):
        # Check for stuck job: if >10 min since start, treat as error
        try:
            started_at = int(full_html.split(":", 1)[1])
            if _time.time() - started_at > 600:
                db.update_full_html(generation_id, "##ERROR##:Generation timed out (server restart). Please try again.")
                return jsonify({"status": "error", "error": "Generation timed out — please try again"})
        except (IndexError, ValueError):
            pass
        return jsonify({"status": "generating"})

    if full_html.startswith("##PENDING##:"):
        return jsonify({"status": "generating"})

    if full_html.startswith("##ERROR##:"):
        return jsonify({"status": "error", "error": full_html[len("##ERROR##:"):]})

    return jsonify(_package_result(generation, full_html))


# ── Checkout ──────────────────────────────────────────────────────────────────

@app.route("/checkout", methods=["POST"])
@require_auth
def checkout(user_id):
    data    = request.get_json(silent=True) or {}
    package = str(data.get("package") or "").strip()

    if package not in PACKAGES:
        return jsonify({"error": "Invalid package. Choose 1, 5, or 50"}), 400

    pkg      = PACKAGES[package]
    frontend = os.environ.get("ALLOWED_ORIGIN", "http://localhost:5000")

    try:
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price": pkg["price_id"], "quantity": 1}],
            success_url=f"{frontend}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{frontend}?checkout=cancelled",
            metadata={"user_id": user_id, "tokens": str(pkg["tokens"])},
        )
    except Exception as e:
        print(f"[checkout] Stripe error: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify({"checkout_url": session.url})


@app.route("/checkout/verify", methods=["POST"])
@require_auth
def checkout_verify(user_id):
    data       = request.get_json(silent=True) or {}
    session_id = (data.get("session_id") or "").strip()

    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    # Prevent double-crediting
    if db.purchase_exists(session_id):
        user = db.get_user_by_id(user_id)
        return jsonify({"tokens_added": 0, "new_balance": user["tokens"]})

    session = stripe.checkout.Session.retrieve(session_id)
    if session.payment_status != "paid":
        return jsonify({"error": "Payment not completed"}), 402

    # Verify session belongs to this user
    if session.metadata.get("user_id") != user_id:
        return jsonify({"error": "Session mismatch"}), 403

    tokens_bought = int(session.metadata.get("tokens", 0))
    amount_chf    = session.amount_total / 100  # Stripe uses cents

    pkg = next((p for p in PACKAGES.values() if p["tokens"] == tokens_bought), None)
    if pkg:
        amount_chf = pkg["amount_chf"]

    db.record_purchase(user_id, tokens_bought, amount_chf, session_id)
    db.add_tokens(user_id, tokens_bought)

    user = db.get_user_by_id(user_id)
    return jsonify({"tokens_added": tokens_bought, "new_balance": user["tokens"]})


# ── Deploy to Netlify ────────────────────────────────────────────────────────

@app.route("/deploy", methods=["POST"])
@require_auth
def deploy(user_id):
    data          = request.get_json(silent=True) or {}
    generation_id = (data.get("generation_id") or "").strip()

    if not generation_id:
        return jsonify({"error": "generation_id is required"}), 400

    generation = db.get_generation(generation_id)
    if not generation:
        return jsonify({"error": "Generation not found"}), 404
    if not generation.get("unlocked"):
        return jsonify({"error": "Unlock this website first"}), 403

    netlify_token = os.environ.get("NETLIFY_TOKEN", "")
    if not netlify_token:
        return jsonify({"error": "Netlify deployment is not configured"}), 503

    try:
        import time
        headers_auth = {"Authorization": f"Bearer {netlify_token}"}

        # Step 1: Create a new site
        site_res = requests.post(
            "https://api.netlify.com/api/v1/sites",
            headers={**headers_auth, "Content-Type": "application/json"},
            json={},
            timeout=30,
        )
        if not site_res.ok:
            return jsonify({"error": f"Netlify site creation failed: {site_res.status_code}"}), 502
        site_data = site_res.json()
        site_id   = site_data["id"]
        site_url  = site_data.get("ssl_url") or site_data.get("url", "")
        print(f"[deploy] Site created: {site_id} → {site_url}")

        # Step 2: Build zip with index.html as a plain string (not bytes)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.html", generation["full_html"])
        buf.seek(0)
        zip_bytes = buf.read()
        print(f"[deploy] Zip size: {len(zip_bytes):,} bytes")

        deploy_res = requests.post(
            f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
            headers={**headers_auth, "Content-Type": "application/zip"},
            data=zip_bytes,
            timeout=60,
        )
        if not deploy_res.ok:
            return jsonify({"error": f"Netlify deploy failed: {deploy_res.status_code} — {deploy_res.text[:200]}"}), 502

        deploy_data = deploy_res.json()
        deploy_id   = deploy_data.get("id")
        print(f"[deploy] Deploy ID: {deploy_id}, state: {deploy_data.get('state')}")

        # Step 3: Poll until deploy is ready (up to ~20 s to stay within Render timeout)
        for _ in range(7):
            time.sleep(3)
            state_res = requests.get(
                f"https://api.netlify.com/api/v1/deploys/{deploy_id}",
                headers=headers_auth,
                timeout=10,
            )
            if state_res.ok:
                state = state_res.json().get("state", "")
                print(f"[deploy] State: {state}")
                if state in ("ready", "current"):
                    break
                if state == "error":
                    err = state_res.json().get("error_message", "unknown error")
                    return jsonify({"error": f"Deploy failed: {err}"}), 502

        # Use the site URL (from creation), not the deploy URL
        url = site_url or deploy_data.get("ssl_url") or deploy_data.get("url", "")
        print(f"[deploy] Live at {url}")
        return jsonify({"url": url, "site_id": site_id})

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ── Local dev entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*50}")
    print(f"  WebsiteRevive API  ->  http://localhost:{port}")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
