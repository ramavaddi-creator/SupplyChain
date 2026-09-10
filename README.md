# India Household Price Intelligence

A local-first household cost-intelligence platform: tracks how commodity
prices move from mandi/wholesale through official benchmarks to local
retail, supermarket, and quick-commerce — and layers pattern detection,
backtested projection, and publish-timing recommendations on top.

Built to the frozen PRD (materiality + evidence, never shock; never call a
price spread "margin"; never claim causation without evidence; every
inflation number carries its time basis).

## What's in here right now

- **~24 months of SYNTHETIC seed data** (869,608 price observations, 52
  commodities, 8 cities, full mandi→official→local_retail→supermarket→
  quick_commerce chain) — clearly labeled as synthetic everywhere in the UI
  and in `data_sources`. Realistic seasonal cycles are deliberately built in
  (tomato/onion/potato/turmeric etc.) so pattern detection has real signal.
- **Real connector code** for AGMARKNET, Consumer Affairs, CPI, and WPI
  (`backend/connectors/`) — written to the actual public APIs, but they need
  internet access + API keys to pull live data. Point them at real sources
  when you run this on your own machine (see below).
- **Six locked intelligence metrics**: Wholesale-to-Retail Spread,
  Quick-Commerce Premium (vs supermarket), Retail Transmission Lag, Price
  Stickiness, Consumer Benefit Pass-Through, Material Household Impact Score.
- **Consumption Intelligence** (`consumption.html`, `/api/consumption/hces`):
  real, sourced MoSPI HCES 2023-24 rural/urban food consumption shares
  (`backend/consumption_seed.py`) — not yet connected to the price engine's
  own baskets; see the honesty note on that page for what's still missing.
- **Per-source Data Status + Confidence Score** on the Sources page: five
  weighted dimensions (Evidence Quality, Source Agreement, Coverage,
  Freshness, Historical Reliability), each scored only from real evidence —
  an unscored dimension contributes 0, never a placeholder.
- **AI intelligence layer** (`backend/intelligence.py`): anomaly detection
  against each commodity's own baseline, year-over-year seasonal pattern
  detection (drift-corrected), backtested trend projection (only shown if it
  beats its own error threshold), and a rule-based publish-timing
  recommendation (Publish Now / Hold / Bundle into Weekly Report).
- **Continuous learning loop**: every "most material" call gets logged;
  14 days later the system checks whether it held up, and nudges its own
  scoring weights. A retroactive backtest across the 24-month seed window
  has already populated this with 7,840 historical checkpoints so the
  Intelligence page isn't empty on day one.

## Quick start

```bash
cd hpi
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt

cd backend
python seed.py                    # (re)generates the 24-month synthetic dataset
python backtest_history.py        # populates the continuous-learning log from that history
uvicorn app:app --reload --port 8811
```

Open **http://127.0.0.1:8811** in your browser. That's the whole app —
Executive Dashboard, Commodity Drilldown, Intelligence, Weekly Report
(print/export to PDF straight from the browser), and Sources.

`data/hpi.db` is already included and seeded, so you can open the app
immediately without running the two Python scripts above — only re-run
them if you want to regenerate the dataset or change the seed logic.

## Switching from synthetic seed data to real data

Each connector in `backend/connectors/` follows the same
fetch→parse→normalize→validate→save shape and is written against the real
public APIs:

- **AGMARKNET**: needs a free API key from https://data.gov.in (My Account
  → API Key). Set `AGMARKNET_API_KEY` as an environment variable.
- **Consumer Affairs**: point `CA_DATA_URL` at whatever structured export
  you're using (the department's public price-monitor pages change their
  export format periodically — check the current one before wiring this up).
- **CPI / WPI**: set `CPI_CSV_PATH` / `WPI_CSV_PATH` to a CSV you maintain
  from MOSPI/OEA's monthly releases (no stable JSON API exists for either;
  a manually-updated CSV is an honest, reasonable V1 approach since these
  update monthly anyway).

None of these connectors will run inside a sandboxed environment without
outbound internet access — run them from your own machine.

## Project structure

