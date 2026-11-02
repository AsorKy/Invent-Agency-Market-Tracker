"""Canonical names and coordinates of the five target cities.

Provider records spell a city in many ways ("Medell\u00edn, Antioquia, Colombia",
"Bogot\u00e1 D.C.", "Santiago de Cali", "Cartagena de Indias"). ``normalize_city``
folds accents and case and returns one of the canonical names, spelled as the rest
of the repository already spells them (``datagen/builder.py``), or None when the
text names no target city. The mapping engine exposes it as the ``city`` transform
(``services/ingest/app/mapping.py``) and ``ops/bd_import.py`` uses the coordinates
for location searches. Pure Python, no I/O.
"""

import re
import unicodedata

MEDELLIN = "Medellin"
BOGOTA = "Bogota"
CALI = "Cali"
BARRANQUILLA = "Barranquilla"
CARTAGENA = "Cartagena"
CANONICAL_CITIES = (MEDELLIN, BOGOTA, CALI, BARRANQUILLA, CARTAGENA)

# (latitude, longitude) of each city center, used for map searches by location.
CITY_COORDINATES: dict[str, tuple[float, float]] = {
    MEDELLIN: (6.2442, -75.5812),
    BOGOTA: (4.7110, -74.0721),
    CALI: (3.4516, -76.5320),
    BARRANQUILLA: (10.9685, -74.7813),
    CARTAGENA: (10.3910, -75.4794),
}

# Folded (lower case, no accents) aliases; longer aliases are tried first.
_ALIASES: dict[str, tuple[str, ...]] = {
    MEDELLIN: ("medellin",),
    BOGOTA: ("santafe de bogota", "santa fe de bogota", "bogota d c", "bogota dc", "bogota"),
    CALI: ("santiago de cali", "cali"),
    BARRANQUILLA: ("barranquilla",),
    CARTAGENA: ("cartagena de indias", "cartagena"),
}
_PATTERNS = tuple(
    (city, re.compile(r"(?<![a-z])" + re.escape(alias) + r"(?![a-z])"))
    for city, aliases in _ALIASES.items()
    for alias in aliases
)


def fold(text: str) -> str:
    """Return ``text`` in lower case without accents, punctuation as spaces."""
    decomposed = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain).split())


def normalize_city(value: object) -> str | None:
    """Return the canonical target city named in ``value``, else None."""
    if not isinstance(value, str) or not value.strip():
        return None
    folded = fold(value)
    for city, pattern in _PATTERNS:
        if pattern.search(folded):
            return city
    return None
