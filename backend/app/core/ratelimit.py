"""Anti-abuso: limitador de intentos por IP y ruta (fase 03, ARCHITECTURE.md §6).

Ventana deslizante en memoria: suficiente para un despliegue LAN de proceso único
(§8.3). Se aplica solo a rutas de autenticación (``login``/``pin``); el estado vive en
``app.state`` para que cada instancia de la app (tests incluidos) parta de cero.
"""

import time
from collections import defaultdict
from collections.abc import Callable


class RateLimiter:
    """Ventana deslizante: ``attempts`` intentos por ``window_seconds`` y clave."""

    def __init__(
        self,
        attempts: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._attempts = attempts
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)

    def check(self, key: str) -> bool:
        """Registra un intento y dice si está permitido (False = excedido)."""
        now = self._clock()
        hits = self._hits[key]
        # Poda la ventana vencida (y claves muertas al pasar).
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.pop(0)
        if len(hits) >= self._attempts:
            if not hits:  # ventana agotada: no retener claves muertas
                del self._hits[key]
            return False
        hits.append(now)
        return True
