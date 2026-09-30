from unittest import mock

from django.core.cache import cache
from django.test import TestCase

from routes.geo import city_key
from routes.locations import LocationError, resolve_location
from routes.models import City


class ResolveLocationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name, state, lat, lng in [
            ("Chicago", "IL", 41.88, -87.63),
            ("St. Louis", "MO", 38.63, -90.20),
        ]:
            City.objects.create(
                name=name, state=state, lookup_key=city_key(name, state), latitude=lat, longitude=lng
            )

    def setUp(self):
        cache.clear()

    def test_city_state_is_resolved_locally(self):
        for text in [
            "Chicago, IL",
            "chicago, il",
            "Chicago, Illinois",
            "Chicago, IL, USA",
            "Chicago, IL 60601",
        ]:
            loc, calls = resolve_location(text)
            self.assertEqual((loc.label, loc.resolved_by, calls), ("Chicago, IL", "city_lookup", 0), text)

    def test_saint_abbreviations_match(self):
        loc, _ = resolve_location("Saint Louis, MO")
        self.assertEqual(loc.label, "St. Louis, MO")

    def test_coordinates(self):
        loc, calls = resolve_location("41.88, -87.63")
        self.assertEqual((loc.lat, loc.lng, calls), (41.88, -87.63, 0))
        loc, calls = resolve_location({"lat": "41.88", "lng": "-87.63"})
        self.assertEqual(loc.resolved_by, "coordinates")

    def test_outside_usa_rejected(self):
        with self.assertRaises(LocationError):
            resolve_location("48.8566,2.3522")  # Paris
        with self.assertRaises(LocationError):
            resolve_location("Toronto, ON")

    @mock.patch("routes.locations.routing.geocode", return_value=(38.8977, -77.0365, "White House"))
    def test_addresses_fall_back_to_geocoder_once(self, geocode):
        loc, calls = resolve_location("1600 Pennsylvania Ave NW, Washington, DC")
        self.assertEqual((loc.resolved_by, calls), ("geocoder", 1))
        _, calls = resolve_location("1600 Pennsylvania Ave NW, Washington, DC")
        self.assertEqual(calls, 0)  # cached
        self.assertEqual(geocode.call_count, 1)
