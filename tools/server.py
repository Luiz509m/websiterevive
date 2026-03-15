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
from scrape_site import scrape

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

def extract_hero_html(full_html: str) -> str:
    """Extract everything up to and including <!-- HERO_END --> as a standalone HTML doc."""
    marker = "<!-- HERO_END -->"
    if marker not in full_html:
        # Fallback: use roughly the first third of the body
        body_start = full_html.find("<body")
        if body_start == -1:
            return full_html[:4000] + "</body></html>"
        cutoff = body_start + (len(full_html) - body_start) // 3
        return full_html[:cutoff] + "\n</body></html>"

    idx = full_html.index(marker) + len(marker)
    return full_html[:idx] + "\n</body>\n</html>"


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

    # Optional: attach generation to logged-in user
    user_id = get_current_user_id()

    try:
        print(f"\n[server] Generating for: {url}")

        scraped     = scrape(url)
        slug        = scraped["slug"]
        references  = load_reference_images(n=3)
        site_images = extract_image_urls(scraped["html"], url)
        full_text   = extract_text_content(scraped["html"])

        # Use cached analysis if available
        analysis_path = TMP / f"{slug}_analysis.json"
        if analysis_path.exists():
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        else:
            analysis = analyze_website(url, scraped["html"], "")
            analysis_path.write_text(
                json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        full_html = generate_website(analysis, references, site_images, full_text)

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

    return jsonify({
        "html": generation["full_html"],
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
        site_id = site_res.json()["id"]

        # Step 2: Deploy zip to the new site
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("index.html", generation["full_html"].encode("utf-8"))
        buf.seek(0)

        deploy_res = requests.post(
            f"https://api.netlify.com/api/v1/sites/{site_id}/deploys",
            headers={**headers_auth, "Content-Type": "application/zip"},
            data=buf.read(),
            timeout=60,
        )
        if not deploy_res.ok:
            return jsonify({"error": f"Netlify deploy failed: {deploy_res.status_code} — {deploy_res.text[:200]}"}), 502

        deploy = deploy_res.json()
        url = deploy.get("ssl_url") or deploy.get("url") or f"https://{site_res.json().get('default_domain', '')}"
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
