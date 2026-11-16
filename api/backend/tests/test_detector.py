"""Unit tests for resolve/detector.py -- input type detection."""

import pytest

pytestmark = pytest.mark.unit

from app.resolve.detector import detect_input_type


class TestDetectCadastre:
    """Cadastral reference detection (14-char format)."""

    def test_valid_reference(self):
        typ, data = detect_input_type("941480000D0117")
        assert typ == "cadastre"
        assert data["ref"] == "941480000D0117"

    def test_lowercase_normalized(self):
        typ, data = detect_input_type("941480000d0117")
        assert typ == "cadastre"
        assert data["ref"] == "941480000D0117"

    def test_with_whitespace(self):
        typ, _ = detect_input_type("  941480000D0117  ")
        assert typ == "cadastre"

    def test_too_short_is_address(self):
        typ, _ = detect_input_type("94148000")
        assert typ == "adresse"

    def test_too_long_is_address(self):
        typ, _ = detect_input_type("941480000D01170")
        assert typ == "adresse"


class TestDetectGPS:
    """GPS coordinate pair detection."""

    def test_comma_separator(self):
        typ, data = detect_input_type("48.8566,2.3522")
        assert typ == "gps"
        assert data["lat"] == pytest.approx(48.8566)
        assert data["lon"] == pytest.approx(2.3522)

    def test_semicolon_separator(self):
        typ, _ = detect_input_type("48.8566;2.3522")
        assert typ == "gps"

    def test_space_separator(self):
        typ, _ = detect_input_type("48.8566 2.3522")
        assert typ == "gps"

    def test_negative_coordinates(self):
        typ, data = detect_input_type("-33.8688,151.2093")
        assert typ == "gps"
        assert data["lat"] == pytest.approx(-33.8688)

    def test_out_of_range_lat_is_address(self):
        typ, _ = detect_input_type("91.0,2.0")
        assert typ == "adresse"

    def test_out_of_range_lon_is_address(self):
        typ, _ = detect_input_type("48.0,181.0")
        assert typ == "adresse"


class TestDetectAddress:
    """Default address detection (fallback)."""

    def test_simple_address(self):
        typ, data = detect_input_type("1 rue de la Paix Paris")
        assert typ == "adresse"
        assert data["q"] == "1 rue de la Paix Paris"

    def test_empty_string(self):
        typ, data = detect_input_type("  ")
        assert typ == "adresse"
        assert data["q"] == ""
