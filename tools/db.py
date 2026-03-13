"""
db.py
Supabase client wrapper.
All database operations go through this module.
"""

import os
from supabase import create_client, Client

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        url = os.environ["SUPABASE_URL"]
        key = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
        _client = create_client(url, key)
    return _client


# ── Users ─────────────────────────────────────────────────────────────────────

def create_user(email: str, password_hash: str) -> dict:
    res = get_client().table("users").insert({
        "email": email,
        "password_hash": password_hash,
        "tokens": 0,
    }).execute()
    return res.data[0]


def get_user_by_email(email: str) -> dict | None:
    res = get_client().table("users").select("*").eq("email", email).execute()
    return res.data[0] if res.data else None


def get_user_by_id(user_id: str) -> dict | None:
    res = get_client().table("users").select("id, email, tokens, created_at").eq("id", user_id).execute()
    return res.data[0] if res.data else None


def deduct_token(user_id: str) -> bool:
    """Atomically deduct 1 token. Returns True if successful."""
    user = get_user_by_id(user_id)
    if not user or user["tokens"] < 1:
        return False
    get_client().table("users").update({"tokens": user["tokens"] - 1}).eq("id", user_id).execute()
    return True


def add_tokens(user_id: str, amount: int) -> None:
    user = get_user_by_id(user_id)
    if user:
        get_client().table("users").update({"tokens": user["tokens"] + amount}).eq("id", user_id).execute()


# ── Generations ───────────────────────────────────────────────────────────────

def save_generation(user_id: str | None, url: str, slug: str, hero_html: str, full_html: str) -> dict:
    res = get_client().table("generations").insert({
        "user_id":   user_id,
        "url":       url,
        "slug":      slug,
        "hero_html": hero_html,
        "full_html": full_html,
        "unlocked":  False,
    }).execute()
    return res.data[0]


def get_generation(generation_id: str) -> dict | None:
    res = get_client().table("generations").select("*").eq("id", generation_id).execute()
    return res.data[0] if res.data else None


def mark_unlocked(generation_id: str) -> None:
    get_client().table("generations").update({"unlocked": True}).eq("id", generation_id).execute()


# ── Purchases ─────────────────────────────────────────────────────────────────

def record_purchase(user_id: str, tokens_bought: int, amount_chf: float, stripe_session_id: str) -> dict:
    res = get_client().table("purchases").insert({
        "user_id":           user_id,
        "tokens_bought":     tokens_bought,
        "amount_chf":        amount_chf,
        "stripe_session_id": stripe_session_id,
    }).execute()
    return res.data[0]


def purchase_exists(stripe_session_id: str) -> bool:
    res = get_client().table("purchases").select("id").eq("stripe_session_id", stripe_session_id).execute()
    return bool(res.data)
