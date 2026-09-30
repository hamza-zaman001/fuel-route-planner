"""
Find which stations lie along a route and at which mile marker.

The route polyline is densified (no gap over ~1 mile), projected onto a unit
sphere and put in a KD-tree. Every station near the route's bounding box is
then matched to its closest route point in one vectorized query. The whole
step takes a few milliseconds even for a coast-to-coast route.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from scipy.spatial import cKDTree

from routes.geo import EARTH_RADIUS_MILES

if TYPE_CHECKING:
    from routes.station_index import StationIndex

MAX_SEGMENT_MILES = 1.0


@dataclass(frozen=True)
class RouteStations:
    station_ids: np.ndarray  # primary keys of matched stations
    mile_markers: np.ndarray  # distance from start along the route
    offsets_miles: np.ndarray  # straight-line distance from the route
    prices: np.ndarray


def _to_xyz(lat: np.ndarray, lng: np.ndarray) -> np.ndarray:
    lat_r, lng_r = np.radians(lat), np.radians(lng)
    cos_lat = np.cos(lat_r)
    return np.column_stack((cos_lat * np.cos(lng_r), cos_lat * np.sin(lng_r), np.sin(lat_r)))


def _segment_miles(lat: np.ndarray, lng: np.ndarray) -> np.ndarray:
    lat_r, lng_r = np.radians(lat), np.radians(lng)
    dlat, dlng = np.diff(lat_r), np.diff(lng_r)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat_r[:-1]) * np.cos(lat_r[1:]) * np.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def densify(lat: np.ndarray, lng: np.ndarray, max_step: float = MAX_SEGMENT_MILES):
    """Insert points so no segment is longer than max_step. Returns lat, lng, cumulative miles."""
    seg = _segment_miles(lat, lng)
    pieces = np.maximum(1, np.ceil(seg / max_step).astype(int))
    # For each segment, fractions 0, 1/n, ..., (n-1)/n; then append the last point.
    starts = np.repeat(np.arange(len(seg)), pieces)
    offsets = np.arange(pieces.sum()) - np.repeat(np.cumsum(pieces) - pieces, pieces)
    frac = offsets / np.repeat(pieces, pieces)
    new_lat = np.append(lat[starts] + (lat[starts + 1] - lat[starts]) * frac, lat[-1])
    new_lng = np.append(lng[starts] + (lng[starts + 1] - lng[starts]) * frac, lng[-1])
    cum = np.concatenate(([0.0], np.cumsum(_segment_miles(new_lat, new_lng))))
    return new_lat, new_lng, cum


def simplify(coords: list, tolerance_deg: float = 0.0005) -> list:
    """Ramer-Douglas-Peucker simplification (≈50 m tolerance) to keep the
    JSON response small without visibly changing the drawn route."""
    pts = np.asarray(coords, dtype=float)
    if len(pts) < 3:
        return pts.tolist()
    keep = np.zeros(len(pts), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[b] - pts[a]
        rel = pts[a + 1 : b] - pts[a]
        norm = np.hypot(*seg)
        if norm == 0:
            dist = np.hypot(rel[:, 0], rel[:, 1])
        else:
            dist = np.abs(seg[0] * rel[:, 1] - seg[1] * rel[:, 0]) / norm
        i = int(np.argmax(dist))
        if dist[i] > tolerance_deg:
            mid = a + 1 + i
            keep[mid] = True
            stack.extend(((a, mid), (mid, b)))
    return np.round(pts[keep], 5).tolist()


def match_stations(
    route_lnglat: list,
    route_distance_miles: float,
    index: "StationIndex",
    radius_miles: float,
) -> RouteStations:
    coords = np.asarray(route_lnglat, dtype=float)
    lng, lat = coords[:, 0], coords[:, 1]
    d_lat, d_lng, cum = densify(lat, lng)

    # Scale polyline distance to the road distance reported by the router so
    # mile markers and the trip total agree exactly.
    if cum[-1] > 0:
        cum = cum * (route_distance_miles / cum[-1])

    # Cheap bounding-box prefilter before the KD-tree query.
    pad_lat = radius_miles / 69.0
    pad_lng = radius_miles / (69.0 * max(0.2, np.cos(np.radians(np.abs(lat).max()))))
    in_box = (
        (index.lat >= lat.min() - pad_lat)
        & (index.lat <= lat.max() + pad_lat)
        & (index.lng >= lng.min() - pad_lng)
        & (index.lng <= lng.max() + pad_lng)
    )
    candidates = np.flatnonzero(in_box)
    if candidates.size == 0:
        return RouteStations(*(np.array([]) for _ in range(4)))

    tree = cKDTree(_to_xyz(d_lat, d_lng))
    max_chord = 2 * np.sin(radius_miles / EARTH_RADIUS_MILES / 2)
    chord, nearest = tree.query(
        _to_xyz(index.lat[candidates], index.lng[candidates]),
        distance_upper_bound=max_chord,
    )
    hit = np.isfinite(chord)
    candidates, chord, nearest = candidates[hit], chord[hit], nearest[hit]
    offsets = 2 * EARTH_RADIUS_MILES * np.arcsin(np.clip(chord / 2, 0, 1))

    return RouteStations(
        station_ids=index.ids[candidates],
        mile_markers=cum[nearest],
        offsets_miles=offsets,
        prices=index.price[candidates],
    )
