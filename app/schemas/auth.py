from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.models.enums import Rol
from app.schemas.comun import validar_email
from app.security.permisos import PERMISOS_POR_ROL

_validar_email = validar_email


class LoginIn(BaseModel):
    email: str = Field(max_length=254)
    clave: str = Field(min_length=1, max_length=200)

    _email = field_validator("email")(_validar_email)


class UsuarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nombre: str
    rol: Rol
    activo: bool
    debe_cambiar_clave: bool
    ultimo_acceso: datetime | None = None

    @computed_field  # solo para decidir qué muestra la interfaz; el servidor vuelve a validar cada acción
    @property
    def permisos(self) -> list[str]:
        return sorted(p.value for p in PERMISOS_POR_ROL[self.rol])


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    usuario: UsuarioOut


class CambiarClaveIn(BaseModel):
    clave_actual: str = Field(min_length=1, max_length=200)
    clave_nueva: str = Field(min_length=1, max_length=200)


class UsuarioCrearIn(BaseModel):
    email: str = Field(max_length=254)
    nombre: str = Field(min_length=2, max_length=150)
    rol: Rol
    clave_temporal: str = Field(min_length=1, max_length=200)

    _email = field_validator("email")(_validar_email)


class UsuarioActualizarIn(BaseModel):
    nombre: str | None = Field(default=None, min_length=2, max_length=150)
    rol: Rol | None = None
    activo: bool | None = None


class RestablecerClaveIn(BaseModel):
    clave_temporal: str = Field(min_length=1, max_length=200)
