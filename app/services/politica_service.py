"""Políticas institucionales. El sistema solo aplica lo que la institución configuró: sin política no hay límite
ni sanción inventados (el agente dirá que no tiene esa información)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.models import PoliticaInstitucional, Sede, Usuario
from app.models.enums import TipoPolitica
from app.services import auditoria, institucion_service

PARAMETROS_PERMITIDOS: dict[TipoPolitica, set[str]] = {
    TipoPolitica.CANCELACION: {"horas_minimas_cancelar"},
    TipoPolitica.REPROGRAMACION: {"horas_minimas_reprogramar", "maximo_reprogramaciones"},
    TipoPolitica.INASISTENCIA: {"inasistencias_para_aviso"},
    TipoPolitica.OTRA: set(),
}


def validar_parametros(tipo: TipoPolitica, parametros: dict) -> None:
    desconocidos = set(parametros) - PARAMETROS_PERMITIDOS[tipo]
    if desconocidos:
        raise ErrorNegocio("parametro_desconocido", f"Parámetros no válidos para {tipo.value}: {', '.join(sorted(desconocidos))}. "
                           f"Permitidos: {', '.join(sorted(PARAMETROS_PERMITIDOS[tipo])) or 'ninguno'}.", 422)
    for k, v in parametros.items():
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 100000:
            raise ErrorNegocio("parametro_invalido", f"«{k}» debe ser un número entero entre 0 y 100000.", 422)


def aplicable(db: Session, tipo: TipoPolitica, *, sede_id: int | None = None, especialidad_id: int | None = None,
              hoy: date | None = None, institucion_id: int | None = None) -> PoliticaInstitucional | None:
    """La política activa y vigente más específica (especialidad > sede > general), solo de la institución (y las compartidas).
    Si no se indica la institución pero sí la sede, se toma la de la sede."""
    hoy = hoy or hoy_local()
    if institucion_id is None and sede_id is not None:
        institucion_id = db.scalar(select(Sede.institucion_id).where(Sede.id == sede_id))
    candidatas = db.scalars(select(PoliticaInstitucional).where(
        institucion_service.alcance(PoliticaInstitucional.institucion_id, institucion_id),
        PoliticaInstitucional.tipo == tipo, PoliticaInstitucional.activa, PoliticaInstitucional.vigente_desde <= hoy,
        or_(PoliticaInstitucional.vigente_hasta.is_(None), PoliticaInstitucional.vigente_hasta >= hoy),
        or_(PoliticaInstitucional.sede_id.is_(None), PoliticaInstitucional.sede_id == sede_id),
        or_(PoliticaInstitucional.especialidad_id.is_(None), PoliticaInstitucional.especialidad_id == especialidad_id))).all()
    if not candidatas:
        return None
    puntaje = lambda p: ((2 if p.especialidad_id else 0) + (1 if p.sede_id else 0), p.vigente_desde, p.id)  # noqa: E731
    return max(candidatas, key=puntaje)


def listar(db: Session, tipo: TipoPolitica | None = None) -> list[PoliticaInstitucional]:
    q = select(PoliticaInstitucional).order_by(PoliticaInstitucional.tipo, PoliticaInstitucional.id)
    if tipo:
        q = q.where(PoliticaInstitucional.tipo == tipo)
    return list(db.scalars(q))


def hoy_local() -> date:
    """La fecha de la institución (no la del servidor de BD, que está en UTC)."""
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


def crear(db: Session, actor: Usuario, datos: dict) -> PoliticaInstitucional:
    validar_parametros(datos["tipo"], datos.get("parametros") or {})
    datos = {**datos, "vigente_desde": datos.get("vigente_desde") or hoy_local()}
    p = PoliticaInstitucional(**datos)
    db.add(p)
    db.flush()
    auditoria.registrar(db, accion="politica_creada", usuario_id=actor.id, entidad="politica", entidad_id=p.id, detalle={"tipo": p.tipo.value})
    db.commit()
    return p


def actualizar(db: Session, actor: Usuario, politica_id: int, datos: dict) -> PoliticaInstitucional:
    p = db.get(PoliticaInstitucional, politica_id)
    if p is None:
        raise ErrorNegocio("no_encontrado", "La política no existe.", 404)
    # Un null solo borra campos opcionales; en los obligatorios (título, vigencia, estado, parámetros) se ignora.
    datos = {k: v for k, v in datos.items() if v is not None or k in ("mensaje_paciente", "consecuencia_autorizada", "vigente_hasta")}
    if "parametros" in datos:
        validar_parametros(p.tipo, datos["parametros"])
    for k, v in datos.items():
        setattr(p, k, v)
    if p.vigente_hasta and p.vigente_hasta < p.vigente_desde:
        db.rollback()
        raise ErrorNegocio("vigencia_invalida", "La vigencia termina antes de empezar.", 422)
    auditoria.registrar(db, accion="politica_actualizada", usuario_id=actor.id, entidad="politica", entidad_id=p.id, detalle={"campos": sorted(datos)})
    db.commit()
    return p
