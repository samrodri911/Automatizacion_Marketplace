"""Extracción determinista de la marca de un producto desde su título.

No usa IA ni APIs: busca marcas conocidas (con variantes) en el título con
límites de palabra, y devuelve la primera que aparece. Sin coincidencia
devuelve None (el campo queda vacío, es opcional en Facebook).
"""

from __future__ import annotations

import re

# alias (en minúsculas, sin tildes) -> marca canónica mostrada en el anuncio.
_BRAND_ALIASES: dict[str, str] = {
    "hp": "HP",
    "hewlett-packard": "HP",
    "dell": "Dell",
    "lenovo": "Lenovo",
    "apple": "Apple",
    "iphone": "Apple",
    "ipad": "Apple",
    "macbook": "Apple",
    "imac": "Apple",
    "samsung": "Samsung",
    "asus": "Asus",
    "acer": "Acer",
    "toshiba": "Toshiba",
    "sony": "Sony",
    "lg": "LG",
    "xiaomi": "Xiaomi",
    "redmi": "Xiaomi",
    "huawei": "Huawei",
    "honor": "Honor",
    "microsoft": "Microsoft",
    "surface": "Microsoft",
    "google": "Google",
    "pixel": "Google",
    "motorola": "Motorola",
    "nokia": "Nokia",
    "oneplus": "OnePlus",
    "oppo": "Oppo",
    "vivo": "Vivo",
    "realme": "Realme",
    "htc": "HTC",
    "blackberry": "BlackBerry",
    "ibm": "IBM",
    "compaq": "Compaq",
    "canon": "Canon",
    "epson": "Epson",
    "brother": "Brother",
    "logitech": "Logitech",
    "razer": "Razer",
    "corsair": "Corsair",
    "kingston": "Kingston",
    "seagate": "Seagate",
    "western digital": "Western Digital",
    "panasonic": "Panasonic",
    "philips": "Philips",
    "bosch": "Bosch",
    "whirlpool": "Whirlpool",
    "electrolux": "Electrolux",
    "mabe": "Mabe",
    "nintendo": "Nintendo",
    "playstation": "PlayStation",
    "xbox": "Xbox",
    "yonex": "Yonex",
    "adidas": "Adidas",
    "nike": "Nike",
    "puma": "Puma",
    "reebok": "Reebok",
    "columbia": "Columbia",
    "samsonite": "Samsonite",
    "yamaha": "Yamaha",
    "casio": "Casio",
    "roland": "Roland",
    "ibanez": "Ibanez",
    "gibson": "Gibson",
    "fender": "Fender",
    "gopro": "GoPro",
    "dji": "DJI",
    "lego": "LEGO",
    "mattel": "Mattel",
    "hasbro": "Hasbro",
}


def extract_brand(title: str) -> str | None:
    """Devuelve la marca del título (primera que aparece) o None.

    La coincidencia respeta límites de palabra: "HP" no se encuentra dentro
    de "SharePoint" ni "PHPM". El orden de prioridad es por posición en el
    título (la marca suele ir al principio) y, a igualdad, por alias más
    largo (evita que "LG" gane a "LG Electronics").
    """
    if not title:
        return None
    lowered = title.casefold()
    best: tuple[tuple[int, int], str] | None = None
    for alias, canonical in _BRAND_ALIASES.items():
        match = re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", lowered)
        if match:
            key = (match.start(), -len(alias))
            if best is None or key < best[0]:
                best = (key, canonical)
    return best[1] if best is not None else None