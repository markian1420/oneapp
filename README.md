# OneApp

A personal budgeting and spend-analysis console for the Philippines, built
around one idea: **every price says where it came from.** A guess is never
presented as a fact.

| Module | What it answers |
|---|---|
| **Map** | What is around me, nearest first — 18,534 places across 9 kinds |
| **Where am I** | Which parts of the app actually apply where I am standing |
| **Fuel** | Where should I actually refuel, once the detour is paid for |
| **Grocery** | What is cheap this week, from the DA daily index |
| **Where to buy** | What is near me, what I paid there, what promos are running |
| **Spending** | Where the money went, and did the lines add up |
| **Promos** | What is running, and more importantly what has expired |
| **Product watch** | Who legitimately sells this, and at what price |
| **Wardrobe** | Was that jacket worth it — cost per wear |
| **Card promos** | Which banks are running offers, and where |
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
| **Fuel** | DOE weekly advisory (brand + region), **GasWatch PH survey** | Weekly | Your receipts |
| **Grocery commodities** | **DA Daily Price Index (NCR)** | **Daily** | — |
| **Packaged goods** | DTI SRP bulletin | Occasional | Your receipts |
| **Supermarket SKUs** | None | — | Your receipts only |
| **Dining** | None | — | Your receipts only |
| **Apparel** | None | — | Cost per wear |
| **Promos** | None at all | — | Typed in by hand |
| **Card promos** | **Metrobank publishes structured JSON** | Continuous | Typed in by hand |

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

### Fuel prices, from the GasWatch PH survey

The DOE's own site returns HTTP 500 on every endpoint, which leaves the fuel map
with nothing. GasWatch PH publishes a JSON endpoint covering **1,292 Metro Manila
stations across five grades**, and its `robots.txt` allows it.

```powershell
.\.venv\Scripts\python.exe manage.py import_gaswatch
```

**Imported as a regional price band, not per-station prices.** Their payload keys
stations by an opaque numeric id with no name, brand or coordinates published
anywhere on the site, so there is no honest way to attach a figure to a
particular pump — and GasWatch themselves describe the values as derived from the
weekly DOE advisory rather than observed at a pump.

So the **median becomes the region's prevailing price**, which lands at the
*Estimated* tier where a regional figure belongs and finally gives all 1,059
mapped stations a number. Anything you log yourself still outranks it.

The **spread is imported alongside it**, and is the more interesting half:

| Grade | Min | Median | Max | Spread |
|---|---|---|---|---|
| Unleaded (RON 91) | ₱67.80 | ₱79.00 | ₱90.77 | **₱22.97** |
| RON 95 | ₱68.80 | ₱81.60 | ₱96.97 | ₱28.17 |
| Diesel | ₱85.10 | ₱91.30 | ₱97.70 | ₱12.60 |

₱22.97 a litre on unleaded is about **₱919 on a 40-litre tank** — which is the
entire argument for comparing stations, in real numbers.

GasWatch states no reuse licence, so this is for personal use, and every row
records the source.

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

### Card promos — a directory, not a wallet

**The app stores no card of anyone's.** There is no card model, no number, no
last four digits, and no list of what you hold — a test asserts that no such
model exists, so the guarantee cannot quietly erode.

**Spend → Card promos** is a directory of offers the banks are publicly running,
grouped by issuer and filterable by bank and category. Those are facts about the
banks, not about you. You read the list and decide for yourself which of your
cards qualifies; the app never learns the answer.

Bank pages turn out to be the best data source in the whole project. Metrobank's
promos page is a Next.js app whose `__NEXT_DATA__` payload carries **every promo
as a structured record** — title, description, start and expiry timestamps,
qualifying cards and a category. No HTML scraping, no OCR, and a real end date on
every row.

```powershell
.\.venv\Scripts\python.exe manage.py import_bank_promos --bank metrobank
```

That pulled **2,610 published promos, of which 667 are still live** — the other
1,943 had already expired and were left out, which is the entire point. Where a
promo names a brand the app already has on the map (Domino's, IKEA, Watsons,
Genki Sushi), it is filed under that place's real category rather than the bank's
looser label.

BPI publishes "Valid until" as plain text too, so it is a good candidate for the
next importer. Merchant promos remain hand-entered — their terms are baked into
graphics.

A purchase records **how** you paid as free text ("BPI credit", "cash", "GCash").
That is a note for grouping spend by payment method, not card data.

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

### Where to buy

**Spend → Where to buy** answers "I am in Pasig, where do I get this" for
groceries, dining, clothing and pharmacy. Share a location and it lists what is
nearest, with three things attached to each place:

- **distance**, which it knows exactly;
- **promos naming that brand**, which the banks publish and which are real;
- **what you paid there**, once you have logged a basket.

