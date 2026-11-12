from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.repositories import usuario_repository as repo
from app.schemas.auth import RestablecerClaveIn, UsuarioActualizarIn, UsuarioCrearIn, UsuarioOut
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import usuario_service

router = APIRouter(prefix="/usuarios", tags=["Usuarios y roles"])
admin = Depends(requiere(Permiso.USUARIOS_GESTIONAR))


@router.get("", response_model=list[UsuarioOut], summary="Listar usuarios")
def listar(_: Usuario = admin, db: Session = Depends(get_db)):
    return repo.listar(db)


@router.post("", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED, summary="Crear usuario")
def crear(datos: UsuarioCrearIn, actor: Usuario = admin, db: Session = Depends(get_db)):
    return usuario_service.crear(db, actor, email=datos.email, nombre=datos.nombre, rol=datos.rol, clave=datos.clave_temporal)


@router.get("/{usuario_id}", response_model=UsuarioOut, summary="Ver usuario")
def ver(usuario_id: int, _: Usuario = admin, db: Session = Depends(get_db)):
    return usuario_service.obtener(db, usuario_id)


@router.patch("/{usuario_id}", response_model=UsuarioOut, summary="Cambiar nombre, rol o estado")
def actualizar(usuario_id: int, datos: UsuarioActualizarIn, actor: Usuario = admin, db: Session = Depends(get_db)):
    return usuario_service.actualizar(db, actor, usuario_id, nombre=datos.nombre, rol=datos.rol, activo=datos.activo)


@router.post("/{usuario_id}/restablecer-clave", status_code=status.HTTP_204_NO_CONTENT, summary="Asignar clave temporal")
def restablecer(usuario_id: int, datos: RestablecerClaveIn, actor: Usuario = admin, db: Session = Depends(get_db)):
    usuario_service.restablecer_clave(db, actor, usuario_id, datos.clave_temporal)
