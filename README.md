# OneApp

A personal budgeting and spend-analysis console for the Philippines, built
around one idea: **every price says where it came from.** A guess is never
presented as a fact.

| Module | What it answers |
|---|---|
| **Map** | What is around me, nearest first — 17,648 places across 8 kinds |
| **Fuel** | Where should I actually refuel, once the detour is paid for |
| **Grocery** | What is cheap this week, from the DA daily index |
| **Spending** | Where the money went, and did the lines add up |
| **Promos** | What is running, and more importantly what has expired |
| **Wardrobe** | Was that jacket worth it — cost per wear |
| **Cards** | Which card do I tap here |
| **Today** | What the app has worked out from my own history |

---

## Contents

- [What data actually exists](#what-data-actually-exists)
- [What this does, and the one thing it cannot do](#what-this-does-and-the-one-thing-it-cannot-do)
- [How prices are decided](#how-prices-are-decided)
- [Project structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running it](#running-it)
- [Loading data](#loading-data)
- [Tests](#tests)
- [Weekly routine](#weekly-routine)
- [Design notes](#design-notes)
- [Known constraints](#known-constraints)
- [Troubleshooting](#troubleshooting)

---

## What data actually exists

Every module was designed around what the Philippines actually publishes, which
varies enormously. This table is the single most useful thing in this file:

| Category | Official feed | Cadence | Fallback |
|---|---|---|---|
| **Fuel** | DOE weekly advisory (brand + region) | Weekly, Tuesdays | Your receipts |
| **Grocery commodities** | **DA Daily Price Index (NCR)** | **Daily** | — |
| **Packaged goods** | DTI SRP bulletin | Occasional | Your receipts |
| **Supermarket SKUs** | None | — | Your receipts only |
| **Dining** | None | — | Your receipts only |
| **Apparel** | None | — | Cost per wear |
| **Promos** | None at all | — | Typed in by hand |
| **Card rewards** | None needed | — | Your own statements |

Verified rather than assumed: the DOE per-station dashboard on
`legacy.doe.gov.ph` no longer resolves, `doe.gov.ph/e-presyo` returns HTTP 500,
brand promo pages are images with the terms baked into the graphic and no dates
in text, and Shopee/Lazada's APIs are seller-side so give a shopper nothing.

**Places come from OpenStreetMap** — free, ODbL, and complete enough to be
useful: 17,648 in Metro Manila alone.

## What this does, and the one thing it cannot do

**It does:**

- Map every fuel station in an area, from OpenStreetMap. Metro Manila alone is
  about 1,060 stations, with brand, street and opening hours where they are tagged.
- Track what you pay. A fill-up records litres, price and total (give any two,
  the third is worked out) and doubles as a dated price observation.
- Rank stations by what a tank *really* costs — the fuel plus the fuel burned
  driving there and back — so a cheap station across town can correctly lose to
  a fair one on your route.
- Keep the DOE weekly advisory as the baseline for stations you have never visited.
- Report fuel economy from consecutive full-tank odometer readings.

**It cannot** show live per-station pump prices, because no such feed exists in
the Philippines:

- The DOE publishes a **weekly** advisory, by **brand and region** — not per
  station, and as a bulletin rather than an API.
- The DOE per-station dashboard that used to sit on `legacy.doe.gov.ph` no
  longer resolves, and `doe.gov.ph/e-presyo` returns HTTP 500.
- Third-party trackers exist but publish no API and grant no reuse licence.

Everything advertising "live PH fuel prices" is really *the weekly advisory plus
people typing in what they saw*. This app does the same thing honestly, and
labels every price with where it came from.

## How prices are decided

Each station's price for a grade resolves through four tiers, best first. The
tier is shown in the UI, on the map pin and in every list — a price is never
presented as more certain than it is.

| Tier | Shown as | Source |
|---|---|---|
| 1 | **You paid this** (green) | A price you logged at that station within `PRICE_FRESH_DAYS` |
| 2 | **DOE weekly advisory** (blue) | This week's advisory for that brand in that region |
| 3 | **Estimated** (amber) | The region's prevailing advisory price, ignoring brand |
| 4 | **Estimated** (amber) | Your own price there, now out of date |
| — | **No price** (grey) | Nothing on record |

Tier 3 deliberately outranks tier 4: pump prices move every Tuesday, so a
three-week-old receipt from the right station is usually further off than this
week's number for the wrong brand. Both are labelled Estimated either way.

The ranking maths lives in `apps/fuel/services.py`:

```
effective cost = (price × litres) + (round-trip km ÷ km per litre × price)
round-trip km  = straight-line distance × 1.35   # road allowance
```

The 1.35 factor keeps the saving estimate conservative. Time and vehicle wear
are not costed — they are real, but not knowable from here, and leaving them out
keeps the number one you can check by hand.

## Project structure

```
OneApp/
├── apps/
│   ├── places/                     # Shared: every place on the map, any kind
│   │   ├── models.py               # Place + PlaceKind (fuel, market, mall…)
│   │   ├── geo.py                  # Distance, shared by every module
│   │   ├── overpass.py             # OpenStreetMap client + the 83 PH areas
│   │   ├── brands.py               # "Sea Oil"/"SEAOIL"/"Seaoil" → one brand
│   │   └── management/commands/import_places.py
│   ├── cards/                      # Which card to tap
│   │   ├── models.py               # Card + Reward rules
│   │   └── services.py             # Ranking, caps, points conversion
│   ├── spend/                      # Purchases, promos, wardrobe
│   │   ├── models.py               # Purchase, PurchaseItem, Promo
│   │   └── services.py             # Category totals, cost per wear
│   ├── insights/                   # The daily briefing
│   │   └── services.py             # Explainable stats, with confidence
│   ├── core/                       # App shell: sidebar registry, overview, table helper
│   │   ├── navigation.py           # The sidebar, as data — add a module here
│   │   ├── tables.py               # Server-side sort / page / page-size helper
│   │   └── views.py                # Overview dashboard + the @module decorator
│   └── fuel/
│       ├── models.py               # Station, PriceObservation, DOEAdvisory, Vehicle, FillUp
│       ├── services.py             # Price resolution, cost ranking, fuel economy
│       ├── overpass.py             # OpenStreetMap client + the 83 PH import areas
│       ├── brands.py               # "Sea Oil" / "SEAOIL" / "Seaoil" → one brand
│       ├── regions.py              # Province → DOE region, for advisory lookup
│       ├── forms.py                # Fill-up, price report, advisory entry
│       ├── views.py                # Map, stations, fill-ups, advisory, vehicles
│       └── management/commands/
│           ├── import_stations.py      # Pull stations from OpenStreetMap
│           └── import_doe_advisory.py  # Load a DOE advisory spreadsheet
│   └── grocery/
│       ├── models.py               # Commodity, CommodityPrice
│       ├── da_index.py             # DA Daily Price Index PDF parser (column geometry)
│       ├── services.py             # Movement over 7/30-day windows, biggest movers
│       ├── chart.py                # Server-computed SVG geometry for the price line
│       ├── views.py                # Commodity list and detail
│       └── management/commands/
│           └── import_da_prices.py     # Fetch and load the DA daily index
├── assets/input.css                # Tailwind source (outside static/ on purpose)
├── config/                         # Settings, URLs, WSGI/ASGI
├── scripts/copy-vendor.mjs         # Vendors Leaflet + htmx into static/
├── static/                         # Icons, manifest; dist/ and vendor/ are built
└── templates/                      # base.html, partials/, core/, fuel/
```

## Prerequisites

- **Python 3.13** (3.11+ works)
- **Node 20+** — for the Tailwind build only, not at runtime
- No database server needed. SQLite by default; Postgres optional.

## Setup

```powershell
cd C:\Users\gotosmcr\PycharmProjects\OneApp

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

npm install
npm run build              # REQUIRED — builds static/dist/app.css and vendors Leaflet/htmx

Copy-Item .env.example .env
# then set DJANGO_SECRET_KEY in .env, see below

.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Generate a secret key with:

```powershell
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

`npm run build` is not optional. `static/dist/app.css` and `static/vendor/` are
gitignored build artifacts, so a fresh clone has no stylesheet and no map
library until it runs.

## Configuration

All settings come from `.env` (see `.env.example`). Nothing sensitive is ever
committed, rendered into a template, or sent to the browser.

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | — | **Required.** Session and CSRF signing. |
| `DJANGO_DEBUG` | `False` | `True` locally. Turning it off enables the secure-cookie and HSTS settings. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated. |
| `DATABASE_URL` | SQLite in project root | e.g. `postgres://user:pass@localhost:5432/oneapp`. Blank means SQLite. |
| `DJANGO_STATIC_MANIFEST` | `False` | Set `True` where `collectstatic` runs, for hashed filenames. |
| `OVERPASS_ENDPOINTS` | two public mirrors | Tried in order by the station importer. |
| `OVERPASS_TIMEOUT` | `180` | Seconds allowed per Overpass query. |
| `PRICE_FRESH_DAYS` | `14` | How long a logged price stays tier 1. |
| `MAP_MAX_STATIONS` | `300` | Ceiling per map viewport request. |

## Running it

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Then sign in at <http://127.0.0.1:8000/>.

While working on the UI, run the Tailwind watcher in a second terminal so class
changes rebuild:

```powershell
npm run dev
```

**Do not use `runserver --noreload` while editing Python.** Views are loaded
once at start-up, so a `--noreload` server keeps serving the code it booted
with and your edits appear to do nothing.

## Loading data

### Stations, from OpenStreetMap

```powershell
# Fuel stations in Metro Manila (the default)
.\.venv\Scripts\python.exe manage.py import_places --area NCR --kind fuel

# Everything: fuel, markets, supermarkets, convenience, dining, malls, pharmacies
.\.venv\Scripts\python.exe manage.py import_places --area NCR --kind all

# One or more provinces, by name or ISO code
.\.venv\Scripts\python.exe manage.py import_places --area Rizal --area PH-CAV

# The whole country: Metro Manila plus all 82 provinces, one query each
.\.venv\Scripts\python.exe manage.py import_places --area all

# A plain bounding box, when the public Overpass instances are struggling
.\.venv\Scripts\python.exe manage.py import_places --bbox 14.35,120.90,14.80,121.15

# See what would change without writing
.\.venv\Scripts\python.exe manage.py import_places --area NCR --dry-run
```

Re-running is safe and is how you refresh: stations are matched on their OSM
element id, so an import updates in place rather than duplicating. Worth
re-running every few months as new stations get mapped.

Province and DOE region are taken from the **area queried**, not from address
tags — only about 13% of Philippine stations carry `addr:province`, but every
station inside Rizal's boundary is in Rizal. A `--bbox` import has no boundary
to derive from, so those stations fall back to whatever the tags admit to and
may end up with no region, and therefore no advisory baseline.

### Grocery commodity prices, from the DA

The DA publishes a Daily Price Index for NCR covering ~160 agri-fishery
commodities — rice, corn, legumes, fish, beef, pork, poultry, vegetables,
spices, fruits — as the prevailing retail price across 33 named wet markets.
Unlike the fuel side, this is a genuine daily feed.

```powershell
# The most recent weekday
.\.venv\Scripts\python.exe manage.py import_da_prices

# A specific day, plus the preceding fortnight, to build a series
.\.venv\Scripts\python.exe manage.py import_da_prices --date 2026-08-17 --backfill 16

# A PDF you already downloaded
.\.venv\Scripts\python.exe manage.py import_da_prices --file Daily-Price-Index-August-17-2026.pdf
```

Weekends are skipped — the DA does not publish then. Re-running a day is safe;
prices are keyed on commodity, region, date and source.

The whole grocery dataset is **derived**, so it can be rebuilt from source at
any time. If a parser change ever strands commodity records, deleting the
`Commodity` and `CommodityPrice` tables and re-importing is a supported repair,
and the importer prunes commodities left with no prices on every run.

### The DOE weekly advisory

Two ways in. The screen is the reliable one:

**Enter it** — *Fuel → DOE advisory*. Pick the week and region, fill in the
grades listed, save. Takes about a minute and depends on nothing staying online.

**Load a file** — for a spreadsheet you downloaded:

```powershell
.\.venv\Scripts\python.exe manage.py import_doe_advisory --file prices.csv --week-of 2026-08-17
```

CSV or XLSX with these columns (headings are matched loosely — `company` works
for `brand`, `product` for `fuel_type`):

```csv
region,brand,fuel_type,price
NCR,,gas_95,77.20
NCR,Petron,gas_95,78.45
NCR,Seaoil,gas_95,76.10
NCR,,diesel,87.38
IV-A,,gas_95,78.90
```

A blank `brand` means the region's prevailing price across brands. Fuel names
are matched loosely too: `unleaded`, `RON 95`, `premium diesel` all resolve.
Rejected rows are reported with a line number rather than skipped silently.

## The other modules

### Cards — the one that needs no feed

Enter each card and what it earns under **Cards → My wallet**. Rewards are rows,
not fields: most cards have a headline rate on one category, a base rate on
everything else, and sometimes a deal tied to one chain. Enter each separately
or the card looks worse than it is everywhere outside its headline category.

Points and miles need a peso-per-point value, otherwise a points card cannot be
compared against a cashback one — and comparing a percentage against a point
count is how people pick the worse card.

**Cards → Which card?** ranks the wallet for one purchase. Arriving from a map
pin or a place screen fills in the category and brand automatically.

The app never asks for a full card number, expiry or CVV. There is no field for
them, and the last-four box refuses a 16-digit entry.

### Spending, promos and the wardrobe

**Spend → Spending** logs groceries, dining and clothes. Fuel has its own screen
because litres and odometer readings mean something specific, but it is counted
in the category totals.

**Spend → Promos** is a notebook, not a feed — nothing in the Philippines
publishes promos machine-readably. The field the form pushes hardest on is the
one nobody publishes: when it ends. Undated promos sort last and get flagged for
review after two months.

**Spend → Wardrobe** tracks cost per wear. Tick *track wears* on a clothing line,
then tap *Wore it* when you use it. Unworn items lead the list.

### Today — the briefing

**Today** is the self-learning layer. It uses rolling medians, month-on-month
deltas and simple comparisons — nothing you could not check by hand. At the
volumes a personal budget produces, anything opaque would be fitting noise.

Every insight states how many observations it rests on, so a thin one reads as
thin. When it has nothing to say it lists exactly what each missing insight
needs, rather than showing an empty screen.

## Offline and installing on a phone

The app ships a web manifest and a service worker, so it installs to a home
screen and keeps working on a bad connection:

- **App shell and libraries** — cache first; instant, and they only change on deploy.
- **Pages** — network first, falling back to the last copy seen. Safe because
  every price in the app is dated, so nothing stale can look current.
- **Map tiles and the places API** — network only. A stale tile is confusing and
  a cached viewport would draw pins that are not there.
- **Admin and sign-in** — never cached, since a cached authenticated page served
  after sign-out would be a real leak.

The worker is served from the site root (`/sw.js`) rather than `/static/`: a
worker fetched from `/static/` can only control `/static/` and would never see a
page navigation.

Offline needs HTTPS (or localhost), so it only activates once the app is hosted.

## Tests

Targeted runs, per module:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.fuel
.\.venv\Scripts\python.exe manage.py test apps.grocery
```

202 tests. **Fuel (55)**: brand normalisation, region mapping, the four price
tiers, detour-aware ranking, fuel economy, fill-up arithmetic, the map
endpoint's bounding box and cap, open-redirect refusal, both importers, and
that every screen renders empty and populated.

**Grocery (34)**: the DA PDF parser — column splitting by geometry, `n/a`
handling, wrapped cells, the methodology footer, and a regression for the
layout drift described below. These use synthetic word coordinates rather than
a checked-in PDF, since coordinates are the parser's actual input and a binary
fixture makes failures much harder to read. Plus the movement maths (window
comparison, refusing to report a month it cannot see), the chart geometry
(flat series, single reading, viewBox bounds) and the screens.

Also worth running after settings changes:

```powershell
.\.venv\Scripts\python.exe manage.py check --deploy
```

With `DJANGO_DEBUG=True` this reports the HTTPS and cookie warnings by design —
those settings are gated behind `DEBUG=False` and switch on in deployment.

## Weekly routine

1. **Tuesday** — the DOE advisory takes effect. Enter it under *Fuel → DOE
   advisory*. The Overview flags it as `Stale` until you do.
2. **Whenever you refuel** — log the fill-up. Twenty seconds, and it puts a real
   price on the map that outranks every estimate for the next two weeks.
3. **In passing** — note a price board from a station page without buying. It
   still counts as first-hand.
4. **Every few months** — re-run `import_stations` to pick up newly mapped
   stations.

## Design notes

**Everything is filtered on the server.** Lists are sorted, paged and searched
in the database; the map asks for the bounding box it is showing and gets at
most `MAP_MAX_STATIONS` back. A client-side filter is not a filter — it is a
full copy of the data with some of it hidden. When the cap does bite, the UI
says *"showing 300 of 812 here"* rather than quietly drawing a subset.

**Price resolution is two queries regardless of station count.** Resolving per
marker would be a query per pin. `services.quotes_for()` batches it, and a test
pins the query count.

**The domain logic has no web in it.** `services.py` takes stations and returns
prices and rankings — no request, no template. That is what makes the planned
mobile client a matter of adding an endpoint rather than reimplementing the
maths, and `/fuel/stations.json` already shows the shape.

**Leaflet and htmx are served from this origin**, vendored out of `node_modules`
by `npm run build`. No CDN: the map needs to work on a phone with a poor
connection at a petrol station, and a third-party script origin is one more
thing that can fail. Map *tiles* necessarily come from openstreetmap.org.

**Dark mode is real, not an afterthought.** OpenStreetMap only publishes a light
basemap, so the tile layer is inverted with a hue rotation rather than pulling
in a second tile provider.

## Known constraints

- **No live price feed exists.** Covered above. The app is built around that
  fact rather than pretending otherwise.
- **City coverage is partial.** Only about 45% of stations carry `addr:city` in
  OSM, so the city column is blank for some. Region is reliable; city is not.
- **Distances are straight-line × 1.35**, not routed. Good enough to rank
  nearby options, not a substitute for a navigation app.
- **Fuel economy needs full-to-full pairs.** Partial fills are excluded, because
  an unknown amount left in the tank makes litres-per-distance meaningless.
- **Single user.** Django auth is in place and every view requires login, but
  there are no roles and data is not partitioned per user.
- **Public Overpass instances rate-limit.** The importer backs off and retries
  across mirrors; a national import can still take a while.
- **The DA index covers commodities, not shelf SKUs.** It prices "Pork Belly
  (Liempo), Local" across NCR, not a specific cut at a specific supermarket. No
  Philippine supermarket publishes shelf prices, so per-store grocery pricing
  will depend on logged receipts, exactly as fuel does.
- **The DA index is NCR only.** Regional offices publish their own bulletins in
  different formats; only the NCR edition is parsed.
- **About 3% of commodity labels carry wrapping artifacts** — e.g. a
  specification fragment such as `diameter/bunch hd)` left on the label. Prices
  and the commodity key are correct and stable across days, so series and
  comparisons are unaffected; only the displayed text is imperfect.
- **DA layout drifts between editions.** The 5 August 2026 edition sets its
  price cells 3.12pt above the commodity baseline where the 17 August edition
  aligns them. The parser tolerates 8pt against a 20.76pt row pitch. If a future
  edition drifts further, the importer will report dropped rows rather than
  silently losing them — treat any non-zero `DROPPED` count as a bug to fix.

## Troubleshooting

**The page has no styling.** `npm run build` has not been run, or
`static/dist/app.css` was cleaned. Run it again.

**The map is blank and markers are broken images.** `static/vendor/` is missing.
`npm run build` vendors it; `npm run vendor` alone does just that part.

**`import_stations` reports "Overpass would not answer".** The public instances
were busy — they answer with 504s or an HTML error page under load. It retries
five times across both mirrors with backoff, then gives up on that area and
moves to the next without corrupting anything. Wait and re-run; it resumes
cleanly. If it persists, use `--bbox` for a smaller, cheaper query.

**Every station shows "No price".** Expected on day one. Either enter a DOE
advisory or log a fill-up. Also check the stations have a region — a `--bbox`
import may leave it blank, which means no advisory can match.

**Edits to a view do nothing.** The server is running with `--noreload`.

**`settings.DATABASES is improperly configured`.** `DATABASE_URL` is set to
something unparseable in `.env`. Leave it blank for SQLite.

**`Missing staticfiles manifest entry`.** `DJANGO_STATIC_MANIFEST=True` without
having run `collectstatic`. Either run it, or leave the variable off locally.

---

Station data © OpenStreetMap contributors, licensed under the
[Open Database Licence](https://www.openstreetmap.org/copyright).
