"""
Turn user input into coordinates, avoiding API calls whenever possible.

Accepted forms, in order of preference:
  1. {"lat": 40.71, "lng": -74.00} or "40.71,-74.00"   -> no API call
  2. "Chicago, IL" / "Chicago, Illinois" / "Chicago, IL, USA" -> local lookup, no API call
  3. Any other text, e.g. "1600 Pennsylvania Ave, Washington DC" -> one geocoding call
"""

import hashlib
import re
from dataclasses import dataclass

from django.core.cache import cache

from routes import routing
from routes.geo import US_STATES, city_key, is_in_usa
from routes.models import City

STATE_NAMES = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
}

CANADIAN_PROVINCES = frozenset("AB BC MB NB NL NS NT NU ON PE QC SK YT".split())

_COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_COUNTRY_SUFFIXES = {"USA", "US", "U S A", "U S", "UNITED STATES", "UNITED STATES OF AMERICA"}


class LocationError(Exception):
    """The input could not be turned into a location inside the USA."""


@dataclass(frozen=True)
class Location:
    lat: float
    lng: float
    label: str
    resolved_by: str  # "coordinates" | "city_lookup" | "geocoder"

    @property
    def latlng(self):
        return (self.lat, self.lng)


def resolve_location(value) -> tuple:
    """Return (Location, number_of_external_api_calls_made)."""
    if isinstance(value, dict):
        return _from_coordinates(value.get("lat"), value.get("lng")), 0

    text = str(value).strip()
    if not text:
        raise LocationError("Location is empty.")

    match = _COORD_RE.match(text)
    if match:
        return _from_coordinates(match.group(1), match.group(2)), 0

    city = _lookup_city(text)
    if city:
        return Location(city.latitude, city.longitude, f"{city.name}, {city.state}", "city_lookup"), 0

    return _geocode(text)


def _from_coordinates(lat, lng) -> Location:
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError) as exc:
        raise LocationError("Coordinates must be numbers: {'lat': ..., 'lng': ...}.") from exc
    if not is_in_usa(lat, lng):
        raise LocationError(f"({lat}, {lng}) is not inside the USA.")
    return Location(lat, lng, f"{lat:.5f}, {lng:.5f}", "coordinates")


def _lookup_city(text: str):
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if parts and " ".join(re.sub(r"[^A-Z]", " ", parts[-1].upper()).split()) in _COUNTRY_SUFFIXES:
        parts = parts[:-1]
    if len(parts) != 2:
        return None
    city, state = parts
    state = re.sub(r"\s+\d{5}(-\d{4})?$", "", state).strip().upper()  # drop a ZIP code
    state = STATE_NAMES.get(state, state)
    if state in CANADIAN_PROVINCES:
        raise LocationError(f"'{text}' is in Canada; start and finish must be inside the USA.")
    if state not in US_STATES:
        return None
    keys = [city_key(city, state)]
    if keys[0].endswith(" CITY"):  # "New York City, NY" -> "New York, NY"
        keys.append(keys[0].removesuffix(" CITY"))
    return City.objects.filter(lookup_key__in=keys).order_by("-lookup_key").first()


def _geocode(text: str) -> tuple:
    cache_key = "geocode:" + hashlib.sha1(text.lower().encode()).hexdigest()
    cached = cache.get(cache_key)
    if cached:
        return Location(*cached, "geocoder"), 0
    try:
        lat, lng, label = routing.geocode(text)
    except routing.RouteNotFoundError as exc:
        raise LocationError(str(exc)) from exc
    if not is_in_usa(lat, lng):
        raise LocationError(f"'{text}' resolved to a place outside the USA.")
    cache.set(cache_key, (lat, lng, label))
    return Location(lat, lng, label, "geocoder"), 1
