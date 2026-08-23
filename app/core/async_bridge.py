"""Loop asyncio dedicado para Playwright (fix del crash 0xC0000005).

El crash nativo ocurría porque la API síncrona de Playwright ejecutaba el
loop del Proactor junto con greenlets en el mismo hilo que el event loop de
Qt, corrompiendo el heap de CPython. Aquí los objetos de
`playwright.async_api` viven SOLO en un hilo con su propio `asyncio` loop
siempre activo; el hilo Qt delega cada operación con `run_coroutine_threadsafe`
y espera el resultado de forma bloqueante. `AsyncProxy` hace esa delegación
transparente para que los módulos de automatización sigan usando una `Page`
con sintaxis síncrona.
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Callable

from app.core.logging_config import get_logger

logger = get_logger(__name__)

_SUBMIT_TIMEOUT_S = 600.0


def _is_playwright_object(value: Any) -> bool:
    return type(value).__module__.startswith("playwright.")


class AsyncBridge:
    """Hilo dedicado con un loop asyncio corriendo siempre."""

    def __init__(self, name: str = "playwright-loop") -> None:
        self._name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    @property
    def is_started(self) -> bool:
        return self._loop is not None and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_started:
            return
        self._started.clear()
        thread = threading.Thread(target=self._run_loop, name=self._name, daemon=True)
        self._thread = thread
        thread.start()
        if not self._started.wait(timeout=10):
            raise RuntimeError("No se pudo arrancar el loop asyncio dedicado")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._started.set()
        try:
            loop.run_forever()
        finally:
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass

    def submit(self, fn: Callable[[], Any], *, timeout: float = _SUBMIT_TIMEOUT_S) -> Any:
        """Ejecuta `fn()` en el loop del hilo dedicado y espera el resultado.

        `fn` debe crear/ejecutar la operación DENTRO del hilo del loop (no
        construir nada en el hilo Qt). Bloquea al llamante hasta terminar.
        """
        if not self.is_started:
            raise RuntimeError("El loop asyncio dedicado no está iniciado")

        async def _run() -> Any:
            result = fn()
            if inspect.isawaitable(result):
                result = await result
            return result

        future = asyncio.run_coroutine_threadsafe(_run(), self._loop)
        return future.result(timeout=timeout)

    def stop(self) -> None:
        loop, thread = self._loop, self._thread
        self._loop = None
        self._thread = None
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None and thread.is_alive():
            thread.join(timeout=10)


class AsyncProxy:
    """Proxy síncrono sobre un objeto de `playwright.async_api`.

    Cada método/propiedad se evalúa dentro del loop del `AsyncBridge`. Los
    resultados que son objetos de Playwright (o listas de ellos) se devuelven
    envueltos para encadenar llamadas; el resto se devuelve tal cual
    (str/int/bool/None/JSON).
    """

    __slots__ = ("_pw_obj", "_pw_bridge")

    def __init__(self, obj: Any, bridge: AsyncBridge) -> None:
        object.__setattr__(self, "_pw_obj", obj)
        object.__setattr__(self, "_pw_bridge", bridge)

    def __getattr__(self, name: str) -> Any:
        obj = object.__getattribute__(self, "_pw_obj")
        bridge = object.__getattribute__(self, "_pw_bridge")
        static = inspect.getattr_static(obj, name, None)
        if callable(static):
            value = getattr(obj, name)
            return self._bind_callable(bridge, value)
        if isinstance(static, property):
            value = bridge.submit(lambda: getattr(obj, name))
            return self._wrap(bridge, value)
        value = getattr(obj, name)
        return self._wrap(bridge, value)

    def _bind_callable(self, bridge: AsyncBridge, attr: Callable) -> Callable:
        def _call(*args: Any, **kwargs: Any) -> Any:
            args = tuple(self._unwrap(a) for a in args)
            kwargs = {k: self._unwrap(v) for k, v in kwargs.items()}
            result = bridge.submit(lambda: attr(*args, **kwargs))
            return self._wrap(bridge, result)

        return _call

    @staticmethod
    def _unwrap(value: Any) -> Any:
        return value._pw_obj if isinstance(value, AsyncProxy) else value

    @classmethod
    def _wrap(cls, bridge: AsyncBridge, value: Any) -> Any:
        if isinstance(value, AsyncProxy):
            return value
        if isinstance(value, (list, tuple)):
            return type(value)(cls._wrap(bridge, item) for item in value)
        if _is_playwright_object(value):
            return AsyncProxy(value, bridge)
        return value