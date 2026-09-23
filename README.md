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
| DOE advisory | Where the week's prices are entered. Signed in only, and not in the public sidebar |

Every screen above is public. Signing in is only needed to record something -
a weekly advisory, a price seen at a pump - which is what keeps the numbers
everyone reads from being editable by anyone.

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

OSM maps the same station three ways depending on who mapped it - a node, a
traced forecourt, or a multipolygon relation where the forecourt has a hole in
it. The importer asks for all three. It did not always: asking for nodes and
ways alone quietly lost 61 Metro Manila fuel stations, and a query that does
not ask cannot report what it missed. If stations are missing after an import,
check the element type in OSM before assuming the data is not there.

Station coverage is OpenStreetMap's, not a commercial map's, and stops at the
boundary of the area imported: a Metro Manila import holds every `amenity=fuel`
inside the NCR relation and nothing across the line in Rizal or Cavite. OSM
also tends to record the brand rather than the branch, so a station reads
"Petron" where a commercial map says "Petron Capital Commons". Import the
neighbouring provinces with `--area` if the boundary is the problem.

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
| `MAP_TILE_URL` | Esri World Street Map tiles | Browser basemap tile URL |
| `MAP_TILE_ATTRIBUTION` | Esri and contributor credit | Attribution shown on Leaflet maps |
| `MAP_TILE_MAX_ZOOM` | `19` | Maximum zoom for the configured tile provider |

The default tile provider is Esri, which serves without a key:

```text
https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}
```

Note the `{z}/{y}/{x}` order, which is Esri's rather than the `{z}/{x}/{y}` most
providers use. The two obvious alternatives were tried and rejected:

| Provider | Why not the default |
|---|---|
| CARTO Positron | Stamps "API key required" diagonally across every tile served without a key |
| OSM Foundation | Answers 403 to whole networks under its tile usage policy; a corporate proxy's shared egress is exactly the kind of address it refuses, and the browser cannot identify itself out of that |

Either works with an account: put the key in `MAP_TILE_URL`, which is why the
tile provider is configuration rather than a constant. Keep the attribution
visible whichever provider is in use.

## Running Locally

```powershell
.\.venv\Scripts\python.exe manage.py runserver
```

Open `http://127.0.0.1:8000/`. Every screen reads without an account; sign in
with the superuser to enter an advisory, note a price, or pin a station.

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

4. **Create your login.** The screens are public, but recording a price is
   not, and a new database has no account to do it with. Run `Create admin
   user` once from the Actions tab. The free web service has no shell, which
   is why this is a workflow rather than a command you run against it.

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

The `places_area` input takes the same treatment for areas: `NCR PH-RIZ`
imports both, and a province that is never imported has no places in the app
however close it is. For somewhere like Pasig that matters - the stations a
few minutes away in Cainta are in Rizal, not NCR.

All nine kinds of place in one pass does not finish on a hosted runner. The
import is hundreds of Overpass queries and the public instances rate-limit by
address; a shared runner address is throttled far harder than a home
connection, so most of the run is spent waiting. Use the `places_kind` input to
take a kind or two at a time - `supermarket convenience` - and what is already
imported stays imported, so the next run picks up the rest rather than starting
over.

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
| `MAINTENANCE_SCREENS` | Navigation codes | Screens that answer with a maintenance page instead of running. Empty by default |

A parked screen is listed by its navigation code from `apps/core/navigation.py`,
comma-separated. The check sits in the `module` decorator every screen already
wears, so a parked screen runs no queries, and the sidebar marks the row rather
than leaving a dead link. The deployment currently parks `spend_where`, which
needs place data it does not have yet; deleting that line in `render.yaml`
brings it back.

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
- Reading is public and needs no account: the point of the app is telling
  anyone where fuel is cheaper, and a login in front of that defeats it.
- Writing is not. Entering an advisory, noting a price at a station, pinning a
  place and tracking a commodity all require signing in, because they change
  what everyone else then reads. The rule lives in one decorator,
  `apps.core.views.editing_requires_login`, rather than in each view.
- There are no business roles. One account curates; everyone else reads.
- The DOE advisory screen is not public at all, rather than public and
  read-only. It exists to type the week's prices into, and its sidebar row is
  hidden from anyone who would only be shown a login by following it.

## Troubleshooting

**The page has no styling.** Run `npm run build`.

**The map controls appear but the basemap is blank, watermarked, or returns
403.** The tile provider is blocked, unreachable, or wants a key it has not been
given. Which of the three it is matters, because only one of them is about this
app: a watermark means the provider wants a key; a 403 usually means the
provider has blocked the network the *browser* comes out of, not the server -
an office proxy's shared egress address collects blocks from other people's
traffic, and no header the app sends changes that. Point `MAP_TILE_URL`,
`MAP_TILE_ATTRIBUTION`, and `MAP_TILE_MAX_ZOOM` at another Leaflet-compatible
raster XYZ provider, key included in the URL if it wants one. Check a provider
by opening one of its tile URLs directly in the browser that has the problem;
that separates a blocked network from a broken setting in about ten seconds.

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

**The places import spends most of its time retrying.** That is what a 504
from Overpass means: the public instances are shared, they shed load rather
than queue, and a hosted runner's address is throttled harder than a home
connection. The importer rotates where each query starts so consecutive
queries do not all queue behind the same instance, and backs off rather than
hammering. Import a kind or two at a time and let it take as long as it takes.

Every instance in `OVERPASS_ENDPOINTS` must carry planet-wide data. A national
mirror answers a Philippine query in a second, with a valid empty result and
no error at all - which is why an empty answer is now confirmed against a
second instance before it is believed.

**`import_places` reports that Overpass would not answer.** Public Overpass
instances rate-limit and return 504/HTML errors under load. Re-run later or
use a smaller area/bounding box for diagnostics.

**Edits to a view do nothing.** Restart the dev server if it is running with
`--noreload`.

**`Missing staticfiles manifest entry`.** `DJANGO_STATIC_MANIFEST=True` was set
without running `collectstatic`. Leave it off locally or collect static files.

Station and place data is derived from OpenStreetMap and remains subject to the
Open Database Licence: https://www.openstreetmap.org/copyright
