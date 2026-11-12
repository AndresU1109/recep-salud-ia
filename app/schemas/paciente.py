import re
from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.models.enums import EstadoCita, TipoDocumento
from app.schemas.comun import validar_email
from app.security.crypto import normalizar_documento

Nombre = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]
_TELEFONO = re.compile(r"^\+?[0-9 ()-]{7,20}$")


def _documento(v: str) -> str:
    n = normalizar_documento(v)
    if not 4 <= len(n) <= 20:
        raise ValueError("El número de documento debe tener entre 4 y 20 caracteres.")
    return v.strip()


def _fecha_nacimiento(v: date) -> date:
    hoy = date.today()
    if v > hoy or v.year < hoy.year - 130:
        raise ValueError("La fecha de nacimiento no es válida.")
    return v


def _telefono(v: str | None) -> str | None:
    if v is None or not v.strip():
        return None
    if not _TELEFONO.match(v.strip()):
        raise ValueError("Teléfono inválido (solo números, espacios, + ( ) -).")
    return v.strip()


def _email(v: str | None) -> str | None:
    return validar_email(v) if v and v.strip() else None


class ConsentimientoIn(BaseModel):
    politica_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    aceptado: bool
    autoriza_transcripcion: bool = False  # guardar el contenido de sus conversaciones con el asistente


class PacienteCrear(BaseModel):
    tipo_documento: TipoDocumento
    numero_documento: str
    nombres: Nombre
    apellidos: Nombre
    fecha_nacimiento: date
    telefono: str | None = None
    email: str | None = None
    entidad_aseguradora: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=150)] = None
    consentimiento: ConsentimientoIn

    _doc = field_validator("numero_documento")(_documento)
    _nac = field_validator("fecha_nacimiento")(_fecha_nacimiento)
    _tel = field_validator("telefono")(_telefono)
    _mail = field_validator("email")(_email)


class PacienteActualizar(BaseModel):
    """Documento y fecha de nacimiento no se editan por la API (son la base de la verificación de identidad):
    si llegan, se responde 422 en lugar de ignorarlos en silencio."""

    model_config = ConfigDict(extra="forbid")

    nombres: Nombre | None = None
    apellidos: Nombre | None = None
    telefono: str | None = None
    email: str | None = None
    entidad_aseguradora: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=150)] = None
    activo: bool | None = None
    notificaciones_activas: bool | None = None  # el paciente puede pedir que no se le escriba

    _tel = field_validator("telefono")(_telefono)
    _mail = field_validator("email")(_email)


class ConsentimientoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    politica_version: str
    aceptado: bool
    autoriza_transcripcion: bool
    canal: str
    otorgado_en: datetime


class PacienteOut(BaseModel):
    id: int
    tipo_documento: TipoDocumento
    documento: str  # enmascarado: ****1234
    nombres: str
    apellidos: str
    fecha_nacimiento: date
    telefono: str | None
    email: str | None
    entidad_aseguradora: str | None
    activo: bool
    notificaciones_activas: bool
    consentimiento_vigente: ConsentimientoOut | None


class DocumentoCompletoOut(BaseModel):
    tipo_documento: TipoDocumento
    numero_documento: str


class VerificarIdentidadIn(BaseModel):
    tipo_documento: TipoDocumento
    numero_documento: str = Field(max_length=40)
    fecha_nacimiento: date


class CitaResumenOut(BaseModel):
    id: int
    codigo_reserva: str
    inicio: datetime
    fin: datetime
    estado: EstadoCita
    canal: str
    profesional: str
    especialidad: str
    sede: str
