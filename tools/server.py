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
CORS(app, resources={
    r"/auth/*":          {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/generate":        {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/unlock":          {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/checkout":        {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/checkout/*":      {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/deploy":          {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/health":          {"origins": "*"},
})

# ── Internal imports ──────────────────────────────────────────────────────────
import stripe
from auth import (
    hash_password, verify_password,
    create_token, get_current_user_id, require_auth,
)
import db
from generate_website import (
    analyze_website, generate_website, load_reference_images,
    extract_image_urls, extract_text_content,
)
from scrape_site import scrape, scrape_subpages

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")

TMP = ROOT / ".tmp"
TMP.mkdir(exist_ok=True)

# ── Token packages ────────────────────────────────────────────────────────────
PACKAGES = {
    "1":  {"tokens": 1,  "amount_chf": 29.00, "price_id": os.environ.get("STRIPE_PRICE_1")},
    "5":  {"tokens": 5,  "amount_chf": 49.00, "price_id": os.environ.get("STRIPE_PRICE_5")},
    "50": {"tokens": 50, "amount_chf": 149.00, "price_id": os.environ.get("STRIPE_PRICE_50")},
}


# ── Helpers ───────────────────────────────────────────────────────────────────

import re as _re
import base64 as _b64

def parse_multifile_html(full_html: str) -> dict:
    """Split homepage HTML from hidden subpage sections and create separate page files."""

    # Extract shared CSS, nav, footer
    css_m    = _re.search(r'<style[^>]*>(.*?)</style>', full_html, _re.DOTALL)
    nav_m    = _re.search(r'<nav\b[^>]*>.*?</nav>', full_html, _re.DOTALL | _re.IGNORECASE)
    footer_m = _re.search(r'<footer\b[^>]*>.*?</footer>', full_html, _re.DOTALL | _re.IGNORECASE)
    css         = css_m.group(1)    if css_m    else ""
    nav_html    = nav_m.group(0)    if nav_m    else ""
    footer_html = footer_m.group(0) if footer_m else ""
    nav_fixed   = _re.sub(r'href="#([^"]+)"', lambda m: f'href="{m.group(1)}.html"', nav_html)

    # Extract <!-- SUBPAGE:id -->...<!-- /SUBPAGE:id --> blocks
    link_intercept = """<script>
document.querySelectorAll('a[href$=".html"]').forEach(function(a){
  a.addEventListener('click',function(e){
    var f=this.getAttribute('href');
    if(f&&f!=='index.html'){e.preventDefault();window.parent.postMessage({action:'loadPage',file:f},'*');}
  });
});
</script>"""
    index_html_raw = _re.sub(r'<!-- SUBPAGE:[^>]+ -->.*?<!-- /SUBPAGE:[^\-]+ -->', '', full_html, flags=_re.DOTALL).strip()
    index_html = index_html_raw.replace('</body>', link_intercept + '\n</body>')
    files = {"index.html": index_html}

    for m in _re.finditer(r'<!-- SUBPAGE:([^-]+?) -->(.*?)<!-- /SUBPAGE:\1 -->', full_html, _re.DOTALL):
        sec_id  = m.group(1).strip()
        content = m.group(2).strip()
        filename = f"{sec_id}.html"
        title = sec_id.replace("-", " ").title()
        files[filename] = f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title>
<style>{css}
main.subpage-main{{padding:120px 40px 60px;max-width:900px;margin:0 auto;}}
main.subpage-main h1{{font-size:2.5rem;font-weight:800;margin-bottom:1rem;line-height:1.1;}}
main.subpage-main h2{{font-size:1.6rem;font-weight:700;margin:2.5rem 0 1rem;}}
main.subpage-main h3{{font-size:1.2rem;font-weight:600;margin:1.5rem 0 0.5rem;}}
main.subpage-main p{{line-height:1.75;margin-bottom:1.2rem;font-size:1.05rem;}}
main.subpage-main ul,main.subpage-main ol{{padding-left:1.5rem;margin-bottom:1.2rem;}}
main.subpage-main li{{margin-bottom:0.5rem;line-height:1.6;}}
main.subpage-main img{{max-width:100%;border-radius:8px;margin:1.5rem 0;}}
</style>
</head>
<body>
{nav_fixed}
<main class="subpage-main">
{content}
</main>
{footer_html}
<script>
document.querySelectorAll('a[href$=".html"]').forEach(function(a){{
  a.addEventListener('click',function(e){{
    var f=this.getAttribute('href');
    if(f&&f!=='index.html'){{e.preventDefault();window.parent.postMessage({{action:'loadPage',file:f}},'*');}}
  }});
}});
</script>
</body>
</html>"""
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

        scraped   = scrape(url)
        slug      = scraped["slug"]
        subpages  = scrape_subpages(url, scraped["html"], max_pages=4)

        references  = load_reference_images(n=3)

        # Collect images from homepage + all sub-pages
        site_images = extract_image_urls(scraped["html"], url)
        for sp in subpages:
            for img in extract_image_urls(sp["html"], sp["url"], max_images=6):
                if img not in site_images:
                    site_images.append(img)
        site_images = site_images[:15]

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

        full_html = generate_website(analysis, references, site_images, full_text, pages)

        # Inject footer watermark (only at bottom of page, not fixed)
        watermark = (
            '<div style="text-align:center;padding:18px 20px;font-size:11px;'
            'color:rgba(150,150,150,0.7);font-family:sans-serif;letter-spacing:0.3px;'
            'border-top:1px solid rgba(150,150,150,0.15);margin-top:0;">'
            'Website made with '
            '<a href="https://websiterevive.com" target="_blank" '
            'style="color:inherit;text-decoration:underline;">WebsiteRevive</a>'
            '</div>'
        )
        full_html = full_html.replace('</body>', watermark + '\n</body>')

        hero_html = extract_hero_html(full_html)

        generation = db.save_generation(user_id, url, slug, hero_html, full_html)

        print(f"[server] Done — generation {generation['id']}")
        return jsonify({
            "generation_id": generation["id"],
            "hero_html":     hero_html,
            "business_name": analysis.get("business_name", ""),
        })

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ── Unlock ────────────────────────────────────────────────────────────────────

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

    # Deduct 1 token (atomic check)
    if not db.deduct_token(user_id):
        return jsonify({"error": "Not enough tokens"}), 402

    db.mark_unlocked(generation_id)

    full_html = generation["full_html"]

    # Debug: log subpage markers found in generated HTML
    import re as _re2
    markers = _re2.findall(r'<!-- SUBPAGE:([^-]+?) -->', full_html)
    print(f"[unlock] Subpage markers found: {markers}")
    if not markers:
        # Log last 500 chars to see what Claude actually generated at the end
        print(f"[unlock] No markers — HTML tail: ...{full_html[-500:]}")

    files     = parse_multifile_html(full_html)
    index_html = files.get("index.html") or next(iter(files.values()), full_html)

    zip_bytes = create_zip(files)
    zip_b64   = _b64.b64encode(zip_bytes).decode()

    print(f"[unlock] ZIP with {len(files)} file(s): {list(files.keys())}")

    return jsonify({
        "html": index_html,
        "zip":  zip_b64,
        "slug": generation["slug"],
    })


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

    session = stripe.checkout.Session.create(
        mode="payment",
        line_items=[{"price": pkg["price_id"], "quantity": 1}],
        success_url=f"{frontend}?checkout=success&session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{frontend}?checkout=cancelled",
        metadata={"user_id": user_id, "tokens": str(pkg["tokens"])},
    )

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
