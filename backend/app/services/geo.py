"""Worldwide place search with a provider fallback chain.

1. OpenStreetMap Nominatim (max 1 request/second, needs a User-Agent)
2. Photon by Komoot (also OpenStreetMap data, no key) if Nominatim fails or is empty

Results are cached for 24 h. If *every* provider fails we raise, so the API can
tell the user clearly instead of silently returning nothing.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time

import requests

from app.core.cache import get_cache
from app.core.config import get_settings

log = logging.getLogger(__name__)
_http = requests.Session()
_lock = threading.Lock()
_last_nominatim = [0.0]


class GeocodingUnavailable(RuntimeError):
    pass


def http_get_json(url: str, params: dict | None = None, timeout: int = 12):
    """GET JSON; returns None on any network/HTTP error (logged)."""
    try:
        r = _http.get(url, params=params, timeout=timeout, headers={"User-Agent": get_settings().http_user_agent})
        if r.status_code != 200:
            log.warning("GET %s -> HTTP %s", url, r.status_code)
            return None
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("GET %s failed: %s", url, type(exc).__name__)
        return None


def _nominatim(query: str, limit: int) -> list[dict] | None:
    with _lock:  # usage policy: at most one request per second
        wait = _last_nominatim[0] + 1.0 - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_nominatim[0] = time.time()
    data = http_get_json(get_settings().nominatim_url,
                         {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 1, "accept-language": "en"})
    if data is None:
        return None
    return [{
        "name": p.get("name") or p.get("display_name", "").split(",")[0],
        "address": p.get("display_name", ""),
        "lat": float(p["lat"]), "lon": float(p["lon"]),
        "type": p.get("type") or p.get("category") or "place",
    } for p in data]


def _photon(query: str, limit: int) -> list[dict] | None:
    data = http_get_json(get_settings().photon_url, {"q": query, "limit": limit, "lang": "en"})
    if data is None:
        return None
    out = []
    for f in data.get("features", []):
        p, (lon, lat) = f.get("properties", {}), f["geometry"]["coordinates"]
        parts = [p.get(k) for k in ("name", "city", "state", "country") if p.get(k)]
        out.append({"name": p.get("name") or parts[0], "address": ", ".join(dict.fromkeys(parts)),
                    "lat": float(lat), "lon": float(lon), "type": p.get("osm_value") or "place"})
    return out


def search_places(query: str, limit: int = 6) -> list[dict]:
    query = " ".join(query.split())
    if len(query) < 2:
        return []
    key = "geo:" + hashlib.sha1(f"{query.lower()}|{limit}".encode()).hexdigest()
    cache = get_cache()
    if (hit := cache.get(key)) is not None:
        return hit
    failures = 0
    for provider in (_nominatim, _photon):
        results = provider(query, limit)
        if results is None:
            failures += 1
            continue
        if results:
            cache.set(key, results, ttl=86400)
            return results
    if failures == 2:
        raise GeocodingUnavailable("Place search is unavailable: OpenStreetMap services did not respond.")
    return []
