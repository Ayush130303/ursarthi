# One container = website + API on the same domain (used for deployment, e.g. Render).
# Local development does NOT need this file.

# ---- 1. build the React app ----
FROM node:20-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/public ./public
COPY frontend/src ./src
# The website calls the API on the same server under /api
ENV REACT_APP_API_URL=/api
RUN npm run build

# ---- 2. Python API that also serves the website ----
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    API_PREFIX=/api FRONTEND_DIST=/srv/web COOKIE_SECURE=true ENVIRONMENT=production
WORKDIR /srv
COPY backend/requirements.txt .
RUN pip install -r requirements.txt
COPY backend/app ./app
COPY --from=web /web/build ./web
# Writable folder for the SQLite fallback (used only if DATABASE_URL is not set)
RUN useradd --create-home appuser && mkdir -p /srv/data && chown appuser /srv/data
ENV DATABASE_URL=sqlite:////srv/data/ursarthi.db
USER appuser
# Render/Railway/Fly give the port in $PORT
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*' --workers ${WEB_CONCURRENCY:-1}"]