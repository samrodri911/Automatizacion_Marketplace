"""Tests de la extracción determinista de marca desde el título."""

from app.automation.brand_extractor import extract_brand


class TestExtractBrand:
    def test_hp_en_titulo(self):
        assert extract_brand("Laptop HP Pavilion 15 - i5, 12GB RAM") == "HP"

    def test_iphone_es_apple(self):
        assert extract_brand("iPhone 13 128GB") == "Apple"

    def test_samsung(self):
        assert extract_brand("Samsung Galaxy A54 128GB") == "Samsung"

    def test_sin_marca(self):
        assert extract_brand("Laptop sin marca 15 pulgadas") is None

    def test_marca_no_dentro_de_palabra(self):
        # "SharePoint" no debe detectar "HP"; "LG" no debe detectarse en "LGA".
        assert extract_brand("Curso de SharePoint básico") is None

    def test_vacio(self):
        assert extract_brand("") is None
        assert extract_brand(None) is None

    def test_primera_aparicion(self):
        # Aparece "Dell" antes que "Xiaomi" -> gana Dell.
        assert extract_brand("Dell Latitude y Xiaomi Redmi") == "Dell"

    def test_minusculas(self):
        assert extract_brand("televisor lg 43 pulgadas") == "LG"