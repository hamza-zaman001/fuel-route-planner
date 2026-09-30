"""
Load the fuel price list into the database, geocoding every station offline.

The price file only has city/state, so each station is placed at its city's
coordinates using a free US cities dataset (MIT licensed, shipped in /data).
This runs once, so API requests never have to geocode stations.

    python manage.py load_stations
"""

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from routes.geo import US_STATES, city_key
from routes.models import City, Station
from routes.station_index import clear_station_index

DATA_DIR = Path(settings.BASE_DIR) / "data"

# A handful of places in the price list that the cities dataset lacks.
CITY_OVERRIDES = {
    ("Port Wentworth", "GA"): (32.1491, -81.1632),
    ("University Park", "IL"): (41.4434, -87.6834),
    ("Evergreen", "AL"): (31.4335, -86.9569),
    ("Henrico", "VA"): (37.5500, -77.4000),
    ("Elizabethport", "NJ"): (40.6490, -74.1860),
    ("Brookpark", "OH"): (41.3984, -81.8046),
}


class Command(BaseCommand):
    help = "Load and geocode truck stop fuel prices from the CSV files in /data."

    def add_arguments(self, parser):
        parser.add_argument("--prices", default=DATA_DIR / "fuel-prices.csv", type=Path)
        parser.add_argument("--cities", default=DATA_DIR / "us_cities.csv", type=Path)

    @transaction.atomic
    def handle(self, *args, **options):
        cities = self._load_cities(options["cities"])
        stations, skipped_foreign, unmatched = self._build_stations(options["prices"], cities)

        Station.objects.all().delete()
        Station.objects.bulk_create(stations, batch_size=1000)
        clear_station_index()

        self.stdout.write(
            self.style.SUCCESS(f"Loaded {len(cities):,} cities and {len(stations):,} US stations.")
        )
        self.stdout.write(f"Skipped {skipped_foreign:,} rows outside the USA (Canadian provinces).")
        if unmatched:
            self.stdout.write(
                self.style.WARNING(
                    f"Could not geocode {len(unmatched)} stations: " + ", ".join(sorted(unmatched)[:20])
                )
            )

    def _load_cities(self, path: Path) -> dict:
        """Return {lookup_key: City}. The dataset lists some cities several times
        (e.g. per county); we keep the listed point closest to their average, so
        the location is always a real place and never, say, in a bay."""
        points = defaultdict(list)
        names = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = city_key(row["CITY"], row["STATE_CODE"])
                points[key].append((float(row["LATITUDE"]), float(row["LONGITUDE"])))
                names.setdefault(key, (row["CITY"], row["STATE_CODE"]))

        for (name, state), latlng in CITY_OVERRIDES.items():
            key = city_key(name, state)
            points[key] = [latlng]
            names[key] = (name, state)

        cities = {}
        for key, pts in points.items():
            name, state = names[key]
            mean_lat = sum(p[0] for p in pts) / len(pts)
            mean_lng = sum(p[1] for p in pts) / len(pts)
            lat, lng = min(pts, key=lambda p: (p[0] - mean_lat) ** 2 + (p[1] - mean_lng) ** 2)
            cities[key] = City(name=name, state=state, lookup_key=key, latitude=lat, longitude=lng)
        City.objects.all().delete()
        City.objects.bulk_create(cities.values(), batch_size=2000)
        return cities

    def _build_stations(self, path: Path, cities: dict):
        """Group rows by OPIS id (the file lists some stations several times at
        different prices) and keep each station's lowest listed price."""
        rows_by_id = {}
        skipped_foreign = 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {k.strip(): (v or "").strip() for k, v in row.items()}
                if row["State"] not in US_STATES:
                    skipped_foreign += 1
                    continue
                opis_id = int(row["OPIS Truckstop ID"])
                price = Decimal(row["Retail Price"]).quantize(Decimal("0.001"))
                current = rows_by_id.get(opis_id)
                if current is None or price < current[1]:
                    rows_by_id[opis_id] = (row, price)

        stations, unmatched = [], set()
        for opis_id, (row, price) in rows_by_id.items():
            city = cities.get(city_key(row["City"], row["State"]))
            if city is None:
                unmatched.add(f"{row['City']}, {row['State']}")
                continue
            stations.append(
                Station(
                    opis_id=opis_id,
                    name=row["Truckstop Name"],
                    address=row["Address"],
                    city=row["City"],
                    state=row["State"],
                    rack_id=int(row["Rack ID"]),
                    price=price,
                    latitude=city.latitude,
                    longitude=city.longitude,
                )
            )
        return stations, skipped_foreign, unmatched
