"""Pacientes: registro con consentimiento, consulta auditada, verificación de identidad con límite de intentos."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import AuditoriaLog, ConsentimientoDatos, Paciente, Usuario
from app.models.enums import TipoDocumento
from app.repositories import paciente_repository as repo
from app.schemas.paciente import ConsentimientoIn, PacienteOut
from app.security import crypto
from app.services import auditoria

MAX_FALLOS_VERIFICACION = 5
VENTANA_VERIFICACION = timedelta(minutes=15)


def a_salida(db: Session, p: Paciente) -> PacienteOut:
    """Nunca incluye el documento completo: solo ****1234."""
    c = repo.consentimiento_vigente(db, p.id)
    return PacienteOut(
        id=p.id, tipo_documento=p.tipo_documento, documento=f"****{p.documento_ultimos4}", nombres=p.nombres,
        apellidos=p.apellidos, fecha_nacimiento=p.fecha_nacimiento, telefono=p.telefono, email=p.email,
        entidad_aseguradora=p.entidad_aseguradora, activo=p.activo, notificaciones_activas=p.notificaciones_activas,
        consentimiento_vigente=c and {"politica_version": c.politica_version, "aceptado": c.aceptado,
                                      "autoriza_transcripcion": c.autoriza_transcripcion, "canal": c.canal, "otorgado_en": c.otorgado_en},
    )


def obtener(db: Session, paciente_id: int) -> Paciente:
    p = repo.por_id(db, paciente_id)
    if not p:
        raise ErrorNegocio("paciente_no_encontrado", "El paciente no existe.", 404)
    return p


def registrar(db: Session, actor: Usuario | None, datos: dict, consentimiento: ConsentimientoIn, canal: str = "PANEL") -> Paciente:
    if not consentimiento.aceptado:
        raise ErrorNegocio("sin_consentimiento", "No se puede registrar al paciente sin su autorización para el tratamiento de datos personales.", 422)
    tipo = datos["tipo_documento"]
    numero = datos.pop("numero_documento")
    p = Paciente(**datos, documento_hash=crypto.hash_documento(tipo.value, numero), documento_cifrado=crypto.cifrar(crypto.normalizar_documento(numero)),
                 documento_ultimos4=crypto.ultimos4(numero))
    db.add(p)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise ErrorNegocio("paciente_duplicado", "Ya existe un paciente con ese documento.", 409) from exc
    db.add(ConsentimientoDatos(paciente_id=p.id, politica_version=consentimiento.politica_version, aceptado=True,
                               autoriza_transcripcion=consentimiento.autoriza_transcripcion, canal=canal))
    auditoria.registrar(db, accion="paciente_creado", actor="usuario" if actor else "agente", usuario_id=actor.id if actor else None,
                        entidad="paciente", entidad_id=p.id, detalle={"tipo_documento": tipo.value})
    db.commit()
    return p


def consultar(db: Session, actor: Usuario, paciente_id: int) -> Paciente:
    p = obtener(db, paciente_id)
    auditoria.registrar(db, accion="paciente_consultado", usuario_id=actor.id, entidad="paciente", entidad_id=p.id)
    db.commit()
    return p


def buscar(db: Session, actor: Usuario, texto: str, limite: int = 50) -> list[Paciente]:
    if len(texto.strip()) < 2:
        raise ErrorNegocio("busqueda_corta", "Escribe al menos 2 caracteres para buscar.", 422)
    pacientes, por = repo.buscar_texto(db, texto, limite)
    # No se guarda lo que se buscó (podría ser un documento), solo cómo y cuántos resultados.
    auditoria.registrar(db, accion="pacientes_busqueda", usuario_id=actor.id, detalle={"por": por, "resultados": len(pacientes)})
    db.commit()
    return pacientes


def actualizar(db: Session, actor: Usuario, paciente_id: int, datos: dict) -> Paciente:
    p = obtener(db, paciente_id)
    obligatorios = {"nombres", "apellidos", "activo", "notificaciones_activas"}
    datos = {c: v for c, v in datos.items() if v is not None or c not in obligatorios}  # un null en un campo obligatorio se ignora (no es un 500)
    for campo, valor in datos.items():
        setattr(p, campo, valor)
    auditoria.registrar(db, accion="paciente_actualizado", usuario_id=actor.id, entidad="paciente", entidad_id=p.id, detalle={"campos": sorted(datos)})
    db.commit()
    return p


def revelar_documento(db: Session, actor: Usuario, paciente_id: int) -> tuple[Paciente, str]:
    p = obtener(db, paciente_id)
    auditoria.registrar(db, accion="paciente_documento_revelado", usuario_id=actor.id, entidad="paciente", entidad_id=p.id)
    db.commit()
    return p, crypto.descifrar(p.documento_cifrado)


def registrar_consentimiento(db: Session, actor: Usuario | None, paciente_id: int, c: ConsentimientoIn, canal: str = "PANEL") -> ConsentimientoDatos:
    p = obtener(db, paciente_id)
    reg = ConsentimientoDatos(paciente_id=p.id, politica_version=c.politica_version, aceptado=c.aceptado,
                              autoriza_transcripcion=c.autoriza_transcripcion and c.aceptado, canal=canal)
    db.add(reg)
    auditoria.registrar(db, accion="consentimiento_registrado" if c.aceptado else "consentimiento_revocado",
                        actor="usuario" if actor else "agente", usuario_id=actor.id if actor else None, entidad="paciente", entidad_id=p.id)
    db.commit()
    return reg


# ---------- verificación de identidad ----------
def _clave_intentos(tipo: TipoDocumento, numero: str) -> str:
    return crypto.hash_documento(tipo.value, numero)[:16]


def verificar_identidad(db: Session, tipo: TipoDocumento, numero: str, fecha_nacimiento: date,
                        actor: Usuario | None = None) -> Paciente:
    """Documento + fecha de nacimiento. Misma respuesta si el paciente no existe o la fecha no coincide;
    tras 5 fallos en 15 minutos para ese documento se bloquea el intento (evita adivinar fechas)."""
    clave = _clave_intentos(tipo, numero)
    desde = datetime.now(timezone.utc) - VENTANA_VERIFICACION
    fallos = db.scalar(select(func.count()).select_from(AuditoriaLog).where(
        AuditoriaLog.accion == "verificar_identidad", AuditoriaLog.exito.is_(False), AuditoriaLog.entidad == "documento",
        AuditoriaLog.entidad_id == clave, AuditoriaLog.ocurrio_en >= desde)) or 0
    quien = {"actor": "usuario" if actor else "agente", "usuario_id": actor.id if actor else None}
    if fallos >= MAX_FALLOS_VERIFICACION:
        raise ErrorNegocio("verificacion_bloqueada", "Demasiados intentos de verificación. Intenta más tarde o comunícate con la institución.", 429)

    p = repo.por_documento(db, tipo, numero)
    if p is None or not p.activo or p.fecha_nacimiento != fecha_nacimiento:
        auditoria.registrar(db, accion="verificar_identidad", exito=False, entidad="documento", entidad_id=clave, **quien)
        db.commit()
        raise ErrorNegocio("identidad_no_verificada", "No fue posible verificar la identidad con esos datos.", 403)
    auditoria.registrar(db, accion="verificar_identidad", entidad="paciente", entidad_id=p.id, **quien)
    db.commit()
    return p
