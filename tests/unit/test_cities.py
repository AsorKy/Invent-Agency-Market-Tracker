"""Unit tests for the canonical target cities and the ``city`` mapping transform."""

import pytest

from libs.domain.cities import CANONICAL_CITIES, CITY_COORDINATES, fold, normalize_city
from services.ingest.app.mapping import MappingError, _transform


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Medell\u00edn, Antioquia, Colombia", "Medellin"),
        ("MEDELLIN", "Medellin"),
        ("Bogot\u00e1, D.C.", "Bogota"),
        ("Santa Fe de Bogot\u00e1", "Bogota"),
        ("Santiago de Cali, Valle del Cauca", "Cali"),
        ("Barranquilla, Atl\u00e1ntico", "Barranquilla"),
        ("Cartagena de Indias", "Cartagena"),
        ("Cra 43A # 1-50, El Poblado, Medell\u00edn", "Medellin"),
    ],
)
def test_known_spellings_map_to_canonical_names(value: str, expected: str) -> None:
    assert normalize_city(value) == expected


@pytest.mark.parametrize("value", ["Envigado, Antioquia", "Calima", "Colombia", "", "  ", None, 7])
def test_other_values_are_not_target_cities(value: object) -> None:
    assert normalize_city(value) is None


def test_canonical_names_match_the_repository_spelling_and_have_coordinates() -> None:
    assert CANONICAL_CITIES == ("Medellin", "Bogota", "Cali", "Barranquilla", "Cartagena")
    assert set(CITY_COORDINATES) == set(CANONICAL_CITIES)
    assert CITY_COORDINATES["Medellin"] == (6.2442, -75.5812)
    assert fold("Sal\u00f3n  de-Eventos") == "salon de eventos"


def test_city_transform_returns_canonical_or_fails() -> None:
    assert _transform("Bogot\u00e1 D.C., Colombia", "city") == "Bogota"
    with pytest.raises(MappingError):
        _transform("Lima, Peru", "city")
