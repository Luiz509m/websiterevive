"""
server.py
WebsiteRevive — Flask API backend.

Endpoints:
    GET  /health          → {"status": "ok"}
    POST /generate        → {"html": "...", "slug": "...", "business_name": "..."}

Deploy on Render via wsgi.py (gunicorn wsgi:app).
Run locally: python tools/server.py
"""

import sys
import os
import json
import traceback
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

# Allow requests from Vercel frontend (update origin after deploying to Vercel)
CORS(app, resources={
    r"/generate": {"origins": os.environ.get("ALLOWED_ORIGIN", "*")},
    r"/health":   {"origins": "*"},
})

# ── Pipeline imports ──────────────────────────────────────────────────────────
from generate_website import (
    analyze_website,
    generate_website,
    load_reference_images,
    extract_image_urls,
    extract_text_content,
)
from scrape_site import scrape

TMP = ROOT / ".tmp"
TMP.mkdir(exist_ok=True)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    """Serve the frontend locally. In production, Vercel handles this."""
    return send_from_directory(str(ROOT), "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/generate", methods=["POST"])
def generate():
    data = request.get_json(silent=True) or {}
    url  = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    if not url.startswith("http"):
        url = "https://" + url

    try:
        # Step 1: Scrape
        print(f"\n[server] Scraping: {url}")
        scraped  = scrape(url)
        slug     = scraped["slug"]

        # Step 2: Load references + extract content
        references  = load_reference_images(n=3)
        site_images = extract_image_urls(scraped["html"], url)
        full_text   = extract_text_content(scraped["html"])

        # Step 3: Analyse — use cached result if available
        analysis_path = TMP / f"{slug}_analysis.json"
        if analysis_path.exists():
            print(f"[server] Using cached analysis for {slug}")
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        else:
            analysis = analyze_website(url, scraped["html"], "")
            analysis_path.write_text(
                json.dumps(analysis, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

        # Step 4: Generate
        generated_html = generate_website(analysis, references, site_images, full_text)

        print(f"[server] Done — {len(generated_html):,} chars generated")

        return jsonify({
            "success":       True,
            "html":          generated_html,
            "slug":          slug,
            "business_name": analysis.get("business_name", ""),
        })

    except Exception as exc:
        traceback.print_exc()
        return jsonify({"error": str(exc)}), 500


# ── Local dev entry point ──────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n{'='*50}")
    print(f"  WebsiteRevive API  ->  http://localhost:{port}")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=port, debug=True)
