"""Input type detection for the polymorphic resolve endpoint.

Uses regex patterns to classify user input as a cadastral reference,
GPS coordinates, or a text address. Priority order: cadastre > GPS >
address (most specific to least specific).
"""

import re

# Reference cadastrale: 14 chars -- commune(5) + prefixe(3) + section(2) + numero(4)
REF_CADASTRALE_RE = re.compile(r"^[0-9]{5}[0-9]{3}[A-Z0-9]{2}[0-9]{4}$")

# Coordonnees GPS: two decimal numbers separated by comma, semicolon, or whitespace
# Examples: "48.8566,2.3522", "48.8566, 2.3522", "48.8566;2.3522", "48.8566 2.3522"
GPS_RE = re.compile(r"^(-?\d{1,3}\.?\d*)[,;\s]+(-?\d{1,3}\.?\d*)$")


def detect_input_type(q: str) -> tuple[str, dict]:
    """Detect whether *q* is a cadastral reference, GPS coordinates, or address.

    Returns a (type, parsed_data) tuple so the caller never re-parses.
    Priority: cadastre > GPS > address (most specific first).
    """
    stripped = q.strip()

    # Cadastral reference: exactly 14 alphanumeric chars
    if REF_CADASTRALE_RE.match(stripped.upper()):
        return ("cadastre", {"ref": stripped.upper()})

    # GPS coordinates: lat/lon pair
    gps_match = GPS_RE.match(stripped)
    if gps_match:
        lat = float(gps_match.group(1))
        lon = float(gps_match.group(2))
        # Validate geographic bounds
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return ("gps", {"lat": lat, "lon": lon})

    # Default: treat as a text address
    return ("adresse", {"q": stripped})
