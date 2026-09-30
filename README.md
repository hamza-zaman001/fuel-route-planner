# Fuel Route Planner API

A Django API that plans a road trip between two places in the USA, returns the route as a map, and picks where to refuel so the trip costs as little as possible.

The vehicle has a 500 mile range and gets 10 miles per gallon. Fuel prices come from the provided truck stop price list.

## Quick start

Requires Python 3.12+.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS / Linux

pip install -r requirements.txt
copy .env.example .env            # Windows  (cp on macOS / Linux)
python manage.py migrate
python manage.py load_stations    # one-time import of the fuel price list (~3 s)
python manage.py runserver
```

Add a free [OpenRouteService](https://openrouteservice.org/dev/#/signup) key to `.env` as `ORS_API_KEY`. Without a key the app falls back to the public OSRM demo server, so it still runs out of the box.

Run the tests with `python manage.py test`.

## Using the API

**`POST /api/route/`**

```json
{ "start": "New York, NY", "finish": "Los Angeles, CA" }
```

`start` and `finish` accept any of:

| Input | Example | External API calls |
|---|---|---|
| City and state | `"Chicago, IL"`, `"Chicago, Illinois"` | 0 (looked up locally) |
| Coordinates | `"41.88,-87.63"` or `{"lat": 41.88, "lng": -87.63}` | 0 |
| Street address | `"1600 Pennsylvania Ave NW, Washington, DC"` | 1 geocoding call |

`GET /api/route/?start=...&finish=...` works too.

**Response** for New York to Los Angeles (trimmed; this is real output):

```json
{
  "start":  { "input": "New York, NY", "resolved_as": "New York, NY", "resolved_by": "city_lookup" },
  "finish": { "input": "Los Angeles, CA", "resolved_as": "Los Angeles, CA", "resolved_by": "city_lookup" },
  "summary": {
    "distance_miles": 2795.8,
    "estimated_drive_time_hours": 45.0,
    "number_of_fuel_stops": 9,
    "total_fuel_cost_usd": 695.12,
    "gallons_purchased": 229.58,
    "total_gallons_used": 279.58,
    "assumptions": {
      "vehicle_range_miles": 500.0,
      "miles_per_gallon": 10.0,
      "starting_fuel": "full tank",
      "station_search_radius_miles": 10.0,
      "stop_penalty_usd": 10.0,
      "stations_considered": 451
    }
  },
  "fuel_stops": [
    {
      "stop_number": 4,
      "mile_marker": 1334.0,
      "station": { "opis_id": 68368, "name": "AKAL TRAVEL CENTER", "address": "I-80 EX 360", "city": "Waco", "state": "NE" },
      "price_per_gallon": 2.799,
      "gallons": 50.0,
      "cost_usd": 139.95,
      "fuel_left_on_arrival_gallons": 0.0,
      "distance_from_route_miles": 6.8
    }
  ],
  "map_url": "http://127.0.0.1:8000/api/route/map/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "map": { "type": "FeatureCollection", "features": ["route line, start, finish and one point per fuel stop"] },
  "meta": { "routing_provider": "openrouteservice", "external_api_calls": 1, "route_from_cache": false }
}
```

The plan fills the tank completely at the cheapest stations on the route (Waco, NE at $2.799 and Utica, IL at $2.969) and only tops up in pricier states further west.

The map comes back two ways:

- **`map`**: GeoJSON of the route, start, finish and fuel stops. Paste it into [geojson.io](https://geojson.io) to see it.
- **`map_url`**: an interactive Leaflet map with the stops listed beside it. It reuses the cached route, so opening it makes no new API calls.

Errors return a JSON `error` message: `400` for bad or non-US input, `422` when no drivable route or no feasible fuel plan exists, `502` when the routing provider fails.

## How it works

1. **Stations are geocoded once, offline.** The price list only has city and state, so `load_stations` places every station at its city using a free US cities dataset (MIT licensed, in `/data`). 100% of US stations match. Canadian stations are skipped, and stations listed several times keep their lowest price.
2. **One routing call per trip.** Start and finish are resolved locally when possible, then OpenRouteService returns the driving route. The route is cached, so repeating a trip or opening its map costs zero calls.
3. **Stations are matched to the route.** The route is densified to one point per mile and put in a KD-tree. Each station within 10 miles of the route gets a mile marker. If some stretch has no station in reach, the search widens to 25 miles on the same route.
4. **The optimizer picks the stops.** This is the classic "gas station problem". Some optimal plan only ever fills the tank or buys just enough to reach the next stop (Khuller, Malekian and Mestre, 2007). A dynamic program over those choices finds the cheapest plan exactly.

Pure cost-minimizing will happily stop for 1 gallon to save 3 cents, so each stop carries a $10 penalty for the time it takes. The optimizer minimizes fuel cost plus that penalty, and the response reports only the real fuel bill. Set `STOP_PENALTY_USD=0` for the strictly cheapest plan.

The optimizer is tested against a brute-force search on 600 random trips and matches it every time.

## Performance

- A trip with a new route takes the routing API's response time (1–3 s for a cross-country route) plus under 200 ms of our own work (simplifying the route, matching stations, optimizing).
- A repeated trip returns in about 15 ms with 0 external calls.
- Stations are loaded into memory once per process, so requests never scan the database.

## Assumptions

- The vehicle starts with a full tank. "Total fuel cost" is what's spent at stops along the way, and the driver buys only what's needed to reach the destination.
- Stations sit at their city's location, since the price list has no coordinates. That's why stations within 10 miles of the route count as on the way.
- Detour distance to a station isn't added to the fuel bill.
- Routing uses a regular car profile (`driving-car`).

## Project layout

```
config/                 Django settings and root URLs
routes/
  services.py           plan_trip(): ties everything together
  locations.py          turns input into coordinates (local lookup first)
  routing.py            OpenRouteService / OSRM / geocoding clients
  matching.py           finds stations along the route (KD-tree)
  optimizer.py          chooses fuel stops (dynamic programming)
  station_index.py      in-memory station arrays
  views.py              API endpoint and map page
  management/commands/load_stations.py
  tests/                optimizer, location parsing and API tests
data/                   fuel prices and US cities datasets
postman_collection.json ready-made requests for Postman
```
