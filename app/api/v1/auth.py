from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.schemas.auth import CambiarClaveIn, LoginIn, TokenOut, UsuarioOut
from app.security.dependencias import usuario_autenticado
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Autenticación"])


@router.post("/login", response_model=TokenOut, summary="Iniciar sesión")
def login(datos: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    usuario, token, expira = auth_service.iniciar_sesion(
        db, datos.email, datos.clave, ip=request.client.host if request.client else None
    )
    response.headers["Cache-Control"] = "no-store"
    return TokenOut(access_token=token, expires_in=expira, usuario=UsuarioOut.model_validate(usuario))


@router.get("/me", response_model=UsuarioOut, summary="Usuario de la sesión")
def me(usuario: Usuario = Depends(usuario_autenticado)):
    return usuario


@router.post("/cambiar-clave", status_code=status.HTTP_204_NO_CONTENT, summary="Cambiar la propia contraseña")
def cambiar_clave(datos: CambiarClaveIn, usuario: Usuario = Depends(usuario_autenticado), db: Session = Depends(get_db)):
    auth_service.cambiar_clave(db, usuario, datos.clave_actual, datos.clave_nueva)
