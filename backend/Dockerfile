FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app

# Writable folder for the SQLite fallback (used only if DATABASE_URL is not set)
RUN useradd --create-home appuser && mkdir -p /srv/data && chown appuser /srv/data
ENV DATABASE_URL=sqlite:////srv/data/ursarthi.db
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8000/health')"
# Scale with WEB_CONCURRENCY (uvicorn workers) or more container replicas.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --workers ${WEB_CONCURRENCY:-1}"]
