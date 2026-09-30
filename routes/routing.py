"""
Clients for the free external APIs.

* Routing: OpenRouteService when ORS_API_KEY is set, otherwise the public
  OSRM demo server (no key needed). Either way it's ONE request per trip.
* Geocoding free-text addresses: ORS geocoder with a key, else Nominatim.
  This is only used when the input isn't coordinates or "City, ST", which
  are resolved locally without any API call.
"""

from dataclasses import dataclass

import requests
from django.conf import settings

METERS_PER_MILE = 1609.344
# api.openrouteservice.org was shut down in Sep 2026; api.heigit.org replaces it.
ORS_BASE_URL = "https://api.heigit.org"
USER_AGENT = "fuel-route-planner/1.0 (Django assessment project)"

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


class RoutingError(Exception):
    """The routing/geocoding provider failed or was unreachable."""


class RouteNotFoundError(Exception):
    """The provider answered, but no drivable route/location was found."""


@dataclass(frozen=True)
class Route:
    coordinates: list  # [[lng, lat], ...]
    distance_miles: float
    duration_seconds: float
    provider: str


def get_route(start: tuple, finish: tuple) -> Route:
    """start/finish are (lat, lng) tuples."""
    if settings.ORS_API_KEY:
        return _ors_route(start, finish)
    return _osrm_route(start, finish)


def geocode(text: str) -> tuple:
    """Return (lat, lng, label) for a free-text US address."""
    if settings.ORS_API_KEY:
        return _ors_geocode(text)
    return _nominatim_geocode(text)


def _request(method: str, url: str, **kwargs) -> requests.Response:
    try:
        return _session.request(method, url, timeout=settings.ROUTING_TIMEOUT_SECONDS, **kwargs)
    except requests.RequestException as exc:
        raise RoutingError(f"Could not reach the routing provider: {exc}") from exc


# --- OpenRouteService ---------------------------------------------------------


def _ors_route(start, finish) -> Route:
    resp = _request(
        "POST",
        f"{ORS_BASE_URL}/openrouteservice/v2/directions/driving-car/geojson",
        headers={"Authorization": settings.ORS_API_KEY, "Accept": "application/geo+json"},
        json={
            "coordinates": [[start[1], start[0]], [finish[1], finish[0]]],
            "instructions": False,
            "radiuses": [5000, 5000],  # snap to a road within 5 km (default is only 350 m)
        },
    )
    if resp.status_code != 200:
        message = _ors_error_message(resp)
        if resp.status_code in (400, 404):
            raise RouteNotFoundError(message)
        raise RoutingError(message)
    feature = resp.json()["features"][0]
    summary = feature["properties"].get("summary", {})
    return Route(
        coordinates=feature["geometry"]["coordinates"],
        distance_miles=summary.get("distance", 0.0) / METERS_PER_MILE,
        duration_seconds=summary.get("duration", 0.0),
        provider="openrouteservice",
    )


def _ors_geocode(text):
    resp = _request(
        "GET",
        f"{ORS_BASE_URL}/pelias/v1/search",
        params={"api_key": settings.ORS_API_KEY, "text": text, "boundary.country": "US", "size": 1},
    )
    if resp.status_code != 200:
        raise RoutingError(_ors_error_message(resp))
    features = resp.json().get("features") or []
    if not features:
        raise RouteNotFoundError(f"Could not find a US location matching '{text}'.")
    lng, lat = features[0]["geometry"]["coordinates"]
    return lat, lng, features[0]["properties"].get("label", text)


def _ors_error_message(resp) -> str:
    try:
        error = resp.json().get("error")
        message = error.get("message") if isinstance(error, dict) else error
    except ValueError:
        message = None
    return f"OpenRouteService error ({resp.status_code}): {message or resp.text[:200]}"


# --- OSRM + Nominatim (keyless fallback) -----------------------------------------


def _osrm_route(start, finish) -> Route:
    coords = f"{start[1]},{start[0]};{finish[1]},{finish[0]}"
    resp = _request(
        "GET",
        f"https://router.project-osrm.org/route/v1/driving/{coords}",
        params={"overview": "full", "geometries": "geojson", "steps": "false"},
    )
    try:
        data = resp.json()
    except ValueError as exc:
        raise RoutingError(f"OSRM returned an invalid response ({resp.status_code}).") from exc
    if data.get("code") != "Ok" or not data.get("routes"):
        if data.get("code") in ("NoRoute", "NoSegment"):
            raise RouteNotFoundError(data.get("message") or "No drivable route found.")
        raise RoutingError(f"OSRM error: {data.get('message') or data.get('code')}")
    route = data["routes"][0]
    return Route(
        coordinates=route["geometry"]["coordinates"],
        distance_miles=route["distance"] / METERS_PER_MILE,
        duration_seconds=route["duration"],
        provider="osrm",
    )


def _nominatim_geocode(text):
    resp = _request(
        "GET",
        "https://nominatim.openstreetmap.org/search",
        params={"q": text, "format": "jsonv2", "countrycodes": "us", "limit": 1},
    )
    if resp.status_code != 200:
        raise RoutingError(f"Nominatim error ({resp.status_code}).")
    results = resp.json()
    if not results:
        raise RouteNotFoundError(f"Could not find a US location matching '{text}'.")
    return float(results[0]["lat"]), float(results[0]["lon"]), results[0].get("display_name", text)