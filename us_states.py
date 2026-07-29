"""US state/territory name normalization for location inputs."""

_ABBR: dict[str, str] = {
    "AL": "Alabama",         "AK": "Alaska",           "AZ": "Arizona",
    "AR": "Arkansas",        "CA": "California",        "CO": "Colorado",
    "CT": "Connecticut",     "DE": "Delaware",          "FL": "Florida",
    "GA": "Georgia",         "HI": "Hawaii",            "ID": "Idaho",
    "IL": "Illinois",        "IN": "Indiana",           "IA": "Iowa",
    "KS": "Kansas",          "KY": "Kentucky",          "LA": "Louisiana",
    "ME": "Maine",           "MD": "Maryland",          "MA": "Massachusetts",
    "MI": "Michigan",        "MN": "Minnesota",         "MS": "Mississippi",
    "MO": "Missouri",        "MT": "Montana",           "NE": "Nebraska",
    "NV": "Nevada",          "NH": "New Hampshire",     "NJ": "New Jersey",
    "NM": "New Mexico",      "NY": "New York",          "NC": "North Carolina",
    "ND": "North Dakota",    "OH": "Ohio",              "OK": "Oklahoma",
    "OR": "Oregon",          "PA": "Pennsylvania",      "RI": "Rhode Island",
    "SC": "South Carolina",  "SD": "South Dakota",      "TN": "Tennessee",
    "TX": "Texas",           "UT": "Utah",              "VT": "Vermont",
    "VA": "Virginia",        "WA": "Washington",        "WV": "West Virginia",
    "WI": "Wisconsin",       "WY": "Wyoming",
    "DC": "District of Columbia",
    "PR": "Puerto Rico",     "GU": "Guam",              "VI": "Virgin Islands",
}

_NAME_LOWER: dict[str, str] = {v.lower(): v for v in _ABBR.values()}

# Common "Washington D.C." variants that users type as the country field
_NAME_LOWER.update({
    "washington d.c.": "District of Columbia",
    "washington d.c":  "District of Columbia",
    "washington dc":   "District of Columbia",
    "washington, d.c.": "District of Columbia",
    "washington, dc":  "District of Columbia",
})


def normalize_state(value: str) -> tuple[str | None, str]:
    """
    Return (state_or_region, country) from a user-entered state/country string.

    Recognizes US state abbreviations ("CA") and full names ("california")
    and returns the canonical spelling + country="US".
    Unrecognized values are returned unchanged with state_or_region=None.

    Examples:
        "CA"            → ("California", "US")
        "ca"            → ("California", "US")
        "California"    → ("California", "US")
        "massachusetts" → ("Massachusetts", "US")
        "DC"            → ("District of Columbia", "US")
        "France"        → (None, "France")
        "England"       → (None, "England")
    """
    v = value.strip()
    if upper := v.upper() if len(v) <= 3 else None:
        if upper in _ABBR:
            return _ABBR[upper], "US"
    canonical = _NAME_LOWER.get(v.lower())
    if canonical:
        return canonical, "US"
    return None, v