```
hpi/
  backend/
    app.py              FastAPI app + all API endpoints
    database.py         SQLite schema
    commodities.py       Master lists: cities, commodities, seasonal factors
    seed.py              Synthetic data generator (24 months)
    backtest_history.py  Retroactive backtest -> populates the learning log
    metrics.py           Core PRD calculations (spread, premium, lag, etc.)
    insights.py          Basket aggregation + Regular/Shock classification
    intelligence.py      Anomaly detection, seasonal patterns, projection,
                          publish-timing, continuous learning loop
    connectors/          Real AGMARKNET / Consumer Affairs / CPI / WPI connectors
  frontend/
    index.html            Executive Dashboard
    commodity.html         Commodity Explorer + Drilldown
    intelligence.html      Pattern detection, publish-timing queue, learning status
    report.html             Weekly Report (print/export-ready)
    sources.html            Data source status (real counts only)
    css/styles.css          Design tokens (Sovereign Intelligence palette)
    js/app.js               Shared nav, fetch helpers, sparkline chart
  data/hpi.db            SQLite database (already seeded)
```

## Accessing this from your phone (including while travelling)

This runs as a local server on your Mac — by default it's only reachable
from that Mac itself. The fastest real fix, including on different WiFi/
mobile networks while travelling, is **Tailscale** (free):

1. Install Tailscale on your Mac (https://tailscale.com/download) and sign in.
2. Install the Tailscale app on your phone and sign in with the same account.
3. On your Mac, run `tailscale ip -4` to get its Tailscale address (looks like `100.x.x.x`).
4. On your phone, with Tailscale connected, open `http://100.x.x.x:8811` —
   same app, reachable from anywhere, no port-forwarding or public exposure.

Keep the `uvicorn` process running on your Mac — Tailscale gets you to the
server, it doesn't replace needing the server to be running.

### Running uvicorn in the background (so it survives closing Terminal)

```bash
cd hpi/backend
nohup ../venv/bin/uvicorn app:app --host 0.0.0.0 --port 8811 > server.log 2>&1 &
```

Note `--host 0.0.0.0` here (not `127.0.0.1`) so it's reachable from Tailscale/
your phone, not just from the Mac itself.

## Deploying this somewhere other than your own machine

**Render (recommended, no rewrite needed)** — this is Python + SQLite, which
Render's free/paid Python runtime supports natively. `render.yaml` in this
repo is a ready-to-use blueprint.

1. Push this repo to GitHub. `data/hpi.db` is gitignored on purpose — it's
   ~150MB (over GitHub's 100MB file limit) and synthetic anyway.
   `app.py`'s `bootstrap_fresh_database()` startup hook detects an empty
   database and re-runs the seed pipeline automatically, so a fresh deploy
   just works without that file. Verified this session: fresh boot correctly
   reseeds 900,091 rows once, and a restart against the already-seeded
   database does NOT duplicate them.
2. On Render: New → Blueprint → point at your GitHub repo. It reads
   `render.yaml` automatically.
3. Set real environment variables in the Render dashboard (never commit
   them): `AGMARKNET_API_KEY`, and later `CA_CSV_PATH`/`WPI_CSV_PATH` if
   you're using manual CSV feeds. Never needed for WPI itself anymore --
   that connector fetches its `.xlsx` live.
4. **Free-tier honesty note:** Render's free web service disk is NOT
   persistent across restarts/redeploys -- any real data ingested since the
   last deploy (a live AGMARKNET pull, a manually-uploaded CSV) will be
   lost on the next restart, and the seed pipeline will just run again.
   Fine for testing the deployment itself; for real accumulating history
   you'll want the paid tier's persistent disk before this matters.
5. Free tier also spins the service down after ~15 minutes idle. The
   internal daily-ingestion scheduler (APScheduler, runs at 06:00) won't
   fire while asleep -- use the "Run Ingestion Now" button on the Sources
   page after visiting, or set up a Render Cron Job (separate free
   allowance) to hit the site periodically and trigger both a wake-up and
   an ingestion run.

**Cloudflare Workers + D1** — considered earlier, but Workers run
JavaScript on an edge runtime; they don't run Python or SQLite. Using this
app there would mean porting the backend (Python → JS) and migrating
SQLite to D1 -- a real rewrite, not a deploy. Worth doing later only if
Render's constraints become the actual bottleneck, as its own scoped
session.

## Honesty notes baked into the design

- YoY is only ever shown when there's 12+ months of real history behind it.
- Seasonal patterns require year-over-year statistical agreement (not just
  one year's noise) before being labeled anything above "Medium" confidence.
- Trend projections are backtested before display; if the backtest fails,
  you see the error rate, not a fabricated forecast.
- Nothing is called "real-time" — every price carries an observation date.
- Wholesale-to-retail differences are always "Price Spread," never "margin."
