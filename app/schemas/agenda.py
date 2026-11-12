from datetime import date, datetime, time
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class EstadoSlot(str, Enum):
    LIBRE = "LIBRE"
    OCUPADO = "OCUPADO"
    BLOQUEADO = "BLOQUEADO"
    SIN_CUPO = "SIN_CUPO"  # se alcanzó el máximo de citas de la jornada
    PASADO = "PASADO"


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class HorarioCrear(BaseModel):
    profesional_id: int
    especialidad_id: int
    sede_id: int
    consultorio_id: int | None = None
    dia_semana: int = Field(ge=0, le=6, description="0 = lunes … 6 = domingo")
    hora_inicio: time
    hora_fin: time
    duracion_cita_min: int = Field(ge=5, le=240)
    cupos_maximos: int | None = Field(default=None, ge=1, le=500)
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activo: bool = True

    @model_validator(mode="after")
    def _coherencia(self):
        if self.hora_fin <= self.hora_inicio:
            raise ValueError("La hora de fin debe ser posterior a la de inicio.")
        if self.vigente_desde and self.vigente_hasta and self.vigente_hasta < self.vigente_desde:
            raise ValueError("La vigencia termina antes de empezar.")
        return self


class HorarioActualizar(BaseModel):
    sede_id: int | None = None
    consultorio_id: int | None = None
    dia_semana: int | None = Field(default=None, ge=0, le=6)
    hora_inicio: time | None = None
    hora_fin: time | None = None
    duracion_cita_min: int | None = Field(default=None, ge=5, le=240)
    cupos_maximos: int | None = Field(default=None, ge=1, le=500)
    vigente_desde: date | None = None
    vigente_hasta: date | None = None
    activo: bool | None = None


class HorarioOut(_Out):
    id: int
    profesional_id: int
    especialidad_id: int
    sede_id: int
    consultorio_id: int | None
    dia_semana: int
    hora_inicio: time
    hora_fin: time
    duracion_cita_min: int
    cupos_maximos: int | None
    vigente_desde: date
    vigente_hasta: date | None
    activo: bool
    advertencias: list[str] = []


class ExcepcionCrear(BaseModel):
    profesional_id: int | None = None  # sin profesional ni sede = aplica a toda la institución
    sede_id: int | None = None
    inicio: datetime
    fin: datetime
    motivo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=250)]

    @model_validator(mode="after")
    def _rango(self):
        if self.inicio.tzinfo is None or self.fin.tzinfo is None:
            raise ValueError("Las fechas deben incluir zona horaria (p. ej. 2030-03-04T08:00:00-05:00).")
        if self.fin <= self.inicio:
            raise ValueError("El fin debe ser posterior al inicio.")
        return self


class ExcepcionOut(_Out):
    id: int
    profesional_id: int | None
    sede_id: int | None
    inicio: datetime
    fin: datetime
    motivo: str
    citas_afectadas: int = 0  # citas activas dentro del bloqueo: NO se cancelan solas, hay que gestionarlas


class SlotOut(BaseModel):
    inicio: datetime
    fin: datetime
    estado: EstadoSlot
    detalle: str | None = None  # código de reserva si está ocupado, motivo si está bloqueado
    horario_id: int
    profesional_id: int
    profesional: str
    especialidad_id: int
    sede_id: int
    sede: str
    consultorio_id: int | None
    consultorio: str | None
