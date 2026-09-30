"""
Trip planning: the one place that ties locations, routing, station matching
and the optimizer together. Views stay thin and just call `plan_trip`.
"""

import hashlib
import time
from dataclasses import replace
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.urls import reverse

from routes import routing
from routes.locations import Location, resolve_location
from routes.matching import match_stations, simplify
from routes.optimizer import Candidate, InfeasibleRouteError, plan_fuel_stops
from routes.station_index import get_station_index


class StationsNotLoadedError(Exception):
    """The station table is empty: `python manage.py load_stations` hasn't run."""


def plan_trip(start_input, finish_input) -> dict:
    started = time.perf_counter()
    index = get_station_index()
    if len(index) == 0:
        raise StationsNotLoadedError("No fuel stations loaded. Run: python manage.py load_stations")

    start, start_calls = resolve_location(start_input)
    finish, finish_calls = resolve_location(finish_input)
    route, route_cached = _get_route_cached(start, finish)
    api_calls = start_calls + finish_calls + (0 if route_cached else 1)

    range_miles, mpg = settings.VEHICLE_RANGE_MILES, settings.VEHICLE_MPG
    plan, radius, candidates = _optimize(route, index, range_miles, mpg)

    stops = [_stop_payload(i, stop, index.records, mpg) for i, stop in enumerate(plan.stops, 1)]

    return {
        "start": _location_payload(start_input, start),
        "finish": _location_payload(finish_input, finish),
        "summary": {
            "distance_miles": round(route.distance_miles, 1),
            "estimated_drive_time_hours": round(route.duration_seconds / 3600, 1),
            "number_of_fuel_stops": len(stops),
            "total_fuel_cost_usd": round(plan.total_cost, 2),
            "gallons_purchased": round(plan.gallons_purchased, 2),
            "total_gallons_used": round(plan.total_gallons_used, 2),
            "assumptions": {
                "vehicle_range_miles": range_miles,
                "miles_per_gallon": mpg,
                "starting_fuel": "full tank",
                "station_search_radius_miles": radius,
                "stop_penalty_usd": settings.STOP_PENALTY_USD,
                "stations_considered": len(candidates),
            },
        },
        "fuel_stops": stops,
        "map_url": _map_url(start_input, finish_input),
        "map": _geojson(route.coordinates, start, finish, stops),
        "meta": {
            "routing_provider": route.provider,
            "external_api_calls": api_calls,
            "route_from_cache": route_cached,
            "response_time_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    }


def _optimize(route, index, range_miles, mpg):
    """Match stations to the route and pick fuel stops. If there's a stretch
    with no station inside the normal search radius, retry with a wider one
    (this reuses the same route, so it costs no extra API calls)."""
    error = None
    for radius in settings.STATION_SEARCH_RADII_MILES:
        matched = match_stations(route.coordinates, route.distance_miles, index, radius)
        candidates = [
            Candidate(mile=float(m), price=float(p), ref=(int(sid), float(off)))
            for sid, m, off, p in zip(
                matched.station_ids,
                matched.mile_markers,
                matched.offsets_miles,
                matched.prices,
                strict=True,
            )
        ]
        try:
            plan = plan_fuel_stops(
                candidates, route.distance_miles, range_miles, mpg, stop_penalty=settings.STOP_PENALTY_USD
            )
            return plan, radius, candidates
        except InfeasibleRouteError as exc:
            error = exc
    raise error


def _get_route_cached(start: Location, finish: Location):
    raw = f"{start.lat:.5f},{start.lng:.5f}|{finish.lat:.5f},{finish.lng:.5f}"
    key = "route:" + hashlib.sha1(raw.encode()).hexdigest()
    route = cache.get(key)
    if route is not None:
        return route, True
    route = routing.get_route(start.latlng, finish.latlng)
    # Simplify once (~50 m tolerance) so the cached route is small and fast to
    # load; that precision is far finer than station matching needs.
    route = replace(route, coordinates=simplify(route.coordinates))
    cache.set(key, route)
    return route, False


def _location_payload(raw, loc: Location) -> dict:
    return {
        "input": raw,
        "resolved_as": loc.label,
        "lat": round(loc.lat, 6),
        "lng": round(loc.lng, 6),
        "resolved_by": loc.resolved_by,
    }


def _stop_payload(number, stop, records, mpg) -> dict:
    station_id, offset = stop.candidate.ref
    s = records[station_id]
    return {
        "stop_number": number,
        "mile_marker": round(stop.candidate.mile, 1),
        "station": {
            "opis_id": s["opis_id"],
            "name": s["name"],
            "address": s["address"],
            "city": s["city"],
            "state": s["state"],
            "lat": round(s["latitude"], 6),
            "lng": round(s["longitude"], 6),
        },
        "price_per_gallon": round(stop.candidate.price, 3),
        "gallons": round(stop.gallons, 2),
        "cost_usd": round(stop.cost, 2),
        "fuel_left_on_arrival_gallons": round(stop.fuel_on_arrival_miles / mpg, 2),
        "distance_from_route_miles": round(offset, 1),
    }


def _geojson(geometry, start: Location, finish: Location, stops) -> dict:
    """A GeoJSON FeatureCollection: paste it into geojson.io to see the map."""
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": geometry},
            "properties": {"kind": "route"},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [start.lng, start.lat]},
            "properties": {"kind": "start", "name": start.label},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [finish.lng, finish.lat]},
            "properties": {"kind": "finish", "name": finish.label},
        },
    ]
    for stop in stops:
        st = stop["station"]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [st["lng"], st["lat"]]},
                "properties": {
                    "kind": "fuel_stop",
                    "stop_number": stop["stop_number"],
                    "name": st["name"],
                    "price_per_gallon": stop["price_per_gallon"],
                    "gallons": stop["gallons"],
                    "cost_usd": stop["cost_usd"],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def _as_query_value(value) -> str:
    if isinstance(value, dict):
        return f"{value.get('lat')},{value.get('lng')}"
    return str(value)


def _map_url(start_input, finish_input) -> str:
    query = urlencode({"start": _as_query_value(start_input), "finish": _as_query_value(finish_input)})
    return f"{reverse('route-map')}?{query}"
