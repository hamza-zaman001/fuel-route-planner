"""
In-memory index of all stations.

Stations are read from the database once per process and kept as NumPy
arrays, so each API request only does vectorized math on data that is
already in memory.
"""

import threading
from dataclasses import dataclass

import numpy as np

from routes.models import Station

_lock = threading.Lock()
_index = None


@dataclass(frozen=True)
class StationIndex:
    ids: np.ndarray  # database primary keys
    lat: np.ndarray
    lng: np.ndarray
    price: np.ndarray  # USD per gallon, float
    records: dict  # pk -> dict with display fields

    def __len__(self):
        return len(self.ids)


def get_station_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = _build()
    return _index


def clear_station_index() -> None:
    global _index
    with _lock:
        _index = None


def _build() -> StationIndex:
    rows = list(
        Station.objects.values(
            "id", "opis_id", "name", "address", "city", "state", "price", "latitude", "longitude"
        )
    )
    return StationIndex(
        ids=np.array([r["id"] for r in rows], dtype=np.int64),
        lat=np.array([r["latitude"] for r in rows], dtype=float),
        lng=np.array([r["longitude"] for r in rows], dtype=float),
        price=np.array([float(r["price"]) for r in rows], dtype=float),
        records={r["id"]: r for r in rows},
    )
