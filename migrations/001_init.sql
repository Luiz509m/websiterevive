-- WebsiteRevive — Supabase Schema
-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)

CREATE TABLE users (
  id            UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  tokens        INTEGER DEFAULT 0 NOT NULL,
  created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE generations (
  id          UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id     UUID REFERENCES users(id) ON DELETE SET NULL,
  url         TEXT NOT NULL,
  slug        TEXT NOT NULL,
  hero_html   TEXT,
  full_html   TEXT,
  unlocked    BOOLEAN DEFAULT FALSE,
  created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE purchases (
  id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id           UUID REFERENCES users(id) ON DELETE SET NULL,
  tokens_bought     INTEGER NOT NULL,
  amount_chf        DECIMAL(10,2) NOT NULL,
  stripe_session_id TEXT UNIQUE NOT NULL,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries
CREATE INDEX idx_generations_user ON generations(user_id);
CREATE INDEX idx_purchases_user ON purchases(user_id);
CREATE INDEX idx_purchases_stripe ON purchases(stripe_session_id);
