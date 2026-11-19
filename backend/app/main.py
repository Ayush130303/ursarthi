import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import models  # noqa: F401  registers tables
from app.api.routes import auth, preferences, trips
from app.core.config import get_settings
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db.session import Base, SessionLocal, engine
from app.rag.embeddings import get_embedder
from app.rag.llm import get_llm
from app.rag.vector_store import get_vector_store, init_vector_store

configure_logging()
log = logging.getLogger("app")
settings = get_settings()


def _check_database() -> None:
    """Fail fast with a readable message instead of a long traceback."""
    from sqlalchemy.exc import OperationalError

    safe_url = engine.url.render_as_string(hide_password=True)
    log.info("Database: %s", safe_url)
    if "YOUR_PASSWORD" in settings.database_url:
        raise SystemExit(f"\n[urSarthi] backend/.env still has the placeholder password: {safe_url}\n"
                         "Replace YOUR_PASSWORD with your real PostgreSQL password and restart.\n")
    if settings.is_sqlite:
        db_file = engine.url.database
        if db_file and db_file != ":memory:":
            folder = Path(db_file).resolve().parent
            try:
                folder.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            if not os.access(folder, os.W_OK):
                raise SystemExit(
                    f"\n[urSarthi] Using SQLite at {Path(db_file).resolve()} but the folder is not writable.\n"
                    "  -> No DATABASE_URL was given, so the app fell back to SQLite.\n"
                    "  -> Set DATABASE_URL to your PostgreSQL database (e.g. in docker-compose.yml or your host's settings),\n"
                    "     or point it to a writable file such as sqlite:////srv/data/ursarthi.db\n")
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        reason = str(exc.orig).strip().splitlines()[0] if exc.orig else str(exc)
        hint = ("The password in DATABASE_URL is wrong for this user."
                if "password authentication failed" in reason else
                "Create the database first (e.g. in pgAdmin or: CREATE DATABASE hamrosikkim;)."
                if "does not exist" in reason else
                "SQLite could not open its file: set DATABASE_URL to PostgreSQL or a writable path."
                if settings.is_sqlite else
                "Check that PostgreSQL is running and the host/port in DATABASE_URL are right.")
        raise SystemExit(f"\n[urSarthi] Cannot connect to the database {safe_url}\n  -> {reason}\n  -> {hint}\n")


def _bootstrap() -> None:
    """Create tables and set up the vector store.

    With several workers, each process runs this at the same moment. On PostgreSQL
    we take an advisory lock so only one process creates tables/extensions at a time
    (otherwise CREATE TABLE / CREATE EXTENSION can collide)."""
    if settings.is_postgres:
        with engine.connect() as lock_conn:
            lock_conn.execute(text("SELECT pg_advisory_lock(727274)"))
            try:
                Base.metadata.create_all(bind=engine)
                with SessionLocal() as db:
                    init_vector_store(db).backfill(db)
            finally:
                lock_conn.execute(text("SELECT pg_advisory_unlock(727274)"))
                lock_conn.commit()
    else:
        Base.metadata.create_all(bind=engine)
        with SessionLocal() as db:
            init_vector_store(db).backfill(db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _check_database()
    _bootstrap()  # creates only missing tables, never drops data
    log.info("urSarthi ready | db=%s | vectors=%s | embeddings=%s | llm=%s",
             engine.url.get_backend_name(), get_vector_store().name, get_embedder().name,
             get_llm().model if get_llm().available else "offline (extractive answers)")
    yield


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan,
              description="Route planning + RAG travel suggestions + preference memory.")
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
for r in (auth.router, preferences.router, trips.router):
    app.include_router(r, prefix=settings.api_prefix)


@app.get(f"{settings.api_prefix}/health", tags=["Health"])
def health():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    llm = get_llm()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok, "vector_store": get_vector_store().name,
            "embeddings": get_embedder().name, "llm": llm.model if llm.available else None}


# ---------------------------------------------------------------------------
# Production: serve the built React app from this same server (one domain,
# so the login cookie always works). Enabled when FRONTEND_DIST is set.
# ---------------------------------------------------------------------------
_dist = Path(settings.frontend_dist).resolve() if settings.frontend_dist else None
if _dist and (_dist / "index.html").exists():
    if not settings.api_prefix:
        raise RuntimeError("Set API_PREFIX=/api when FRONTEND_DIST is used, so pages and API paths don't clash.")
    app.mount("/static", StaticFiles(directory=_dist / "static"), name="static")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        file = (_dist / path).resolve()
        if path and file.is_file() and _dist in file.parents:
            return FileResponse(file)          # favicon, manifest...
        return FileResponse(_dist / "index.html")  # React Router handles the page
else:
    @app.get("/", tags=["Health"])
    def root():
        return {"app": "urSarthi", "docs": "/docs"}