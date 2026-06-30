-- WebsiteRevive — hosted_sites table
-- Run this in Supabase SQL Editor (supabase.com → your project → SQL Editor)
-- AFTER 001_init.sql. Required for the hosting tiers (Create + Host, Hosting Only).

-- Idempotent: safe to re-run (won't error if the table already exists).
CREATE TABLE IF NOT EXISTS hosted_sites (
  id                UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  user_id           UUID REFERENCES users(id) ON DELETE SET NULL,
  generation_id     UUID REFERENCES generations(id) ON DELETE CASCADE,
  subdomain         TEXT UNIQUE NOT NULL,
  netlify_site_id   TEXT,
  stripe_session_id TEXT UNIQUE NOT NULL,
  status            TEXT DEFAULT 'active' NOT NULL,
  created_at        TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for common queries (lookups by user, generation, subdomain)
CREATE INDEX IF NOT EXISTS idx_hosted_sites_user       ON hosted_sites(user_id);
CREATE INDEX IF NOT EXISTS idx_hosted_sites_generation ON hosted_sites(generation_id);
CREATE INDEX IF NOT EXISTS idx_hosted_sites_subdomain  ON hosted_sites(subdomain);
