"""Tests de la normalización y coincidencia de categorías (puras)."""

import pytest

from app.automation.category_matcher import (
    CategoryConfidence,
    CategoryMatch,
    normalize_category,
    match_category,
)


class TestNormalize:
    def test_tildes_mayusculas_y_espacios(self):
        assert normalize_category("  Electrónica e  Informática ") == "electronica e informatica"

    def test_puntuacion(self):
        assert normalize_category("Libros, películas y música") == "libros peliculas y musica"

    def test_vacio_y_none(self):
        assert normalize_category("") == ""
        assert normalize_category(None) == ""
        assert normalize_category("   ") == ""


class TestMatchCases:
    # Caso A: coincidencia directa con variación de género/número.
    def test_caso_a_coincidencia_directa(self):
        result = match_category(
            "Computadores Portátiles",
            ["Electrónica", "Computadoras", "Computadoras portátiles", "Accesorios para computadora"],
        )
        assert result.confidence == CategoryConfidence.HIGH
        assert result.selected == "Computadoras portátiles"
        assert result.score == pytest.approx(1.0)

    # Caso B: varias candidatas razonables -> decisión clara gracias al match exacto.
    def test_caso_b_varias_candidatas_decision_clara(self):
        result = match_category(
            "Computadores Portátiles",
            ["Computadoras portátiles", "Portátiles"],
        )
        assert result.confidence == CategoryConfidence.HIGH
        assert result.selected == "Computadoras portátiles"

    # Caso C: sinónimos en la BD que no están verbatim en Facebook.
    def test_caso_c_sinonimo_laptops(self):
        result = match_category(
            "Laptops",
            ["Televisores", "Accesorios para celulares", "Computadoras portátiles"],
        )
        assert result.confidence == CategoryConfidence.HIGH
        assert result.selected == "Computadoras portátiles"

    # Caso D: ninguna coincidencia razonable -> NO_MATCH/LOW, no seleccionar.
    def test_caso_d_sin_coincidencia(self):
        result = match_category(
            "Instrumentos musicales",
            ["Celulares", "Computadoras", "Electrodomésticos"],
        )
        assert result.confidence in (CategoryConfidence.NO_MATCH, CategoryConfidence.LOW)
        assert result.selected is None

    # Caso E: candidata parcial -> MEDIUM (no seleccionar sola).
    def test_caso_e_candidata_parcial_medium(self):
        result = match_category(
            "Computadores Portátiles",
            ["Portátiles", "Muebles", "Electrodomésticos"],
        )
        assert result.confidence == CategoryConfidence.MEDIUM
        assert result.selected is None


class TestRealScenario:
    # La categoría de BD "Electronica e informatica" (sin tilde) coincide
    # exactamente con la opción real de Facebook "Electrónica e informática".
    def test_electronica_sin_tilde_vs_con_tilde(self):
        result = match_category(
            "Electronica e informatica",
            ["Electrónica e informática", "Teléfonos celulares", "Instrumentos musicales"],
        )
        assert result.confidence == CategoryConfidence.HIGH
        assert result.selected == "Electrónica e informática"

    def test_resultado_incluye_candidatas_ordenadas(self):
        result = match_category("Laptops", ["Televisores", "Computadoras portátiles"])
        assert len(result.candidates) == 2
        assert result.candidates[0][1] >= result.candidates[1][1]


def test_category_match_dataclass():
    m = CategoryMatch("A", "B", CategoryConfidence.HIGH, 1.0, [("B", 1.0)])
    assert m.requested == "A"
    assert m.selected == "B"