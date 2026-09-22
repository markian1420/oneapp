# OneApp

OneApp is a Metro Manila price-awareness dashboard for fuel stations, grocery
commodity movements, nearby shopping places, and public bank card promos.

The app is built around one rule: every price says where it came from. A weekly
regional number, a brand advisory, and a first-hand station note are presented
as different levels of confidence.

| Module | What it answers |
|---|---|
| **Overview** | What needs attention today: station coverage, DOE advisory status, grocery movement, and shortcuts |
| **Fuel map** | Which Metro Manila fuel stations are nearby and what the all-in price gap is |
| **DOE advisory** | Weekly brand and regional fuel price baseline |
| **Grocery map** | Nearby Metro Manila supermarkets and the cheapest known place for an item |
| **Commodity prices** | NCR commodity price movements from the DA Daily Price Index |
| **Where to buy** | Nearby places for a category, with DA benchmarks and bank card offers |
| **Card promos** | Public bank promos, grouped and searchable by issuer/category |
| **Today** | Compact insights from currently available grocery and card-promo data |
| **Where am I** | Region and coverage checks for the current location |

## Contents

- [Data Sources](#data-sources)
- [Fuel Pricing](#fuel-pricing)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Setup](#setup)
- [Configuration](#configuration)
- [Running Locally](#running-locally)
- [Loading Data](#loading-data)
- [Tests And Checks](#tests-and-checks)
- [Deployment](#deployment)
- [Operational Routine](#operational-routine)
- [Design And Data Boundaries](#design-and-data-boundaries)
- [Troubleshooting](#troubleshooting)

## Data Sources

The current application scope is Metro Manila first. Some importers can support
other Philippine areas later, but the screens, defaults, and documentation are
centered on the NCR/Metro Manila workflow.

| Category | Source | Cadence | Notes |
|---|---|---|---|
| Fuel stations and places | OpenStreetMap via Overpass | Manual refresh | Imported as place/station records; station region comes from the import area |
| Fuel baseline | DOE weekly advisory | Weekly | Brand and regional prices; entered manually or imported from CSV/XLSX |
| Fuel regional band | GasWatch PH survey | Weekly/daily source dependent | Loaded as Metro Manila regional prevailing prices, not per-station observations |
| Fuel brand averages | MetroFuel Tracker | Daily source dependent | Adds brand-level advisory rows for diesel and unleaded 91 |
| Grocery commodities | DA Daily Price Index for NCR | Weekdays | Prevailing retail commodity prices across named wet markets |
| Bank card promos | Public bank promo pages | Continuous | Metrobank currently has the structured importer |

The app does not claim live per-station pump prices because no official live
Philippine feed exists. Per-station prices only become first-hand when someone
records a price seen at that station.

## Fuel Pricing

Each station price resolves through these tiers, best first:

| Tier | Shown as | Source |
|---|---|---|
| 1 | **You paid this** | A current first-hand station price within `PRICE_FRESH_DAYS` |
| 2 | **DOE weekly advisory** | Current brand price for the station's DOE region |
| 3 | **Regional price** | Current regional prevailing price for the DOE region |
| 4 | **Older noted price** | A first-hand station price that is now outside the freshness window |
| - | **No price** | Nothing usable on record |

Regional prices are useful coverage, not pump claims. Brand advisory rows beat
regional rows, and fresh first-hand station notes beat both.

The station ranking math lives in `apps/fuel/services.py`:

```text
effective cost = (price * litres) + (round-trip km / km per litre * price)
round-trip km  = straight-line distance * 1.35
```

The road allowance keeps the comparison conservative while avoiding a routing
service dependency.

## Project Structure

```text
OneApp/
|-- apps/
|   |-- core/       # overview, navigation registry, shared table helper, refresh command
|   |-- fuel/       # fuel map, station detail, fuel prices, advisory/import commands
|   |-- grocery/    # grocery map, DA parser, commodity screens, movement services
|   |-- insights/   # Today briefing builders
|   |-- places/     # shared place model, OSM/Overpass imports, location coverage
|   `-- spend/      # Where to buy and public bank card promo directory
|-- assets/         # Tailwind input CSS
|-- config/         # Django settings, URLs, ASGI/WSGI
|-- scripts/        # local build/vendor helpers
|-- static/         # static sources; dist/vendor are built
`-- templates/      # base shell and app templates
```

## Prerequisites

- Python 3.13 preferred; Python 3.11+ should work.
- Node 20+ for Tailwind and vendored frontend libraries.
- SQLite is the default database. Set `DATABASE_URL` for Postgres.
- Docker only if you want to build or run the deployment image locally.

## Setup

```powershell
cd C:\Users\gotosmcr\PycharmProjects\OneApp
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
npm run build
copy .env.example .env
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
```

Generate a `DJANGO_SECRET_KEY` before using the app outside local development:

```powershell
.\.venv\Scripts\python.exe -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

## Configuration

Configuration lives in `.env`; `.env.example` documents the local defaults.

| Variable | Default | Purpose |
|---|---|---|
| `DJANGO_SECRET_KEY` | blank | Required outside throwaway local use |
| `DJANGO_DEBUG` | `True` | Local debug mode |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Host header allowlist |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | blank | HTTPS origins allowed for CSRF |
| `DATABASE_URL` | blank | Uses SQLite when blank |
| `OVERPASS_ENDPOINTS` | public Overpass mirrors | OSM import endpoints, tried in order |
| `OVERPASS_TIMEOUT` | `180` | Overpass request timeout in seconds |
| `PRICE_FRESH_DAYS` | `14` | Freshness window for first-hand station prices |
| `MAP_MAX_STATIONS` | `300` | Maximum fuel station markers returned for one viewport; grocery caps lower in the view |
| `MAP_TILE_URL` | OpenStreetMap raster tiles | Browser basemap tile URL |
| `MAP_TILE_ATTRIBUTION` | OpenStreetMap credit | Attribution shown on Leaflet maps |
| `MAP_TILE_MAX_ZOOM` | `19` | Maximum zoom for the configured tile provider |

The default tile provider is OSM's own server:

```text
https://tile.openstreetmap.org/{z}/{x}/{y}.png
```

It serves without a key, which CARTO's hosted basemap - the previous default -
no longer does: it now stamps "API key required" across every tile. An app of
this size sits inside the OSM Foundation's tile usage policy; a busier one is
expected to move to a provider it pays, which is a change of `MAP_TILE_URL` and
nothing else. Keep the attribution visible whichever provider is in use.

## Running Locally

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000/` and sign in with the superuser account.

After frontend changes, rebuild assets:

```powershell
npm run build
```

## Loading Data

Import Metro Manila places first:

```powershell
.\.venv\Scripts\python.exe manage.py import_places --area NCR
```

Run with `--dry-run` first to see what would be imported. Re-running is safe:
OSM element IDs are used to update matching records instead of duplicating them.
For the grocery map alone, the focused import is:

```powershell
.\.venv\Scripts\python.exe manage.py import_places --area NCR --kind supermarket
```

Load fuel source data:

```powershell
.\.venv\Scripts\python.exe manage.py import_gaswatch
.\.venv\Scripts\python.exe manage.py import_metrofuel
```

Enter the DOE advisory in the app under **Fuel -> DOE advisory**, or import a
CSV/XLSX file:

```powershell
.\.venv\Scripts\python.exe manage.py import_doe_advisory --file prices.csv --week-of 2026-08-17
```

Expected advisory columns are matched loosely:

```csv
region,brand,fuel_type,price
NCR,,gas_95,77.20
NCR,Petron,gas_95,78.45
NCR,,diesel,87.38
```

A blank `brand` means the region's prevailing price.

Load grocery and card-promo data:

```powershell
.\.venv\Scripts\python.exe manage.py import_da_prices
.\.venv\Scripts\python.exe manage.py import_bank_promos --bank metrobank
```

Refresh due sources in one pass:

```powershell
.\.venv\Scripts\python.exe manage.py refresh_all --due-only
```

Places are intentionally excluded from routine refresh unless `--places` is
passed, because Overpass imports are large and rate-limited.

## Tests And Checks

Run targeted tests for the modules you change. Useful focused commands:

```powershell
.\.venv\Scripts\python.exe manage.py test apps.core apps.fuel apps.places apps.spend apps.insights
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py check --deploy
npm run build
```

`check --deploy` reports HTTPS and secure-cookie warnings when local debug
settings are active; production should run with `DJANGO_DEBUG=False` and secure
cookie/SSL settings enabled.

## Deployment

The deployed app runs on free tiers, in three parts:

| Part | Where | Why |
|---|---|---|
| Web app | Render free web service, built from `Dockerfile` | Deploys on push; no card needed |
| Database | Neon free Postgres | A free host gives no persistent disk, so SQLite would be wiped on every deploy |
| Scheduled refresh | GitHub Actions, `.github/workflows/refresh-data.yml` | Render's own cron is a paid feature, and the free web service sleeps |

Two consequences worth knowing before relying on it:

- The web service sleeps after about 15 minutes with no traffic, so the first
  visit afterwards takes roughly 30-60 seconds to answer. Visits after that are
  normal speed.
- The refresh does not depend on the web service being awake. It runs on
  GitHub's runners and writes to the same database.

### First deployment

1. **Create the database.** Make a Neon project and copy its pooled connection
   string. It looks like
   `postgres://user:password@host/dbname?sslmode=require`.

2. **Create the web service.** In Render, create a Blueprint from this
   repository. `render.yaml` describes the service; the only value Render will
   ask for is `DATABASE_URL`, because it is deliberately not in the file. The
   container runs migrations as it boots, so the schema appears on the first
   deploy.

3. **Give the workflows their secrets.** In the GitHub repository settings,
   under Secrets and variables > Actions, add:

   | Secret | Value |
   |---|---|
   | `DATABASE_URL` | The same Neon connection string |
   | `DJANGO_SECRET_KEY` | Any random value; the refresh signs nothing |
   | `ADMIN_USERNAME` | The account you will log in with |
   | `ADMIN_EMAIL` | Its email address |
   | `ADMIN_PASSWORD` | Its password, at least 10 characters |

4. **Create your login.** A new database has no account and the app is
   login-only, so run `Create admin user` once from the Actions tab. The free
   web service has no shell, which is why this is a workflow rather than a
   command you run against it.

5. **Fill the database.** Run `Refresh data` from the Actions tab with `only`
   set to `places_osm`. That is the slow one - hundreds of Overpass queries -
   and without it the maps have nothing to draw. Then run `Refresh data` again
   with `only` empty to pull the fuel baselines, the commodity index and the
   card promos.

6. **Check it.** Open the Render URL and log in. The overview screen reports
   how fresh each source is, which is the quickest confirmation that the
   scheduled refresh is reaching the same database.

#### Moving an existing local database across instead

If there is already data in the local SQLite file worth keeping, load it rather
than re-importing. `PYTHONUTF8` is not optional: place names carry characters
the Windows locale codec cannot encode, and the dump dies on the first one.

```powershell
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe manage.py dumpdata `
  --natural-foreign --natural-primary `
  --exclude contenttypes --exclude auth.permission --exclude sessions `
  --indent 2 --output $env:TEMP\transfer.json

$env:DATABASE_URL = "postgres://user:password@host/dbname?sslmode=require"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py loaddata $env:TEMP\transfer.json
Remove-Item $env:TEMP\transfer.json
Remove-Item Env:\DATABASE_URL
```

The dump carries the user accounts, so step 4 is unnecessary after it. It is
written outside the project and deleted afterwards: it is a plain-text copy of
the whole database, password hashes included. This needs a direct connection to
the database, which many office networks refuse - see Troubleshooting.

### The scheduled refresh

`refresh-data.yml` runs `manage.py refresh_all --due-only --strict` twice a day,
at 07:00 and 19:00 Manila time. `--due-only` means a source that is still
current costs nothing, and `--strict` makes a failed source fail the run, so
GitHub emails about a feed that has gone quiet instead of the data silently
ageing. The overview screen shows the same staleness from the other side.

Places are not in the routine refresh. Re-import them every few months by
running the workflow by hand with its `only` input set to `places_osm`, which
takes that path instead of the due-source pass. Any source key works there when
one needs catching up.

GitHub disables scheduled workflows in a repository with no activity for 60
days. A commit, or one manual run, resets that.

### Configuration in production

Everything comes from the environment. Beyond the values in `.env.example`:

| Variable | Set to | Effect |
|---|---|---|
| `DJANGO_DEBUG` | `False` | Required off anywhere real |
| `DATABASE_URL` | The Neon string | Postgres instead of the local SQLite file |
| `DJANGO_STATIC_MANIFEST` | `True` | Hashed static filenames; the image runs `collectstatic` at build time |
| `DJANGO_SECURE_SSL_REDIRECT` | `True` | Redirect plain HTTP |
| `DJANGO_TRUST_PROXY_SSL_HEADER` | `True` | Read the original scheme from `X-Forwarded-Proto`. Only correct behind a TLS-terminating proxy - without one, a client can claim HTTPS |

`RENDER_EXTERNAL_HOSTNAME` is set by the platform, and settings add it to
`ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, so a rename does not need an edit.

### Running the container locally

```powershell
docker build -t oneapp .
docker run --rm -p 8000:8000 `
  -e DJANGO_SECRET_KEY=local-container-key `
  -e DATABASE_URL="postgres://user:password@host/dbname?sslmode=require" `
  oneapp
```

## Operational Routine

Steps 1 to 4 happen on their own where the app is deployed - see
[Deployment](#deployment). Run them by hand locally, or when a source needs
catching up:

1. Weekly, enter or import the DOE advisory for NCR.
2. Refresh GasWatch and MetroFuel so the Metro Manila regional and brand baselines stay current.
3. On weekdays, import the DA NCR commodity index.
4. Weekly, refresh bank card promos.
5. Every few months, refresh Metro Manila places from OpenStreetMap. This one is
   never automatic: the import is hundreds of queries against public Overpass
   instances that rate-limit hard.

## Design And Data Boundaries

- Lists are filtered, sorted, and paged on the server.
- Map endpoints return only the current viewport and cap marker count with `MAP_MAX_STATIONS`.
- The old all-places map is removed. Fuel and grocery have purpose-specific maps
  so each screen fetches and draws only the markers it needs.
- The grocery map ranks by recorded store prices only when those prices exist;
  otherwise it stays nearest-first and shows the DA NCR benchmark.
- Browser JavaScript receives only fields needed for the current view.
- Leaflet and htmx are vendored into `static/vendor` by `npm run build`.
- Map tiles come from the configured raster tile provider; scripts do not come from a CDN.
- The service worker caches the app shell and pages, but map tiles and live JSON stay network-first.
- The app stores public bank promo facts, not card numbers, card ownership, or wallet data.
- The app is single-user oriented: Django auth is required, but there are no business roles.

## Troubleshooting

**The page has no styling.** Run `npm run build`.

**The map controls appear but the basemap is blank, watermarked, or returns
403.** The tile provider is blocked, unreachable, or wants a key it has not been
given - CARTO, the previous default, now stamps "API key required" across every
tile it serves without one. The default is OSM's own server, which needs no key.
Point `MAP_TILE_URL`, `MAP_TILE_ATTRIBUTION`, and `MAP_TILE_MAX_ZOOM` at any
other Leaflet-compatible raster XYZ provider, key included in the URL if it
wants one.

**The grocery map has stores but no item prices.** That is expected until there
are recorded basket lines for that item at those stores. The DA NCR price is a
benchmark, not a supermarket shelf price.

**The map markers are broken.** Run `npm run build` so `static/vendor` is present.

**Every station shows `No price`.** Import Metro Manila places, then enter the
DOE advisory or load GasWatch/MetroFuel data. Check that stations have `NCR` as
their region; bounding-box imports may lack a region.

**`database is locked` during an import.** SQLite allows one writer. Run imports
one at a time or move to Postgres with `DATABASE_URL`.

**Connecting to the hosted Postgres times out or is refused.** Many corporate
networks block outbound port 5432 while leaving 443 open, so the database is
unreachable from the office even though the deployed app and the scheduled
refresh reach it perfectly well - they connect from elsewhere. Run anything
that needs a direct connection, the initial data transfer in particular, from
a network that permits it.

**`import_places` reports that Overpass would not answer.** Public Overpass
instances rate-limit and return 504/HTML errors under load. Re-run later or
use a smaller area/bounding box for diagnostics.

**Edits to a view do nothing.** Restart the dev server if it is running with
`--noreload`.

**`Missing staticfiles manifest entry`.** `DJANGO_STATIC_MANIFEST=True` was set
without running `collectstatic`. Leave it off locally or collect static files.

Station and place data is derived from OpenStreetMap and remains subject to the
Open Database Licence: https://www.openstreetmap.org/copyright
