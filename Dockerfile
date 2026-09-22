# syntax=docker/dockerfile:1

# Tailwind and the vendored browser libraries are built in a Node stage so the
# runtime image carries no JavaScript toolchain. static/dist and static/vendor
# are build output and are not in the repository.
FROM node:22-slim AS assets

WORKDIR /build

COPY package.json package-lock.json ./
RUN npm ci

# Tailwind scans the templates and the Python that emits class names - see the
# @source lines in assets/input.css - so both have to be present to build the
# stylesheet, not just the CSS entry point.
COPY assets ./assets
COPY scripts ./scripts
COPY templates ./templates
COPY apps ./apps
COPY static ./static
RUN npm run build


FROM python:3.13-slim AS runtime

# The manifest storage is on because collectstatic runs below; a checkout
# without one would fail to render on a missing manifest entry instead.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_STATIC_MANIFEST=True

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=assets /build/static/dist ./static/dist
COPY --from=assets /build/static/vendor ./static/vendor

# Hashed filenames are collected once, at build time. SECRET_KEY is only read
# because settings insists on one; nothing signed here outlives the build.
RUN DJANGO_SECRET_KEY=build-only-not-a-secret python manage.py collectstatic --noinput

# The platform supplies PORT. Migrations run at boot because the free plan has
# no separate pre-deploy step; they are idempotent and there is one writer.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 2 --threads 4 --timeout 60 --access-logfile - --error-logfile -"]
