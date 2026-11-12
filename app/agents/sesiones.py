"""Sesiones de conversación en memoria (un proceso). Guardan el estado del asistente (identidad verificada, cupos ofrecidos,
confirmación pendiente). Si el servidor se reinicia o la sesión caduca, la conversación debe empezar de nuevo."""
import threading
import uuid
from datetime import datetime, timedelta

from app.agents.recepcionista import Recepcionista

TTL = timedelta(minutes=30)
MAXIMO = 500


class AlmacenSesiones:
    def __init__(self):
        self._d: dict[uuid.UUID, Recepcionista] = {}
        self._candado = threading.Lock()

    def agregar(self, conv_id: uuid.UUID, agente: Recepcionista) -> None:
        with self._candado:
            self._limpiar()
            if len(self._d) >= MAXIMO:  # se expulsa la más antigua sin uso
                del self._d[min(self._d, key=lambda k: self._d[k].ultimo_uso)]
            self._d[conv_id] = agente

    def obtener(self, conv_id: uuid.UUID) -> Recepcionista | None:
        with self._candado:
            self._limpiar()
            return self._d.get(conv_id)

    def quitar(self, conv_id: uuid.UUID) -> None:
        with self._candado:
            self._d.pop(conv_id, None)

    def _limpiar(self) -> None:
        limite = datetime.now() - TTL
        for k in [k for k, a in self._d.items() if a.ultimo_uso < limite]:
            del self._d[k]

    def __len__(self) -> int:
        return len(self._d)


SESIONES = AlmacenSesiones()
