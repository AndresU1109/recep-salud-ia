"""Tarea de fondo del servidor: cada cierto tiempo genera los recordatorios que toquen y envía lo pendiente.
Varios procesos pueden correrla a la vez: los avisos se bloquean con SKIP LOCKED y los recordatorios tienen clave única."""
import asyncio
import logging
from datetime import datetime, timezone

from fastapi.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.database.session import get_session_factory
from app.services import notificacion_service, retencion_service

log = logging.getLogger("notificaciones")


_ultima_retencion = None


def ciclo() -> dict:
    global _ultima_retencion
    with get_session_factory()() as db:
        nuevos = notificacion_service.generar_recordatorios(db)
        resultado = {"recordatorios_nuevos": nuevos, **notificacion_service.procesar_pendientes(db)}
        hoy = datetime.now(timezone.utc).date()
        if _ultima_retencion != hoy:  # una vez al día (es idempotente: si dos procesos la corren, no pasa nada)
            retencion_service.aplicar(db)
            _ultima_retencion = hoy
        return resultado


async def bucle() -> None:
    s = get_settings()
    while True:
        await asyncio.sleep(s.notificaciones_intervalo_s)
        try:
            resultado = await run_in_threadpool(ciclo)
            if any(resultado.values()):
                log.info("notificaciones: %s", resultado)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - un fallo (BD caída, etc.) no debe matar la tarea: se reintenta en el siguiente ciclo
            log.exception("notificaciones: falló el ciclo")
