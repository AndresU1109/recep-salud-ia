from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing import Annotated

Texto = Annotated[str, StringConstraints(strip_whitespace=True)]
Nombre = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=150)]
Opcional = Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=250)]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---- Sedes ----
class SedeCrear(BaseModel):
    nombre: Nombre
    direccion: Opcional = None
    ciudad: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)] = None
    telefono: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=30)] = None
    activa: bool = True


class SedeActualizar(BaseModel):
    nombre: Nombre | None = None
    direccion: Opcional = None
    ciudad: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=100)] = None
    telefono: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=30)] = None
    activa: bool | None = None


class SedeOut(_Out):
    id: int
    nombre: str
    direccion: str | None
    ciudad: str | None
    telefono: str | None
    activa: bool


# ---- Consultorios ----
class ConsultorioCrear(BaseModel):
    sede_id: int
    nombre: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    piso: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=20)] = None
    activo: bool = True


class ConsultorioActualizar(BaseModel):
    nombre: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] = None
    piso: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=20)] = None
    activo: bool | None = None


class ConsultorioOut(_Out):
    id: int
    sede_id: int
    nombre: str
    piso: str | None
    activo: bool


# ---- Especialidades ----
class EspecialidadCrear(BaseModel):
    nombre: Nombre
    descripcion: Texto | None = None
    activa: bool = True


class EspecialidadActualizar(BaseModel):
    nombre: Nombre | None = None
    descripcion: Texto | None = None
    activa: bool | None = None


class EspecialidadOut(_Out):
    id: int
    nombre: str
    descripcion: str | None
    activa: bool


# ---- Servicios ----
class ServicioCrear(BaseModel):
    especialidad_id: int
    nombre: Nombre
    descripcion: Texto | None = None
    duracion_minutos: int = Field(gt=0, le=480)
    activo: bool = True


class ServicioActualizar(BaseModel):
    nombre: Nombre | None = None
    descripcion: Texto | None = None
    duracion_minutos: int | None = Field(default=None, gt=0, le=480)
    activo: bool | None = None


class ServicioOut(_Out):
    id: int
    especialidad_id: int
    nombre: str
    descripcion: str | None
    duracion_minutos: int
    activo: bool


# ---- Requisitos ----
class RequisitoCrear(BaseModel):
    especialidad_id: int | None = None  # sin especialidad ni servicio = requisito general
    servicio_id: int | None = None
    titulo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)]
    descripcion: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2)]
    orden: int = Field(default=0, ge=0, le=999)
    activo: bool = True


class RequisitoActualizar(BaseModel):
    titulo: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=2, max_length=200)] = None
    descripcion: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=2)] = None
    orden: int | None = Field(default=None, ge=0, le=999)
    activo: bool | None = None


class RequisitoOut(_Out):
    id: int
    especialidad_id: int | None
    servicio_id: int | None
    titulo: str
    descripcion: str
    orden: int
    activo: bool


# ---- Profesionales ----
class EspecialidadMini(_Out):
    id: int
    nombre: str


class ProfesionalCrear(BaseModel):
    nombre_completo: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]
    registro_profesional: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=50)] = None
    especialidad_ids: list[int] = Field(min_length=1)
    activo: bool = True


class ProfesionalActualizar(BaseModel):
    nombre_completo: Annotated[str | None, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)] = None
    registro_profesional: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=50)] = None
    especialidad_ids: list[int] | None = Field(default=None, min_length=1)
    activo: bool | None = None


class ProfesionalOut(_Out):
    id: int
    nombre_completo: str
    registro_profesional: str | None
    activo: bool
    especialidades: list[EspecialidadMini]
