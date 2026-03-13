"""
auth.py
Password hashing and JWT helpers.
"""

import os
import bcrypt
import jwt
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import request, jsonify


JWT_SECRET    = os.environ.get("JWT_SECRET", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY    = timedelta(days=30)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: str, email: str) -> str:
    payload = {
        "sub":   user_id,
        "email": email,
        "exp":   datetime.now(timezone.utc) + JWT_EXPIRY,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


def get_current_user_id() -> str | None:
    """Extract user_id from Authorization header. Returns None if missing/invalid."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = decode_token(auth[7:])
        return payload["sub"]
    except Exception:
        return None


def require_auth(f):
    """Decorator: requires valid JWT. Injects user_id as first argument."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        user_id = get_current_user_id()
        if not user_id:
            return jsonify({"error": "Authentication required"}), 401
        return f(user_id, *args, **kwargs)
    return wrapper
