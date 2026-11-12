from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import Usuario
from app.repositories import paciente_repository as repo
from app.schemas import paciente as s
from app.security.dependencias import requiere
from app.security.permisos import Permiso
from app.services import paciente_service as svc

router = APIRouter(prefix="/pacientes", tags=["Pacientes"])
ver = Depends(requiere(Permiso.PACIENTES_VER))
gestionar = Depends(requiere(Permiso.PACIENTES_GESTIONAR))


@router.get("", response_model=list[s.PacienteOut], summary="Buscar por nombre o documento")
def buscar(q: str = Query(..., max_length=100), limite: int = Query(50, ge=1, le=100), usuario: Usuario = ver, db: Session = Depends(get_db)):
    return [svc.a_salida(db, p) for p in svc.buscar(db, usuario, q, limite)]


@router.post("", response_model=s.PacienteOut, status_code=status.HTTP_201_CREATED, summary="Registrar paciente (con consentimiento)")
def registrar(datos: s.PacienteCrear, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    cuerpo = datos.model_dump(exclude={"consentimiento"})
    return svc.a_salida(db, svc.registrar(db, usuario, cuerpo, datos.consentimiento))


@router.post("/verificar-identidad", response_model=s.PacienteOut, summary="Verificar identidad con documento y fecha de nacimiento")
def verificar(datos: s.VerificarIdentidadIn, usuario: Usuario = ver, db: Session = Depends(get_db)):
    return svc.a_salida(db, svc.verificar_identidad(db, datos.tipo_documento, datos.numero_documento, datos.fecha_nacimiento, usuario))


@router.get("/{paciente_id}", response_model=s.PacienteOut)
def ver_paciente(paciente_id: int, usuario: Usuario = ver, db: Session = Depends(get_db)):
    return svc.a_salida(db, svc.consultar(db, usuario, paciente_id))


@router.patch("/{paciente_id}", response_model=s.PacienteOut, summary="Actualizar contacto y datos autorizados")
def actualizar(paciente_id: int, datos: s.PacienteActualizar, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.a_salida(db, svc.actualizar(db, usuario, paciente_id, datos.model_dump(exclude_unset=True)))


@router.get("/{paciente_id}/documento", response_model=s.DocumentoCompletoOut, summary="Ver el documento completo (queda auditado)")
def documento_completo(paciente_id: int, usuario: Usuario = Depends(requiere(Permiso.DOCUMENTO_PACIENTE_VER)), db: Session = Depends(get_db)):
    p, numero = svc.revelar_documento(db, usuario, paciente_id)
    return s.DocumentoCompletoOut(tipo_documento=p.tipo_documento, numero_documento=numero)


@router.get("/{paciente_id}/citas", response_model=list[s.CitaResumenOut], summary="Historial administrativo de citas",
            dependencies=[Depends(requiere(Permiso.CITAS_VER))])
def citas(paciente_id: int, _: Usuario = ver, db: Session = Depends(get_db)):
    svc.obtener(db, paciente_id)
    return [s.CitaResumenOut(**dict(f._mapping)) for f in repo.citas_de(db, paciente_id)]


@router.get("/{paciente_id}/consentimientos", response_model=list[s.ConsentimientoOut])
def consentimientos(paciente_id: int, _: Usuario = ver, db: Session = Depends(get_db)):
    svc.obtener(db, paciente_id)
    return repo.consentimientos(db, paciente_id)


@router.post("/{paciente_id}/consentimientos", response_model=s.ConsentimientoOut, status_code=status.HTTP_201_CREATED,
             summary="Registrar o revocar la autorización de tratamiento de datos")
def registrar_consentimiento(paciente_id: int, datos: s.ConsentimientoIn, usuario: Usuario = gestionar, db: Session = Depends(get_db)):
    return svc.registrar_consentimiento(db, usuario, paciente_id, datos)
