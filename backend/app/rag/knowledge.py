"""Builds the knowledge base for the places on a route.

For every stop:
  * Wikipedia *geosearch* finds articles physically near the stop (temples,
    lakes, museums…). Their intros become knowledge, and because they carry
    coordinates they double as "nearby place" recommendations.
  * The stop's own Wikipedia article and its Wikivoyage travel guide
    (See / Do / Eat / Stay safe…) add depth.

Network calls run in parallel (thread pool); database writes happen on the
caller's session. Results are cached per ~1 km grid cell for 30 days, so a
second trip through the same town costs zero HTTP calls.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Chunk, PlaceIndex, Source
from app.rag.chunking import category_for, chunk_sections, split_wiki_sections
from app.rag.embeddings import get_embedder
from app.rag.vector_store import get_vector_store
from app.services.geo import http_get_json

log = logging.getLogger(__name__)
WIKI_API = "https://en.wikipedia.org/w/api.php"
VOYAGE_API = "https://en.wikivoyage.org/w/api.php"
VOYAGE_SECTIONS = {"overview", "understand", "see", "do", "eat", "drink", "buy", "get around", "stay safe", "go next", "sleep"}
SKIP_TITLE_WORDS = ("list of", "railway station", "constituency", "district court", "high school", "airport")


def cell_key(lat: float, lon: float) -> str:
    return f"{lat:.2f},{lon:.2f}"


def _wiki_url(title: str, host: str = "en.wikipedia.org") -> str:
    return f"https://{host}/wiki/{quote(title.replace(' ', '_'))}"


# ------------------------------------------------------------ network only --

def _geosearch(lat: float, lon: float, limit: int = 10) -> list[dict]:
    data = http_get_json(WIKI_API, {"action": "query", "list": "geosearch", "gscoord": f"{lat}|{lon}",
                                    "gsradius": get_settings().knowledge_radius_m, "gslimit": limit, "format": "json"})
    pages = (data or {}).get("query", {}).get("geosearch", [])
    return [p for p in pages if not any(w in p["title"].lower() for w in SKIP_TITLE_WORDS)]


def _search_title(api: str, name: str) -> str | None:
    data = http_get_json(api, {"action": "query", "list": "search", "srsearch": name, "srlimit": 1, "format": "json"})
    hits = (data or {}).get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def _intro_extracts(titles: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(titles), 20):  # API limit for intro extracts
        data = http_get_json(WIKI_API, {"action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                                        "exlimit": 20, "redirects": 1, "titles": "|".join(titles[i:i + 20]), "format": "json"})
        for page in (data or {}).get("query", {}).get("pages", {}).values():
            if page.get("extract"):
                out[page["title"]] = page["extract"]
    return out


def _wikivoyage(name: str) -> tuple[str, str] | None:
    for title in (name, _search_title(VOYAGE_API, name)):
        if not title:
            continue
        data = http_get_json(VOYAGE_API, {"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
                                          "redirects": 1, "titles": title, "format": "json"})
        for page in (data or {}).get("query", {}).get("pages", {}).values():
            if "missing" not in page and len(page.get("extract", "")) > 300:
                return page["title"], page["extract"][:40_000]
    return None


def fetch_stop(stop: dict) -> dict:
    """All HTTP for one stop. Pure function: no database access."""
    nearby = _geosearch(stop["lat"], stop["lon"])
    main_title = _search_title(WIKI_API, stop["name"].split(",")[0])
    titles = list(dict.fromkeys([p["title"] for p in nearby] + ([main_title] if main_title else [])))
    extracts = _intro_extracts(titles) if titles else {}
    pages = []
    for p in nearby:
        if p["title"] in extracts:
            pages.append({"title": p["title"], "lat": p["lat"], "lon": p["lon"], "text": extracts[p["title"]], "main": False})
    if main_title and main_title in extracts and main_title not in {p["title"] for p in pages}:
        pages.append({"title": main_title, "lat": stop["lat"], "lon": stop["lon"], "text": extracts[main_title], "main": True})
    return {"stop": stop, "pages": pages, "voyage": _wikivoyage(stop["name"].split(",")[0])}


# --------------------------------------------------------------- database --

def _add_source(db: Session, key: str, kind: str, title: str, url: str, lat, lon,
                sections: list[tuple[str, str]], category: str | None = None) -> int:
    existing = db.scalar(select(Source).where(Source.key == key))
    if existing:
        return existing.id
    src = Source(key=key, kind=kind, title=title[:300], url=url, lat=lat, lon=lon)
    db.add(src)
    db.flush()
    pieces = chunk_sections(sections)
    rows = [Chunk(source_id=src.id, section=p.section, category=category or p.category, content=p.content) for p in pieces]
    db.add_all(rows)
    db.flush()
    if rows:
        vecs = get_embedder().embed_documents([f"{title} – {r.section}: {r.content}" for r in rows])
        get_vector_store().upsert(db, [r.id for r in rows], vecs)
    return src.id


def store_stop_knowledge(db: Session, fetched: dict) -> list[int]:
    ids = []
    for page in fetched["pages"]:
        ids.append(_add_source(
            db, f"wikipedia:{page['title']}", "wikipedia", page["title"], _wiki_url(page["title"]),
            page["lat"], page["lon"], [("About", page["text"])],
            category="overview" if page["main"] else "sightseeing",
        ))
    if fetched["voyage"]:
        title, article = fetched["voyage"]
        sections = [(s, b) for s, b in split_wiki_sections(article) if s.strip().lower() in VOYAGE_SECTIONS]
        ids.append(_add_source(db, f"wikivoyage:{title}", "wikivoyage", f"{title} travel guide",
                               _wiki_url(title, "en.wikivoyage.org"), None, None, sections))
    return ids


def ensure_knowledge(db: Session, stops: list[dict]) -> list[int]:
    """Return knowledge source ids covering every stop, fetching what's missing."""
    fresh_after = datetime.now(timezone.utc) - timedelta(days=get_settings().knowledge_cache_days)
    source_ids: list[int] = []
    todo: list[dict] = []
    for stop in stops:
        row = db.get(PlaceIndex, cell_key(stop["lat"], stop["lon"]))
        fetched_at = row.fetched_at.replace(tzinfo=row.fetched_at.tzinfo or timezone.utc) if row else None
        if row and fetched_at > fresh_after and row.source_ids:
            source_ids += row.source_ids
        else:
            todo.append(stop)
    if todo:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(fetch_stop, todo))
        for fetched in results:
            ids = store_stop_knowledge(db, fetched)
            cell = cell_key(fetched["stop"]["lat"], fetched["stop"]["lon"])
            row = db.get(PlaceIndex, cell) or PlaceIndex(cell=cell)
            row.source_ids, row.fetched_at = ids, datetime.now(timezone.utc)
            db.add(row)
            source_ids += ids
            log.info("Knowledge for %s: %d sources", fetched["stop"]["name"], len(ids))
        db.commit()
    return list(dict.fromkeys(source_ids))


def add_history(db: Session, user_id: int, trip_id: int, trip_title: str, question: str, answer: str) -> None:
    """Store a Q&A pair as private knowledge so future answers can recall it."""
    key = f"history:{user_id}:{trip_id}"
    src = db.scalar(select(Source).where(Source.key == key))
    if not src:
        src = Source(key=key, kind="history", owner_id=user_id, title=f"Your earlier question ({trip_title})"[:300])
        db.add(src)
        db.flush()
    chunk = Chunk(source_id=src.id, owner_id=user_id, section="Earlier question", category="history",
                  content=f"Q: {question}\nA: {answer[:700]}")
    db.add(chunk)
    db.flush()
    get_vector_store().upsert(db, [chunk.id], get_embedder().embed_documents([chunk.content]))


__all__ = ["ensure_knowledge", "add_history", "fetch_stop", "cell_key", "category_for"]
