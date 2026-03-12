# Workflow: Generate Website from URL

## Objective
Take a customer's existing website URL and automatically generate a modern, optimized replacement website using Claude AI.

## Required Inputs
- `url` — the customer's current website URL
- `--name` — business name (optional, Claude will detect it from the HTML)
- `--refs` — number of reference design images to use (default: 3)

## Pipeline Steps

### Step 1: Scrape (`scrape_site.py`)
- Fetches full HTML via `requests` with a real browser User-Agent
- Saves raw HTML to `.tmp/<slug>.html`
- Optionally takes a Playwright screenshot (requires `playwright install chromium`)

### Step 2: Load Reference Designs
- Picks `--refs` random images from `reference_designs/`
- Encodes as base64 for Claude's vision API

### Step 3: Analyze (`Claude claude-opus-4-6`)
- Sends truncated HTML (max 40,000 chars) to Claude
- Extracts: business name, industry, tagline, services, tone, colors, fonts, key content, weaknesses
- Saves analysis to `.tmp/<slug>_analysis.json`

### Step 4: Generate (`Claude claude-opus-4-6`)
- Sends analysis + reference screenshots to Claude
- Claude generates a complete single-file HTML website
- Saves to `.tmp/<slug>_generated.html`

## Usage

```bash
# Install dependencies first (one time)
pip install anthropic requests python-dotenv playwright
playwright install chromium

# Run the pipeline
cd Revive
python tools/generate_website.py https://example.com
python tools/generate_website.py https://example.com --name "Smith Plumbing" --refs 5
```

## Output Files
- `.tmp/<slug>_generated.html` — the generated website (open in browser)
- `.tmp/<slug>_analysis.json` — Claude's analysis of the original site
- `.tmp/<slug>.html` — raw scraped HTML

## Known Constraints
- HTML truncated at 40,000 chars (most sites fit; JS-heavy SPAs may be sparse)
- Claude vision: max ~20MB per image; reference PNGs should be under 5MB each
- Rate limits: claude-opus-4-6 allows ~4,000 tokens/min on free tier
- Some sites block scraping (Cloudflare, etc.) — try adding delays or Playwright for those

## Edge Cases
- **JS-rendered sites (React/Next.js)**: HTML will be empty shell. Use Playwright screenshot instead and pass image to Claude.
- **Site blocks requests**: Add `--screenshot-only` mode (TODO)
- **Very long HTML**: Already handled by truncation at 40,000 chars

## Improvements Log
- v1: Basic requests scrape + two-step Claude pipeline
