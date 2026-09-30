"""Small geographic helpers shared across the app."""

import math
import re

EARTH_RADIUS_MILES = 3958.8

US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)

# Rough bounding boxes (min_lat, max_lat, min_lng, max_lng) used to reject
# coordinates that are clearly outside the USA before calling any API.
_US_BOXES = [
    (24.3, 49.5, -125.0, -66.8),  # contiguous states
    (51.0, 71.6, -180.0, -129.9),  # Alaska
    (18.8, 22.4, -160.4, -154.7),  # Hawaii
]

_ABBREVIATIONS = {"SAINT": "ST", "SAINTE": "ST", "STE": "ST", "MOUNT": "MT", "FORT": "FT"}


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def is_in_usa(lat: float, lng: float) -> bool:
    return any(a <= lat <= b and c <= lng <= d for a, b, c, d in _US_BOXES)


def normalize_city(name: str) -> str:
    """Normalize a city name so 'St. Louis' and 'Saint Louis' match."""
    cleaned = re.sub(r"[^A-Z0-9 ]", " ", name.upper())
    return " ".join(_ABBREVIATIONS.get(word, word) for word in cleaned.split())


def city_key(city: str, state: str) -> str:
    return f"{state.strip().upper()}|{normalize_city(city)}"
