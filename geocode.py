"""Nominatim geocoding helper — shared by retag.py and geocode_cities.py."""

import requests

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "photo-sorter-family-archive/1.0 (sedawkins@gmail.com)"

# Cache within a process run so repeated cities (same clump) hit Nominatim once.
_cache: dict[tuple, tuple[float, float] | None] = {}


# Some informal country/region names Nominatim doesn't recognize — map to official names.
_COUNTRY_ALIASES = {
    "england":  "United Kingdom",
    "scotland": "United Kingdom",
    "wales":    "United Kingdom",
    "northern ireland": "United Kingdom",
}


def geocode(city: str, state_or_region: str | None, country: str) -> tuple[float, float] | None:
    """Return (lat, lon) city-center coordinates, or None if not found."""
    key = (city, state_or_region, country)
    if key in _cache:
        return _cache[key]

    nom_country = _COUNTRY_ALIASES.get(country.lower(), country)

    params = {"format": "json", "limit": 1, "addressdetails": 0}
    if country == "US" and state_or_region:
        params["city"]    = city
        params["state"]   = state_or_region
        params["country"] = "United States"
    else:
        params["city"]    = city
        params["country"] = nom_country

    result = _query(params)

    # Fallback: drop state for tricky US places (DC, territories, etc.)
    if result is None and country == "US" and state_or_region:
        fallback = {k: v for k, v in params.items() if k != "state"}
        result = _query(fallback)

    _cache[key] = result
    return result


def _query(params: dict) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            NOMINATIM_URL, params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return None
