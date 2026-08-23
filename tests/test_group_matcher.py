"""Tests de la clasificación de grupos del paso final (pura)."""

from app.automation.group_matcher import (
    is_general_group,
    classify_group,
    group_keyword_score,
    product_keywords,
    select_audience_groups,
)
from app.models.product import Product

LAPTOP = Product(
    title="Laptop HP Pavilion 15 - i5, 12GB RAM",
    description="Laptop en buen estado.",
    price=500000.0,
    category="Electronica e informatica",
    condition="Usado - Aceptable",
    location="Cali",
    tags=["notebook", "hp", "usado"],
    images=["laptop/01.jpg"],
    enabled=True,
)

REAL_GROUPS = [
    "Implementos deportivos compra - venta",
    "Emprendedores Vendiendo De Todo",
    "Ventas de Articulos Deportivos",
    "Tu mejor opción, en compra de Inmuebles en México",
    "COMPRA Y VENTAS (VALLE DEL CAUCA) ENTRETENIMIENTO",
    "venta de todo tipo, estado de México",
]


class TestIsGeneralGroup:
    def test_de_todo_es_general(self):
        assert is_general_group("Emprendedores Vendiendo De Todo") is True

    def test_todo_tipo_es_general(self):
        assert is_general_group("venta de todo tipo, estado de México") is True

    def test_compra_ventas_primero_es_general(self):
        assert is_general_group("COMPRA Y VENTAS (VALLE DEL CAUCA) ENTRETENIMIENTO") is True

    def test_especializacion_antes_no_es_general(self):
        assert is_general_group("Implementos deportivos compra - venta") is False

    def test_deportivos_no_es_general(self):
        assert is_general_group("Ventas de Articulos Deportivos") is False

    def test_inmuebles_no_es_general(self):
        assert is_general_group("Tu mejor opción, en compra de Inmuebles en México") is False

    def test_vacio(self):
        assert is_general_group("") is False


class TestProductKeywords:
    def test_incluye_titulo_y_categoria(self):
        kw = product_keywords(LAPTOP)
        assert "laptop" in kw
        assert "portatil" in kw
        assert "electronica" in kw

    def test_incluye_familia(self):
        kw = product_keywords(LAPTOP)
        assert "computadora" in kw
        assert "tecnologia" in kw


class TestGroupKeywordScore:
    def test_grupo_relacionado(self):
        kw = product_keywords(LAPTOP)
        score = group_keyword_score("Computadoras y tecnologia Cali", kw)
        assert score >= 0.5

    def test_grupo_no_relacionado(self):
        kw = product_keywords(LAPTOP)
        assert group_keyword_score("Ventas de Articulos Deportivos", kw) == 0.0


class TestSelectAudienceGroups:
    def test_grupos_reales_de_la_laptop(self):
        selected = select_audience_groups(REAL_GROUPS, product_keywords(LAPTOP))
        assert "Emprendedores Vendiendo De Todo" in selected
        assert "venta de todo tipo, estado de México" in selected
        assert "COMPRA Y VENTAS (VALLE DEL CAUCA) ENTRETENIMIENTO" in selected
        assert "Implementos deportivos compra - venta" not in selected
        assert "Ventas de Articulos Deportivos" not in selected
        assert "Tu mejor opción, en compra de Inmuebles en México" not in selected

    def test_grupo_relacionado_especifico_se_elige(self):
        groups = ["Computadoras y tecnologia de Cali", "Compro todo de segunda"]
        selected = select_audience_groups(groups, product_keywords(LAPTOP))
        assert "Computadoras y tecnologia de Cali" in selected

    def test_classify_general_siempre(self):
        profile = classify_group("Compro y vendo de todo", product_keywords(LAPTOP))
        assert profile.selected is True
        assert profile.is_general is True

    def test_classify_especifico_sin_relacion(self):
        profile = classify_group("Autos usados en Cali", product_keywords(LAPTOP))
        assert profile.selected is False
        assert profile.is_general is False