import io
from itertools import pairwise
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from routes import routing
from routes.geo import city_key, haversine_miles
from routes.models import City
from routes.station_index import clear_station_index

# A rough New York -> Los Angeles interstate path, used in place of a live
# routing API so tests are fast and run offline.
WAYPOINTS = [
    ("New York", "NY"),
    ("Harrisburg", "PA"),
    ("Pittsburgh", "PA"),
    ("Columbus", "OH"),
    ("Indianapolis", "IN"),
    ("St. Louis", "MO"),
    ("Tulsa", "OK"),
    ("Oklahoma City", "OK"),
    ("Amarillo", "TX"),
    ("Albuquerque", "NM"),
    ("Flagstaff", "AZ"),
    ("Barstow", "CA"),
    ("Los Angeles", "CA"),
]


def fake_route(start, finish):
    """Build a polyline through WAYPOINTS, clipped to the requested endpoints."""
    points = []
    for name, state in WAYPOINTS:
        c = City.objects.get(lookup_key=city_key(name, state))
        points.append((c.latitude, c.longitude))
    start_i = min(range(len(points)), key=lambda i: haversine_miles(*start, *points[i]))
    end_i = min(range(len(points)), key=lambda i: haversine_miles(*finish, *points[i]))
    path = [start] + points[start_i + 1 : end_i] + [finish]
    coords, miles = [], 0.0
    for (a_lat, a_lng), (b_lat, b_lng) in pairwise(path):
        for t in range(20):
            coords.append([a_lng + (b_lng - a_lng) * t / 20, a_lat + (b_lat - a_lat) * t / 20])
        miles += haversine_miles(a_lat, a_lng, b_lat, b_lng) * 1.15  # roads aren't straight
    coords.append([path[-1][1], path[-1][0]])
    return routing.Route(coords, miles, miles / 60 * 3600, "fake")


@override_settings(ORS_API_KEY="", STATION_SEARCH_RADII_MILES=[10, 25])
class TripApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("load_stations", stdout=io.StringIO())

    def setUp(self):
        cache.clear()
        clear_station_index()
        patcher = mock.patch("routes.services.routing.get_route", side_effect=fake_route)
        self.get_route = patcher.start()
        self.addCleanup(patcher.stop)

    def post(self, start, finish):
        body = {"start": start, "finish": finish}
        return self.client.post("/api/route/", body, content_type="application/json")

    def test_cross_country_trip(self):
        resp = self.post("New York, NY", "Los Angeles, CA")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        summary = data["summary"]

        self.assertGreater(summary["distance_miles"], 2400)
        self.assertGreaterEqual(summary["number_of_fuel_stops"], 4)
        self.assertEqual(data["meta"]["external_api_calls"], 1)
        self.assertEqual(data["start"]["resolved_by"], "city_lookup")

        # Stops are in order and never more than one tank apart.
        markers = [0] + [s["mile_marker"] for s in data["fuel_stops"]] + [summary["distance_miles"]]
        for a, b in pairwise(markers):
            self.assertLessEqual(b - a, 500.1)

        # Totals add up, and fuel bought + the starting tank covers the whole trip.
        stop_total = sum(s["cost_usd"] for s in data["fuel_stops"])
        self.assertAlmostEqual(summary["total_fuel_cost_usd"], stop_total, delta=0.05)
        self.assertAlmostEqual(summary["gallons_purchased"] + 50, summary["total_gallons_used"], delta=0.05)

        # The response contains a drawable map.
        kinds = [f["properties"]["kind"] for f in data["map"]["features"]]
        self.assertEqual(kinds[:3], ["route", "start", "finish"])
        self.assertEqual(kinds.count("fuel_stop"), summary["number_of_fuel_stops"])
        self.assertIn("/api/route/map/?", data["map_url"])

    def test_repeat_request_uses_cache(self):
        self.post("New York, NY", "Los Angeles, CA")
        resp = self.post("New York, NY", "Los Angeles, CA")
        self.assertEqual(resp.json()["meta"]["external_api_calls"], 0)
        self.assertTrue(resp.json()["meta"]["route_from_cache"])
        self.assertEqual(self.get_route.call_count, 1)

    def test_short_trip_has_no_stops(self):
        resp = self.post("New York, NY", "Harrisburg, PA")
        data = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(data["fuel_stops"], [])
        self.assertEqual(data["summary"]["total_fuel_cost_usd"], 0)

    def test_accepts_coordinates_and_get(self):
        resp = self.client.get("/api/route/", {"start": "40.7128,-74.0060", "finish": "Columbus, Ohio"})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["start"]["resolved_by"], "coordinates")
        resp = self.post({"lat": 40.7128, "lng": -74.006}, "Tulsa, OK")
        self.assertEqual(resp.status_code, 200, resp.content)

    def test_rejects_locations_outside_usa(self):
        resp = self.post("51.5074,-0.1278", "Los Angeles, CA")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not inside the USA", resp.json()["error"])
        self.get_route.assert_not_called()

    def test_validation_errors(self):
        self.assertEqual(self.post("", "Los Angeles, CA").status_code, 400)
        resp = self.client.post("/api/route/", {}, content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_map_page_renders_without_new_api_call(self):
        url = self.post("New York, NY", "Los Angeles, CA").json()["map_url"]
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "leaflet")
        self.assertContains(resp, "trip-geojson")
        self.assertEqual(self.get_route.call_count, 1)

    def test_routing_failure_returns_502(self):
        self.get_route.side_effect = routing.RoutingError("provider down")
        resp = self.post("New York, NY", "Los Angeles, CA")
        self.assertEqual(resp.status_code, 502)
