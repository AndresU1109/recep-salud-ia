"""Alta, edición y baja lógica del catálogo (nada se borra: las citas históricas dependen de estos registros)."""
from typing import Any, TypeVar

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errores import ErrorNegocio
from app.models import Especialidad, Profesional, Usuario
from app.services import auditoria

M = TypeVar("M")


def _traducir(exc: IntegrityError) -> ErrorNegocio:
    nombre = getattr(getattr(exc.orig, "diag", None), "constraint_name", "") or ""
    if nombre.startswith(("uq_", "pk_")) or nombre.endswith("_key"):
        return ErrorNegocio("duplicado", "Ya existe un registro con esos datos.", 409)
    if nombre.startswith("fk_"):
        return ErrorNegocio("referencia_invalida", "Hace referencia a un registro que no existe.", 422)
    return ErrorNegocio("datos_fuera_de_regla", "Los datos no cumplen las reglas del sistema.", 422)


def _guardar(db: Session, accion: str, actor: Usuario, entidad: str, obj: Any, detalle: dict | None = None) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _traducir(exc) from exc
    auditoria.registrar(db, accion=accion, usuario_id=actor.id, entidad=entidad, entidad_id=obj.id, detalle=detalle)
    db.commit()


def listar(db: Session, modelo: type[M], orden, filtros: dict | None = None) -> list[M]:
    consulta = select(modelo)
    for columna, valor in (filtros or {}).items():
        if valor is not None:
            consulta = consulta.where(columna == valor)
    return list(db.scalars(consulta.order_by(*(orden if isinstance(orden, tuple) else (orden,)))))


def obtener(db: Session, modelo: type[M], id_: int) -> M:
    obj = db.get(modelo, id_)
    if obj is None:
        raise ErrorNegocio("no_encontrado", "El registro no existe.", 404)
    return obj


def crear(db: Session, actor: Usuario, entidad: str, modelo: type[M], datos: dict) -> M:
    obj = modelo(**datos)
    db.add(obj)
    _guardar(db, f"{entidad}_creado", actor, entidad, obj)
    return obj


def actualizar(db: Session, actor: Usuario, entidad: str, modelo: type[M], id_: int, datos: dict) -> M:
    obj = obtener(db, modelo, id_)
    for campo, valor in datos.items():
        setattr(obj, campo, valor)
    _guardar(db, f"{entidad}_actualizado", actor, entidad, obj, {"campos": sorted(datos)})
    return obj


# ---- Profesionales (relación N:M con especialidades) ----
def _especialidades(db: Session, ids: list[int]) -> list[Especialidad]:
    unicos = sorted(set(ids))
    encontradas = list(db.scalars(select(Especialidad).where(Especialidad.id.in_(unicos))))
    if len(encontradas) != len(unicos):
        raise ErrorNegocio("referencia_invalida", "Alguna especialidad no existe.", 422)
    return encontradas


def crear_profesional(db: Session, actor: Usuario, datos: dict) -> Profesional:
    ids = datos.pop("especialidad_ids")
    prof = Profesional(**datos, especialidades=_especialidades(db, ids))
    db.add(prof)
    _guardar(db, "profesional_creado", actor, "profesional", prof)
    return prof


def actualizar_profesional(db: Session, actor: Usuario, id_: int, datos: dict) -> Profesional:
    prof = obtener(db, Profesional, id_)
    ids = datos.pop("especialidad_ids", None)
    if ids is not None:
        prof.especialidades = _especialidades(db, ids)
    for campo, valor in datos.items():
        setattr(prof, campo, valor)
    _guardar(db, "profesional_actualizado", actor, "profesional", prof, {"campos": sorted(datos) + (["especialidades"] if ids else [])})
    return prof
