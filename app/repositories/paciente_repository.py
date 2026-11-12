import re

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models import Cita, ConsentimientoDatos, Especialidad, Paciente, Profesional, Sede
from app.models.enums import TipoDocumento
from app.security.crypto import hash_documento


def por_id(db: Session, paciente_id: int) -> Paciente | None:
    return db.get(Paciente, paciente_id)


def por_documento(db: Session, tipo: TipoDocumento | str, numero: str) -> Paciente | None:
    tipo = TipoDocumento(tipo)
    return db.scalar(select(Paciente).where(Paciente.tipo_documento == tipo, Paciente.documento_hash == hash_documento(tipo.value, numero)))


def _escapar_like(t: str) -> str:
    return t.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


def buscar_texto(db: Session, texto: str, limite: int = 50) -> tuple[list[Paciente], str]:
    """Busca por documento (si parece uno) o por nombre. Devuelve (pacientes, 'documento'|'nombre')."""
    texto = texto.strip()
    # Un documento es una sola "palabra" con al menos un dígito (1.023.456.789, AB123456); un nombre no.
    if re.fullmatch(r"[0-9A-Za-z.\-]{5,25}", texto) and any(ch.isdigit() for ch in texto):
        hashes = [hash_documento(t.value, texto) for t in TipoDocumento]
        filas = db.scalars(select(Paciente).where(Paciente.documento_hash.in_(hashes)).limit(limite))
        return list(filas), "documento"
    condiciones = []
    for token in texto.split()[:4]:
        patron = f"%{_escapar_like(token)}%"
        condiciones.append(or_(Paciente.nombres.ilike(patron, escape="\\"), Paciente.apellidos.ilike(patron, escape="\\")))
    consulta = select(Paciente).order_by(Paciente.apellidos, Paciente.nombres).limit(limite)
    if condiciones:
        consulta = consulta.where(and_(*condiciones))
    return list(db.scalars(consulta)), "nombre"


def consentimiento_vigente(db: Session, paciente_id: int) -> ConsentimientoDatos | None:
    return db.scalar(select(ConsentimientoDatos).where(ConsentimientoDatos.paciente_id == paciente_id)
                     .order_by(ConsentimientoDatos.otorgado_en.desc(), ConsentimientoDatos.id.desc()).limit(1))


def consentimientos(db: Session, paciente_id: int) -> list[ConsentimientoDatos]:
    return list(db.scalars(select(ConsentimientoDatos).where(ConsentimientoDatos.paciente_id == paciente_id)
                           .order_by(ConsentimientoDatos.otorgado_en.desc(), ConsentimientoDatos.id.desc())))


def citas_de(db: Session, paciente_id: int, limite: int = 100, institucion_id: int | None = None):
    consulta = (select(Cita.id, Cita.codigo_reserva, Cita.inicio, Cita.fin, Cita.estado, Cita.canal,
                       Profesional.nombre_completo.label("profesional"), Especialidad.nombre.label("especialidad"), Sede.nombre.label("sede"))
                .join(Profesional, Profesional.id == Cita.profesional_id).join(Especialidad, Especialidad.id == Cita.especialidad_id)
                .join(Sede, Sede.id == Cita.sede_id).where(Cita.paciente_id == paciente_id))
    if institucion_id is not None:  # solo las citas de la institución con la que se habla (y las de sedes compartidas)
        consulta = consulta.where((Sede.institucion_id == institucion_id) | Sede.institucion_id.is_(None))
    return db.execute(consulta.order_by(Cita.inicio.desc()).limit(limite)).all()
