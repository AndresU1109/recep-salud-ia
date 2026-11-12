"""Persistencia de conversaciones. El contenido de los mensajes solo se guarda si el paciente autorizó la transcripción
(o si es una conversación de prueba del personal); en los demás casos se guarda el hecho de que ocurrió, sin el texto."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Conversacion, EjecucionHerramienta, Mensaje, MensajeFuente
from app.models.enums import CanalConversacion, EstadoConversacion, ResultadoHerramienta, RolMensaje
from app.tools.registro import ResultadoHerramienta as Resultado


def crear(db: Session, canal: CanalConversacion = CanalConversacion.TEXTO, *, es_prueba: bool = False, institucion_id: int | None = None) -> Conversacion:
    c = Conversacion(id=uuid.uuid4(), canal=canal, es_prueba=es_prueba, guarda_transcripcion=es_prueba, institucion_id=institucion_id)
    db.add(c)
    db.commit()
    return c


def obtener(db: Session, conv_id: uuid.UUID) -> Conversacion | None:
    return db.get(Conversacion, conv_id)


def guardar_mensaje(db: Session, conv: Conversacion, rol: RolMensaje, contenido: str, intencion: str | None = None) -> Mensaje:
    m = Mensaje(conversacion_id=conv.id, rol=rol, contenido=contenido if conv.guarda_transcripcion else None, intencion=intencion)
    db.add(m)
    # Se confirma de inmediato: si una herramienta falla después y hace rollback, el mensaje ya quedó a salvo.
    db.commit()
    return m


def poner_intencion(m: Mensaje, intencion: str) -> None:
    m.intencion = intencion[:60]


def registrar_ejecucion(db: Session, conv: Conversacion, mensaje: Mensaje | None, nombre: str, r: Resultado) -> None:
    resultado = ResultadoHerramienta.EXITO if r.ok else (ResultadoHerramienta.RECHAZADA if r.rechazada else ResultadoHerramienta.ERROR)
    db.add(EjecucionHerramienta(conversacion_id=conv.id, mensaje_id=mensaje.id if mensaje else None, nombre=nombre[:60], parametros=r.parametros or None,
                                resultado=resultado, codigo_error=(r.codigo_error or None), duracion_ms=r.duracion_ms))
    db.commit()


def registrar_fuentes(db: Session, mensaje: Mensaje, fuentes) -> None:
    for f in fuentes:
        db.add(MensajeFuente(mensaje_id=mensaje.id, fragmento_id=f.fragmento_id, puntaje=f.score))


def sincronizar_identidad(conv: Conversacion, paciente_id: int | None, verificada: bool, guarda_transcripcion: bool) -> None:
    conv.paciente_id, conv.identidad_verificada = paciente_id, verificada
    if not conv.es_prueba:  # las pruebas del personal siempre guardan; en las reales manda la autorización del paciente
        conv.guarda_transcripcion = guarda_transcripcion


def finalizar(db: Session, conv: Conversacion, estado: EstadoConversacion) -> None:
    conv.estado, conv.finalizada_en = estado, datetime.now(timezone.utc)
    db.commit()


def mensajes(db: Session, conv_id: uuid.UUID, limite: int = 200) -> list[Mensaje]:
    return list(db.scalars(select(Mensaje).where(Mensaje.conversacion_id == conv_id).order_by(Mensaje.id).limit(limite)))
