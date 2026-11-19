"""Distance matrices and route geometry.

Uses OpenRouteService when ORS_API_KEY is set (real road distances worldwide);
otherwise – or if ORS fails – falls back to great-circle distances with a road
factor so route planning still works anywhere without a key.
"""
from __future__ import annotations

import logging
from math import atan2, cos, radians, sin, sqrt

import requests

from app.core.config import get_settings

log = logging.getLogger(__name__)
ROAD_FACTOR = 1.3  # typical ratio of road distance to straight-line distance


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6_371_000
    p1, p2 = radians(lat1), radians(lat2)
    dphi, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _headers() -> dict:
    return {"Authorization": get_settings().ors_api_key, "Content-Type": "application/json"}


def _ors_matrix(coords: list[list[float]], profile: str) -> list[list[float | None]]:
    s = get_settings()
    resp = requests.post(f"{s.ors_base_url}/matrix/{profile}", headers=_headers(), timeout=30,
                         json={"locations": coords, "metrics": ["distance"]})
    resp.raise_for_status()
    data = resp.json()
    if "distances" not in data:
        raise ValueError("ORS returned no distances")
    return data["distances"]


def _ors_snap(coords: list[list[float]], profile: str) -> list[list[float]]:
    """Snap points to the nearest road (helps for mountain / rural places)."""
    s = get_settings()
    try:
        resp = requests.post(f"{s.ors_base_url}/snap/{profile}", headers=_headers(), timeout=20,
                             json={"locations": coords, "radius": 2000})
        resp.raise_for_status()
        out = []
        for original, loc in zip(coords, resp.json().get("locations", [])):
            point = (loc or {}).get("location") if isinstance(loc, dict) else loc
            out.append([float(point[0]), float(point[1])] if point else original)
        return out if len(out) == len(coords) else coords
    except Exception as exc:
        log.info("ORS snap skipped: %s", type(exc).__name__)
        return coords


def distance_matrix(points: list[dict], profile: str = "driving-car") -> tuple[list[list[float | None]], str]:
    """points: [{lat, lon}] -> (matrix in metres, mode 'road' | 'estimated')."""
    if get_settings().ors_api_key:
        try:
            coords = _ors_snap([[p["lon"], p["lat"]] for p in points], profile)
            return _ors_matrix(coords, profile), "road"
        except Exception as exc:
            log.warning("ORS matrix failed (%s); using estimated distances", type(exc).__name__)
    n = len(points)
    m = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            if i != j:
                m[i][j] = haversine_m(points[i]["lat"], points[i]["lon"], points[j]["lat"], points[j]["lon"]) * ROAD_FACTOR
    return m, "estimated"


def route_geometry(points: list[dict], profile: str = "driving-car") -> list[list[float]]:
    """Returns [[lon, lat], ...]. Straight segments if ORS is unavailable."""
    coords = [[p["lon"], p["lat"]] for p in points]
    s = get_settings()
    if s.ors_api_key and len(coords) > 1:
        try:
            resp = requests.post(f"{s.ors_base_url}/directions/{profile}/geojson", headers=_headers(),
                                 json={"coordinates": coords}, timeout=30)
            resp.raise_for_status()
            return resp.json()["features"][0]["geometry"]["coordinates"]
        except Exception as exc:
            log.warning("ORS directions failed (%s); drawing straight lines", type(exc).__name__)
    return coords
