"""Excepciones propias de Marketplace Manager.

Tener una jerarquía explícita permite que la capa de servicios y la GUI
decidan qué hacer ante cada tipo de fallo (por ejemplo: un
ListingNotFoundError debe marcar el producto como fallido y continuar
con el siguiente; un SessionError debe pausar todo el proceso porque no
tiene sentido seguir sin sesión).
"""

from __future__ import annotations


class MarketplaceManagerError(Exception):
    """Excepción base de la aplicación."""


# ---------------------------------------------------------------------------
# Errores de datos / dominio
# ---------------------------------------------------------------------------
class ProductValidationError(MarketplaceManagerError):
    """El producto no cumple los datos mínimos requeridos.

    Ej: falta precio, no tiene fotografías, título vacío, etc.
    """

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        super().__init__(f"Campo inválido '{field}': {message}")


class ProductNotFoundError(MarketplaceManagerError):
    """No existe un producto con el id solicitado en la base de datos."""


class RepositoryError(MarketplaceManagerError):
    """Error de acceso a la base de datos SQLite."""


# ---------------------------------------------------------------------------
# Errores de automatización (capa Playwright / Facebook)
# ---------------------------------------------------------------------------
class AutomationError(MarketplaceManagerError):
    """Error genérico durante la automatización del navegador."""


class BrowserLaunchError(AutomationError):
    """No fue posible iniciar Chromium / el contexto persistente."""


class SessionError(AutomationError):
    """No hay una sesión de Facebook válida y no se pudo establecer."""


class ListingNotFoundError(AutomationError):
    """No se encontró ninguna publicación que coincida con el producto."""


class ListingMatchUncertainError(AutomationError):
    """Se encontraron candidatos pero ninguno se pudo confirmar con
    suficiente certeza. Nunca debe eliminarse una publicación en este caso.
    """


class DeletionFailedError(AutomationError):
    """El intento de eliminar la publicación no se pudo confirmar."""


class PublicationFailedError(AutomationError):
    """El intento de publicar no se pudo confirmar como exitoso."""


class RepublishError(MarketplaceManagerError):
    """Error de dominio del flujo de republicación (target, edición o fase)."""


class RepublishBlockedError(RepublishError):
    """La republicación está bloqueada por una regla de seguridad.

    P. ej. se intentó eliminar/proseguir con confianza distinta de HIGH, o
    la eliminación no pudo confirmarse (DELETE_UNCERTAIN / DELETE_FAILED):
    en ese caso el sistema debe DETENERSE y NO publicar una publicación
    nueva.
    """


class InterventionRequiredError(AutomationError):
    """Facebook requiere una acción manual del usuario (CAPTCHA,
    verificación de seguridad, confirmación de identidad, login, etc).

    Esta excepción NO representa un fallo definitivo: la capa de
    servicios debe capturarla, pasar la máquina de estados a
    WAITING_USER, y esperar a que el usuario confirme para reintentar.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Se requiere intervención manual: {reason}")


class AutomationCancelledError(AutomationError):
    """El usuario pulsó DETENER durante el proceso."""