It is **ordered by distance, not price, and says so on the screen.** Fuel can be
ranked by price because the DOE publishes a weekly advisory for every brand. No
Philippine supermarket publishes shelf prices at all, so the only per-store
grocery price that can ever exist is one off your own receipt. Ranking shops by
a number the app does not have would be inventing it.

Two deliberate refinements:

- **Supermarkets and markets outrank convenience stores.** Metro Manila has
  3,670 convenience stores against 733 supermarkets, so by distance alone the
  answer to "where do I buy chicken" was nine 7-Elevens — technically nearest,
  useless as advice.
- **Category-wide promos are listed once, beside the table**, not against every
  row. Showing "23 promos" against every mall made them all look identical.

Where the DA tracks the item, its regional price appears as a benchmark for what
a fair ask looks like — explicitly not a shelf price.

### Product watch — the counterfeit problem

**Spend → Product watch** tracks a specific item — *Salomon XT-6 Gore-Tex*, not
"shoes" — across sellers.

**It does not search the web for prices, and will not pretend to.** No Philippine
retailer publishes product prices in any machine-readable form: Shopee and Lazada
expose seller-side APIs only, and the brands' own stores are client-rendered with
no product schema. Salomon PH's page carries a single `Organization` JSON-LD
block and prices as loose text not bound to any SKU. Scraping it would break on
the next redesign and would sometimes report the wrong shoe's price with total
confidence.

What it does instead is the part software is actually good at: **keeping the
sellers straight.**

Prices are grouped by **who is selling** before they are sorted by price:

| Tier | Meaning | Set by |
|---|---|---|
| Brand's own store | Domain verified against the brand | Auto-detected |
| Authorised stockist | The brand recognises them | **You**, after checking |
| Marketplace seller | Shopee/Lazada — genuine and fake side by side | Auto-detected |
| Unverified | Nobody has checked | Default |

The app only ever auto-assigns the two it can verify. It never upgrades an
unknown seller on its own, because a reassuring badge on a seller nobody checked
is worse than no badge.

**Lookalike domain detection.** Searching for a Salomon XT-6 in the Philippines
returns `ph.salomon.com` and `salomophilippines.com` side by side — one is the
brand, the other is a misspelling. The app flags a domain whose name is a
near-miss of the brand's. It compares the start of the hostname as well as the
whole of it: `salomophilippines` scores only 0.58 against `salomon` as a whole
word, but its first six characters score 0.92.

Sorting purely on price would put a ₱5,999 unverified listing above the brand's
own ₱12,990 — which reads as a recommendation to buy the suspicious one.

### Today — the briefing

**Today** is the self-learning layer. It uses rolling medians, month-on-month
deltas and simple comparisons — nothing you could not check by hand. At the
volumes a personal budget produces, anything opaque would be fitting noise.

Every insight states how many observations it rests on, so a thin one reads as
thin. When it has nothing to say it lists exactly what each missing insight
needs, rather than showing an empty screen.

## Travelling — what recalibrates and what does not

The app was set up around Metro Manila, and **almost everything in it is
regional**: the DOE publishes fuel advisories per region, the DA publishes its
commodity index for NCR only, and places are imported one province at a time.

Drive home to the province and none of that fails loudly. The map just looks
empty and the prices just look like prices. So **Where am I** resolves your
region and states what stopped applying.

Standing in Batangas City with a Metro Manila install:

```
Batangas City, Batangas — CALABARZON (IV-A) — 0 places imported

[warn] No places imported for CALABARZON
       fix: python manage.py import_places --area "Batangas" --kind all
[warn] No fuel advisory for CALABARZON this week
       fix: Enter it under Fuel, DOE advisory — pick this region
[warn] The commodity index does not cover this region
       fix: Treat commodity prices as NCR-only while you are here
```

That third one is the important one, because it is the gap that would otherwise
be invisible. The commodity screen keeps working perfectly and shows Metro
Manila wet market rates — the wrong benchmark 100km away. Regional DA offices
publish their own bulletins in their own formats, and this app does not read
them.

Location is resolved through Nominatim, OpenStreetMap's own lookup service.
Results are cached against a coordinate rounded to about a kilometre, so a day
of moving around one city costs a single lookup — the service is free and asks
for no more than a request a second. Nothing about your location is stored
beyond that rounded point.

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
4. **Every few months** — re-run `import_places` to pick up newly mapped
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

**`database is locked` during an import.** SQLite allows one writer at a time,
so two imports running at once will collide. Run them one after another, or move
to Postgres via `DATABASE_URL` if you want them concurrent.

**`import_places` reports "Overpass would not answer".** The public instances
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
