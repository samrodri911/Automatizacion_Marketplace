"""Máquina de estados del flujo de republicación.

Esta es la base sobre la que se construirán las siguientes iteraciones
(búsqueda, eliminación, creación, publicación...). En la Iteración 1
solo se usan activamente los estados relacionados con el arranque del
navegador y la sesión; el resto ya queda definido para no tener que
rediseñar la máquina de estados más adelante.

El diseño es intencionalmente simple (un Enum + una función de
transiciones válidas) en vez de una librería de FSM de terceros, porque
el flujo es lineal con un único punto de "pausa" (WAITING_USER).
"""

from __future__ import annotations

from enum import Enum, auto


class AutomationState(Enum):
    IDLE = auto()
    STARTING_BROWSER = auto()
    CHECKING_SESSION = auto()
    WAITING_LOGIN = auto()
    OPENING_MARKETPLACE = auto()
    OPENING_YOUR_LISTINGS = auto()
    SCANNING_LISTINGS = auto()     # Escaneando automáticamente el feed de publicaciones
    LISTINGS_SCANNED = auto()      # Escaneo y matching por lote completado
    SEARCHING_LISTING = auto()
    MATCHING_LISTING = auto()
    VERIFYING_LISTING = auto()
    # -- Flujo de republicación (Iteración 5): MATCH → EDITAR → ELIMINAR → PUBLICAR --
    MATCHED = auto()               # Una coincidencia HIGH fue congelada como target
    EDITING_PRODUCT = auto()       # El usuario está editando los datos de la NUEVA publicación
    AWAITING_REPUBLISH_CONFIRM = auto()  # Esperando confirmación "Eliminar y republicar"
    # -- Flujo de eliminación (Iteración 4) --
    VERIFYING_DELETE = auto()      # Verificando condiciones pre-eliminación
    AWAITING_DELETE_CONFIRM = auto()  # Esperando confirmación del usuario en la GUI
    DELETING_LISTING = auto()      # Ejecutando la acción destructiva en FB
    VERIFYING_DELETION = auto()    # Verificando que la publicación fue eliminada
    LISTING_DELETED = auto()       # Eliminación CONFIRMED (evidencia positiva)
    DELETE_UNCERTAIN = auto()      # No se puede verificar el resultado
    DELETE_FAILED = auto()         # Eliminación falló o no se pudo ejecutar
    # -- Flujo de creación/publicación --
    WAITING_DELETION = auto()
    CREATING_LISTING = auto()
    UPLOADING_IMAGES = auto()
    FILLING_LISTING = auto()       # Rellenando el formulario de la NUEVA publicación
    FILLING_DATA = auto()
    WAITING_USER = auto()
    PUBLISHING = auto()
    VERIFYING_PUBLICATION = auto()
    REPUBLISHED = auto()           # Flujo completo terminado con éxito
    REPUBLISH_BLOCKED = auto()     # El flujo se detuvo por una regla de seguridad
    SUCCESS = auto()
    ERROR = auto()
    CANCELLED = auto()
    PAUSED = auto()
    LISTING_FOUND = auto()
    LISTING_NOT_FOUND = auto()
    AMBIGUOUS_LISTING = auto()
    SEARCH_LIMIT_REACHED = auto()


# Estados desde los que es válido pedir "reanudar" tras una intervención
# manual (CAPTCHA, verificación, login...). El estado guardado antes de
# entrar a WAITING_USER debe pertenecer a este conjunto.
#
# IMPORTANTE (modificación 1 del spec): DELETING_LISTING NO está en
# RESUMABLE_STATES intencionalmente. Si ocurre una interrupción DURANTE
# la acción destructiva, la reanudación no debe reintentar la eliminación
# a ciegas: primero debe verificar el estado real (VERIFYING_DELETION)
# para determinar si el delete ya ocurrió o no.
# VERIFYING_DELETION sí es resumable: permite que el usuario ayude a
# verificar si hubiera ambigüedad.
RESUMABLE_STATES: frozenset[AutomationState] = frozenset(
    {
        AutomationState.CHECKING_SESSION,
        AutomationState.WAITING_LOGIN,
        AutomationState.OPENING_MARKETPLACE,
        AutomationState.OPENING_YOUR_LISTINGS,
        AutomationState.SCANNING_LISTINGS,
        AutomationState.SEARCHING_LISTING,
        AutomationState.MATCHING_LISTING,
        AutomationState.VERIFYING_DELETION,  # Verificar (no reintentar delete)
        AutomationState.CREATING_LISTING,
        AutomationState.UPLOADING_IMAGES,
        AutomationState.FILLING_LISTING,
        AutomationState.FILLING_DATA,
        AutomationState.PUBLISHING,
        AutomationState.VERIFYING_PUBLICATION,  # Verificar (no volver a crear/publicar)
    }
)

TERMINAL_STATES: frozenset[AutomationState] = frozenset(
    {
        AutomationState.SUCCESS,
        AutomationState.ERROR,
        AutomationState.CANCELLED,
        AutomationState.LISTING_DELETED,
        AutomationState.DELETE_UNCERTAIN,
        AutomationState.DELETE_FAILED,
        AutomationState.REPUBLISHED,
        AutomationState.REPUBLISH_BLOCKED,
    }
)


class StateTransitionError(Exception):
    """Se intentó una transición de estado no permitida."""


class AutomationStateMachine:
    """Máquina de estados mínima con historial y punto de reanudación."""

    def __init__(self) -> None:
        self._state = AutomationState.IDLE
        self._history: list[AutomationState] = [self._state]
        self._resume_point: AutomationState | None = None

    @property
    def state(self) -> AutomationState:
        return self._state

    @property
    def history(self) -> list[AutomationState]:
        return list(self._history)

    def transition_to(self, new_state: AutomationState) -> None:
        self._state = new_state
        self._history.append(new_state)

    def request_intervention(self, reason: str) -> None:
        """Guarda el estado actual como punto de reanudación y pasa a
        WAITING_USER. `reason` se usa solo para logging/UI."""
        if self._state in RESUMABLE_STATES:
            self._resume_point = self._state
        self.transition_to(AutomationState.WAITING_USER)

    def resume(self) -> AutomationState:
        """Vuelve al estado guardado antes de la intervención manual.

        No reinicia el flujo desde el principio: continúa desde donde
        se pausó, tal como exige la sección 21 del spec.
        """
        if self._resume_point is None:
            raise StateTransitionError("No hay un punto de reanudación guardado")
        target = self._resume_point
        self._resume_point = None
        self.transition_to(target)
        return target

    def is_terminal(self) -> bool:
        return self._state in TERMINAL_STATES

    def reset(self) -> None:
        self._state = AutomationState.IDLE
        self._history = [self._state]
        self._resume_point = None
