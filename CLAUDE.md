# Flight Query (SkyNet)

Multi-platform Chinese OTA flight price comparison app. Aggregates Fliggy + Ctrip, merges by flight number, and shows best-price platform.

## Stack

- **Backend**: Node.js Express (`server.js`), ES modules
- **Frontend**: Vanilla JS SPA (`public/index.html`), dark sci-fi theme
- **Fliggy data**: Node.js MCP client (`fliggy.js`) — direct HTTP call with HMAC-SHA256 + AES-256-GCM. No Python needed.
- **Ctrip data**: Python Playwright scraper (`ota_scraper.py`) using system Chrome headless (local only)
- **Qunar / Tongcheng**: Blocked by anti-bot detection, return 0 results

## Running

```bash
node server.js          # or npm start
# Opens at http://localhost:3000
```

### Public access via Cloudflare Tunnel

```bash
cloudflared tunnel --url http://localhost:3000
# Generates a public *.trycloudflare.com URL
```

Note: `trycloudflare.com` domains may be partially blocked in China. For reliable China access, deploy to a Hong Kong or domestic VPS.

### Production deployment (Netlify)

Site: **https://flight-query.netlify.app** (China-accessible, no VPN needed).

Fliggy search runs via Netlify Function (Node.js `fliggy.js` → `flyai.open.fliggy.com`). No Mac required — the site works 24/7 from anywhere.

Ctrip scraping is unavailable on Netlify (no Chrome/Python). For multi-platform results, run locally:
```bash
node server.js  # Fliggy (Node.js) + Ctrip (Playwright)
```

```bash
# Deploy
npx netlify deploy --dir=public --functions=netlify/functions --prod
```

Must access via `http://localhost:3000` — opening `index.html` directly from Finder (file:// protocol) breaks CORS and autocomplete.

FlyClaw must be cloned to `/tmp/FlyClaw`. Ctrip scraping requires Google Chrome at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`.

## Architecture

```
Browser (index.html)
  → POST /api/search { origin, destination, departureDate, ... }
    → server.js
      → Promise.all:
        → FlyClaw subprocess (Fliggy MCP → JSON)
        → ota_scraper.py subprocess (Playwright → Ctrip DOM extraction)
      → mergeFlightsByNumber()
        → normalize flight numbers (carrier code + digits)
        → group by normalized FN
        → fill missing FNs via time + airline fuzzy matching (±60 min)
        → compute best price platform
      → JSON response with platformPrices[], searchLinks[]
```

## Multi-airport cities

FlyClaw's `airport_manager.resolve_all("北京")` returns `["PEK", "PKX"]`. Server passes city names to FlyClaw via `resolveFliggyInput()` for multi-airport coverage. Ctrip uses IATA city codes (`BJS` for Beijing, `SHA` for Shanghai, `CTU` for Chengdu) via `getCityCode()`.

## Ctrip constraints

- **No direct booking URLs**. Ctrip uses in-page expandable modals (`u_key="flight_action_expand_all_price"`), not `<a>` links. Fallback: search URL with `&flightno=` parameter for pre-filtered results.
- **Visible flights capped at ~7**. Scraper does not scroll — only above-the-fold `.flight-item` cards are captured.
- **~30s latency**. Browser launch + 10s JS render wait + DOM extraction.
- **Anti-detection**: disables AutomationControlled, overrides navigator.webdriver/chrome/permissions/plugins/languages.

## Fliggy constraints

- Direct booking links via `a.feizhu.com/XXXXXX` jump URLs.
- API uses HMAC-SHA256 signing with built-in default credentials. No user configuration needed.
- ~3-5s response time.

## Flight matching

`mergeFlightsByNumber()` in server.js:
1. Normalize flight numbers: extract carrier code prefix (2-char alpha or digit+alpha) + digit suffix
2. Group by normalized FN across Fliggy + Ctrip
3. For OTA flights without flight numbers: match by `departure_time ± 60 min` + airline name
4. Airline name normalization: expand abbreviations (`山航` → `山东航空`), strip `航空` suffix, strip `中国` prefix

## Key files

| File | Purpose |
|------|---------|
| `server.js` | Express server, search endpoint, merge logic, city/airport mapping |
| `public/index.html` | Full SPA frontend with autocomplete, search form, results rendering |
| `ota_scraper.py` | Playwright scraper for Ctrip/Qunar/Tongcheng |
| `.env` | `PORT=3000` only (no secrets) |

## Workflow

**After every round of changes**, before handing control back to the user:

1. Summarize what was modified and why
2. Commit all changes to git with a concise message describing the "why", then `git push`
3. Restart the server: `lsof -ti:3000 | xargs kill -9 2>/dev/null; node server.js &`
4. Open the page: `open http://localhost:3000`

## Do NOT

- Add `file://` protocol support — always use the Express server
- Remove airline abbreviation normalization without checking matching accuracy
- Pass single IATA codes to FlyClaw when user input is a city name
- Label Ctrip links as "direct booking" when they're search pages
