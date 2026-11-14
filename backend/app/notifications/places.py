"""Place_Label: the nearest major Korean city within 30 km (design.md §14, Req 11.11).

No geocoding service is called. ``KNOWN_PLACES`` holds representative
coordinates (city hall area) of 16 cities; the default location
(37.5665, 126.9780) is "서울". Same signature as ``deps.fallback_place_label``
so tasks 7.1 can put ``place_label`` into the container.
"""

from __future__ import annotations

import math

EARTH_RADIUS_KM = 6371.0088  # IUGG mean radius
MAX_DISTANCE_KM = 30.0
UNKNOWN_PLACE = "현재 위치"

# (name, latitude, longitude). Ties are broken by this order.
KNOWN_PLACES: tuple[tuple[str, float, float], ...] = (
    ("서울", 37.5665, 126.9780),
    ("부산", 35.1796, 129.0756),
    ("인천", 37.4563, 126.7052),
    ("대구", 35.8714, 128.6014),
    ("대전", 36.3504, 127.3845),
    ("광주", 35.1595, 126.8526),
    ("울산", 35.5384, 129.3114),
    ("세종", 36.4800, 127.2890),
    ("수원", 37.2636, 127.0286),
    ("고양", 37.6584, 126.8320),
    ("창원", 35.2280, 128.6811),
    ("청주", 36.6424, 127.4890),
    ("전주", 35.8242, 127.1480),
    ("춘천", 37.8813, 127.7298),
    ("강릉", 37.7519, 128.8761),
    ("제주", 33.4996, 126.5312),
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def place_label(latitude: float, longitude: float) -> str:
    """Name of the closest known place at most 30 km away (ties: list order), else "현재 위치"."""
    if not (math.isfinite(latitude) and math.isfinite(longitude)):
        return UNKNOWN_PLACE
    best_name = UNKNOWN_PLACE
    best_distance = math.inf
    for name, lat, lon in KNOWN_PLACES:
        distance = haversine_km(latitude, longitude, lat, lon)
        if distance <= MAX_DISTANCE_KM and distance < best_distance:
            best_name, best_distance = name, distance
    return best_name
