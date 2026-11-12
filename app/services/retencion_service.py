"""Retención de datos: borra lo que ya no hace falta conservar, según los plazos de la configuración (0 = nunca).
No toca la bitácora de auditoría (es de solo escritura y debe conservarse) ni las citas. Es idempotente: se puede correr todos los días."""
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Conversacion, Mensaje, Notificacion
from app.models.enums import EstadoNotificacion
from app.services import auditoria

log = logging.getLogger("retencion")
FINALES = (EstadoNotificacion.ENVIADA, EstadoNotificacion.FALLIDA, EstadoNotificacion.OMITIDA)  # lo pendiente todavía se necesita


def aplicar(db: Session, ahora: datetime | None = None) -> dict[str, int]:
    s, ahora = get_settings(), ahora or datetime.now(timezone.utc)
    hecho = {"avisos_sin_texto": 0, "mensajes_sin_contenido": 0, "conversaciones_de_prueba_borradas": 0}
    if s.retencion_avisos_dias > 0:
        corte = ahora - timedelta(days=s.retencion_avisos_dias)
        hecho["avisos_sin_texto"] = db.execute(update(Notificacion).where(Notificacion.estado.in_(FINALES), Notificacion.updated_at < corte, Notificacion.cuerpo != "")
                                               .values(cuerpo="", asunto="", destino_enmascarado=None)).rowcount
    if s.retencion_conversaciones_dias > 0:
        corte = ahora - timedelta(days=s.retencion_conversaciones_dias)
        hecho["mensajes_sin_contenido"] = db.execute(update(Mensaje).where(Mensaje.contenido.is_not(None), Mensaje.conversacion_id.in_(
            Conversacion.__table__.select().with_only_columns(Conversacion.id).where(Conversacion.es_prueba.is_(False), Conversacion.iniciada_en < corte))).values(contenido=None)).rowcount
    if s.retencion_pruebas_dias > 0:
        corte = ahora - timedelta(days=s.retencion_pruebas_dias)
        hecho["conversaciones_de_prueba_borradas"] = db.execute(delete(Conversacion).where(Conversacion.es_prueba.is_(True), Conversacion.iniciada_en < corte)).rowcount  # sus mensajes caen en cascada
    if any(hecho.values()):
        auditoria.registrar(db, accion="retencion_aplicada", actor="sistema", detalle=hecho)
        log.info("retención: %s", hecho)
    db.commit()
    return hecho
