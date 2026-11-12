from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.models.enums import CanalCita, EstadoCita, EstadoListaEspera, TipoPolitica

Texto = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]


def _con_zona(v: datetime) -> datetime:
    if v.tzinfo is None:
        raise ValueError("La fecha y hora deben incluir zona horaria (p. ej. 2030-03-04T09:00:00-05:00).")
    return v


class CitaCrear(BaseModel):
    paciente_id: int
    profesional_id: int
    especialidad_id: int
    servicio_id: int | None = None
    sede_id: int | None = None
    inicio: datetime
    canal: CanalCita = CanalCita.PRESENCIAL
    notas: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=500)] = None

    _zona = field_validator("inicio")(_con_zona)


class CancelarIn(BaseModel):
    motivo: Texto | None = None
    forzar: bool = False  # solo surte efecto con permiso de verificar inasistencias (coordinación/administración)


class ReprogramarIn(BaseModel):
    inicio: datetime
    profesional_id: int | None = None
    forzar: bool = False

    _zona = field_validator("inicio")(_con_zona)


class InasistenciaIn(BaseModel):
    observacion: Texto | None = None


class VerificarInasistenciaIn(BaseModel):
    confirmada: bool  # False = descartar (el paciente sí fue atendido)
    observacion: Texto | None = None
    aplicar_consecuencia: bool = False  # solo si la política vigente define una consecuencia autorizada


class CitaOut(BaseModel):
    id: int
    codigo_reserva: str
    paciente_id: int | None  # solo con permiso de ver pacientes
    paciente: str | None
    profesional_id: int
    profesional: str
    especialidad_id: int
    especialidad: str
    servicio: str | None
    sede_id: int
    sede: str
    consultorio: str | None
    inicio: datetime
    fin: datetime
    estado: EstadoCita
    canal: CanalCita
    reprogramaciones: int
    motivo_cancelacion: str | None
    confirmada_en: datetime | None
    cancelada_en: datetime | None
    lista_espera_candidatos: int = 0  # al cancelar: cuántas personas esperaban un cupo como este


class HistorialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    accion: str
    estado_anterior: EstadoCita | None
    estado_nuevo: EstadoCita
    inicio_anterior: datetime | None
    fin_anterior: datetime | None
    actor_tipo: str
    usuario_id: int | None
    detalle: dict | None
    ocurrio_en: datetime


class InasistenciaOut(BaseModel):
    id: int
    cita_id: int
    paciente_id: int
    paciente: str
    fecha: datetime
    verificada: bool
    pendiente: bool
    observacion: str | None
    consecuencia_aplicada: str | None


class ListaEsperaCrear(BaseModel):
    paciente_id: int
    especialidad_id: int
    servicio_id: int | None = None
    profesional_id: int | None = None
    sede_id: int | None = None
    desde: date
    hasta: date

    @model_validator(mode="after")
    def _rango(self):
        if self.hasta < self.desde:
            raise ValueError("La fecha final es anterior a la inicial.")
        return self


class ListaEsperaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    paciente_id: int
    especialidad_id: int
    servicio_id: int | None
    profesional_id: int | None
    sede_id: int | None
    desde: date
    hasta: date
    estado: EstadoListaEspera
    cita_id: int | None


class AsignarListaEsperaIn(BaseModel):
    profesional_id: int
    inicio: datetime

    _zona = field_validator("inicio")(_con_zona)


class PoliticaCrear(BaseModel):
    tipo: TipoPolitica
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    parametros: dict[str, int] = Field(default_factory=dict)
    mensaje_paciente: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=1000)] = None
    consecuencia_autorizada: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=1000)] = None
    sede_id: int | None = None
    especialidad_id: int | None = None
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activa: bool = True


class PoliticaActualizar(BaseModel):
    titulo: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)] = None
    parametros: dict[str, int] | None = None
    mensaje_paciente: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=1000)] = None
    consecuencia_autorizada: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=1000)] = None
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activa: bool | None = None


class PoliticaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tipo: TipoPolitica
    titulo: str
    parametros: dict
    mensaje_paciente: str | None
    consecuencia_autorizada: str | None
    sede_id: int | None
    especialidad_id: int | None
    vigente_desde: date
    vigente_hasta: date | None
    activa: bool
