"""
wsgi.py
Gunicorn entry point for Render deployment.

Start command (render.yaml):
    gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 300
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "tools"))

from server import app  # noqa: F401 — imported for gunicorn
