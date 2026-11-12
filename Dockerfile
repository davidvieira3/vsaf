FROM python:3.12-slim

# Miroirs internes (Artifactory...) : passez-les en build-arg si Internet n'est pas joignable.
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=
ARG VENDOR_BASE=https://cdn.jsdelivr.net/npm
ARG TABULATOR_VERSION=6.3.1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL} PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --create-home --uid 1000 app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=app:app . .

# Bibliothèque de grille servie en local (aucun appel CDN depuis le navigateur des utilisateurs).
RUN [ -s static/vendor/tabulator.min.js ] || ( \
      curl -fsSL "${VENDOR_BASE}/tabulator-tables@${TABULATOR_VERSION}/dist/js/tabulator.min.js" -o static/vendor/tabulator.min.js \
   && curl -fsSL "${VENDOR_BASE}/tabulator-tables@${TABULATOR_VERSION}/dist/css/tabulator.min.css" -o static/vendor/tabulator.min.css ) \
 && DJANGO_SECRET_KEY=build python manage.py collectstatic --noinput \
 && mkdir -p /data/history && chown -R app:app /app /data

USER app
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["web"]
