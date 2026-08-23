"""Tests de la lógica pura (sin navegador real) de selectors.py.

Nada de esto abre Chromium ni toca Facebook: solo verifica la
clasificación de URL + fragmentos de texto visible en señales de la
sección "Tus publicaciones".
"""

from app.automation.selectors import (
    SnippetMatch,
    classify_listings_section,
    find_marketplace_signal,
    find_snippet_matches,
)


class TestClassifyListingsSection:
    def test_heading_es_confirms_section(self):
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling",
            snippets=["Tus publicaciones", "Activos", "PS5 Slim $200"],
        )
        assert state.found is True
        assert state.url_matches is True
        kinds = {s.kind for s in state.signals}
        assert "heading" in kinds
        assert "tabs" in kinds

    def test_english_tokens_also_work(self):
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling",
            snippets=["Your listings", "Active", "Sold"],
        )
        assert state.found is True
        assert state.url_matches is True

    def test_case_insensitive_matching(self):
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling",
            snippets=["tus PUBLICACIONES", "activos"],
        )
        assert state.found is True

    def test_empty_state_counts_as_loaded(self):
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling",
            snippets=["No tienes publicaciones todavía"],
        )
        assert state.found is True
        assert all(s.kind == "empty" for s in state.signals)

    def test_tabs_alone_confirm_section(self):
        # El tab "Vendidos" puede aparecer incluso sin el heading; es una
        # señal fuerte de que la sección de publicaciones renderizó.
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling",
            snippets=["Vendidos", "Borradores"],
        )
        assert state.found is True

    def test_no_signals_means_not_found(self):
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/",
            snippets=["Explorar", "Categorías", "Buscar en Marketplace"],
        )
        assert state.found is False
        assert state.url_matches is False

    def test_url_match_alone_is_not_enough(self):
        # La URL coincide pero no hay señal textual: honestidad, no
        # sobreactuar el éxito.
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling",
            snippets=[],
        )
        assert state.found is False
        assert state.url_matches is True

    def test_url_parameter_ignored(self):
        state = classify_listings_section(
            url="https://www.facebook.com/marketplace/you/selling/?vanity=casa",
            snippets=["Tus publicaciones"],
        )
        assert state.url_matches is True


class TestFindMarketplaceSignal:
    def test_url_confirms_marketplace(self):
        assert find_marketplace_signal("https://www.facebook.com/marketplace/", []) is True
        assert find_marketplace_signal("https://www.facebook.com/marketplace", []) is True

    def test_nav_token_confirms_marketplace(self):
        assert find_marketplace_signal("https://www.facebook.com/", ["Marketplace"]) is True

    def test_no_signal_is_false(self):
        assert find_marketplace_signal("https://www.facebook.com/", ["Inicio", "Amigos"]) is False


class TestFindSnippetMatches:
    def test_returns_expected_matches(self):
        matches = find_snippet_matches(
            ["Tus publicaciones de compra", "Activos"],
            ("Tus publicaciones", "Activos"),
            kind="heading",
        )
        assert len(matches) == 2
        assert all(isinstance(m, SnippetMatch) for m in matches)
        assert matches[0].token == "Tus publicaciones"
        assert matches[1].kind == "heading"


class TestVerifyPublication:
    """Señales positivas/inciertas de la verificación de publicación (espejo
    de la verificación de eliminación)."""

    def test_item_url_is_positive_signal(self):
        from app.automation.selectors import verify_publication_from_page

        res = verify_publication_from_page(
            "https://www.facebook.com/marketplace/item/987654",
            "",
        )
        assert res.confirmed is True
        assert res.extracted_reference == "987654"
        assert res.extracted_url

    def test_success_text_is_positive_signal(self):
        from app.automation.selectors import verify_publication_from_page

        res = verify_publication_from_page("", "¡Tu anuncio se publicó correctamente!")
        assert res.confirmed is True

    def test_english_success_text_works(self):
        from app.automation.selectors import verify_publication_from_page

        res = verify_publication_from_page("", "Your listing was created")
        assert res.confirmed is True

    def test_no_signals_is_uncertain(self):
        from app.automation.selectors import verify_publication_from_page

        res = verify_publication_from_page("https://www.facebook.com/", "página vacía")
        assert res.confirmed is False
        assert res.signals_found == []

    def test_create_form_tokens_defined(self):
        from app.automation import selectors

        assert selectors.CREATE_PHOTO_INPUT_SELECTOR
        assert "Título" in selectors.CREATE_TITLE_FIELD_TOKENS
        assert "Precio" in selectors.CREATE_PRICE_FIELD_TOKENS
        assert "Categoría" in selectors.CREATE_CATEGORY_FIELD_TOKENS
        assert "Condición" in selectors.CREATE_CONDITION_FIELD_TOKENS
        assert "Descripción" in selectors.CREATE_DESCRIPTION_FIELD_TOKENS
        assert "Ubicación" in selectors.CREATE_LOCATION_FIELD_TOKENS
        assert any("Publicar" in t for t in selectors.CREATE_PUBLISH_BUTTON_TOKENS)
        assert selectors.CREATE_MARKETPLACE_SALE_TOKENS