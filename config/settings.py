"""
Django settings for OneApp - a personal budgeting and spend-analysis console.

The first module is Fuel: a live station map built from OpenStreetMap, with a
tiered price layer (own logged fill-ups > DOE weekly advisory > estimate).
Grocery, utilities and other spend modules follow the same shape.

Secrets come from the environment (see .env.example) and are never committed.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    DJANGO_CSRF_TRUSTED_ORIGINS=(list, []),
    DJANGO_SECURE_SSL_REDIRECT=(bool, False),
    OVERPASS_TIMEOUT=(int, 180),
    MAP_TILE_MAX_ZOOM=(int, 19),
)

environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-only-insecure-key-change-me")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("DJANGO_CSRF_TRUSTED_ORIGINS")

# Render names the service's host at runtime. Reading it here means a first
# deploy, or a rename, does not arrive as a DisallowedHost page.
_platform_host = env("RENDER_EXTERNAL_HOSTNAME", default="")
if _platform_host:
    ALLOWED_HOSTS = [*ALLOWED_HOSTS, _platform_host]
    CSRF_TRUSTED_ORIGINS = [*CSRF_TRUSTED_ORIGINS, f"https://{_platform_host}"]

# --------------------------------------------------------------------------
# Applications
# --------------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.humanize",
    # Must precede staticfiles: it turns off runserver's own static handler so
    # WhiteNoise serves assets in development exactly as it will in production.
    "whitenoise.runserver_nostatic",
    "django.contrib.staticfiles",
    "widget_tweaks",
    "apps.core",
    "apps.places",
    "apps.fuel",
    "apps.grocery",
    "apps.spend",
    "apps.insights",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.core.context_processors.navigation",
            ],
        },
    },
]

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
# SQLite by default: this is a single-user personal app and the whole dataset
# is a few thousand stations plus a fill-up history. Point DATABASE_URL at
# Postgres if it ever outgrows that.

_SQLITE_URL = f"sqlite:///{BASE_DIR / 'db.sqlite3'}"

# A DATABASE_URL left blank in .env is "I have not set this", not "use no
# database". django-environ would otherwise parse the empty string into the
# dummy backend, and every query then fails with a confusing ENGINE error.
DATABASES = {"default": env.db_url_config(env("DATABASE_URL", default="") or _SQLITE_URL)}

# A managed Postgres sits across a network hop and the free tier suspends
# between refreshes, so every cold request would otherwise pay for a fresh
# connection and TLS handshake. Holding one open for ten minutes removes that;
# the health check discards a connection the server closed while we were idle.
if DATABASES["default"]["ENGINE"] != "django.db.backends.sqlite3":
    DATABASES["default"]["CONN_MAX_AGE"] = env.int("DJANGO_CONN_MAX_AGE", default=600)
    DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "core:home"
LOGOUT_REDIRECT_URL = "login"

# --------------------------------------------------------------------------
# Internationalisation
# --------------------------------------------------------------------------

LANGUAGE_CODE = "en-ph"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True

# --------------------------------------------------------------------------
# Static files
# --------------------------------------------------------------------------
# assets/ sits outside static/ on purpose: collectstatic would otherwise try to
# resolve Tailwind's `@import "tailwindcss"` as a real asset URL and fail.

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Hashed filenames let static assets be cached forever, but the manifest only
# exists after collectstatic - so a template rendered without one (a test run,
# a fresh checkout) dies on a missing manifest entry rather than on anything
# real. Opt in from the environment where collectstatic is part of the deploy.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": (
            "whitenoise.storage.CompressedManifestStaticFilesStorage"
            if env.bool("DJANGO_STATIC_MANIFEST", default=False)
            else "whitenoise.storage.CompressedStaticFilesStorage"
        ),
    },
}

# --------------------------------------------------------------------------
# Messages
# --------------------------------------------------------------------------

from django.contrib.messages import constants as message_constants  # noqa: E402

MESSAGE_TAGS = {message_constants.ERROR: "error"}

# --------------------------------------------------------------------------
# Security
# --------------------------------------------------------------------------
# Kept mild for local use; the toggles that matter behind a real domain are
# driven from the environment so a deployment can turn them on without a code
# change.

SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
SECURE_SSL_REDIRECT = env("DJANGO_SECURE_SSL_REDIRECT")

# Behind a TLS-terminating proxy the app only learns the original scheme from
# a header, and without this the redirect above loops forever. Off by default:
# trusting the header when nothing sets it lets a client claim HTTPS.
if env.bool("DJANGO_TRUST_PROXY_SSL_HEADER", default=False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"

if not DEBUG:
    SESSION_COOKIE_SECURE = env.bool("DJANGO_SESSION_COOKIE_SECURE", default=True)
    CSRF_COOKIE_SECURE = env.bool("DJANGO_CSRF_COOKIE_SECURE", default=True)
    SECURE_HSTS_SECONDS = env.int("DJANGO_SECURE_HSTS_SECONDS", default=31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

# --------------------------------------------------------------------------
# Fuel module
# --------------------------------------------------------------------------

# Public Overpass endpoint used by the station importer. The main instance
# rejects heavy queries when busy, so the importer walks the country in tiles
# and retries against the mirrors below in order.
OVERPASS_ENDPOINTS = env.list(
    "OVERPASS_ENDPOINTS",
    default=[
        "https://overpass-api.de/api/interpreter",
        "https://overpass.kumi.systems/api/interpreter",
    ],
)
OVERPASS_TIMEOUT = env("OVERPASS_TIMEOUT")

# A logged pump price older than this stops being treated as current and the
# station falls back to the DOE advisory baseline.
PRICE_FRESH_DAYS = env.int("PRICE_FRESH_DAYS", default=14)

# Cap on how many stations one map viewport request may return. The browser
# never receives the whole table; it asks for the box it is looking at.
MAP_MAX_STATIONS = env.int("MAP_MAX_STATIONS", default=300)

# OSM Foundation's public tile server may return 403s when a network or app is
# blocked by policy enforcement, so the browser map uses an OSM-derived hosted
# basemap that can be swapped from the environment.
MAP_TILE_URL = env(
    "MAP_TILE_URL",
    default="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
)
MAP_TILE_ATTRIBUTION = env(
    "MAP_TILE_ATTRIBUTION",
    default=(
        "&copy; OpenStreetMap contributors &copy; CARTO"
    ),
)
MAP_TILE_MAX_ZOOM = env("MAP_TILE_MAX_ZOOM")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}
