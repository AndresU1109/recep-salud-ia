"""Datos 100 % ficticios para pruebas."""
import hashlib
import itertools
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import Cita, ConsentimientoDatos, Consultorio, Especialidad, Paciente, Profesional, Sede
from app.models.enums import CanalCita, EstadoCita, TipoDocumento

_contador = itertools.count(1)
LUNES_10 = datetime(2030, 3, 4, 10, 0, tzinfo=timezone.utc)


def nuevo_paciente(db, con_consentimiento: bool = True, **extra) -> Paciente:
    n = next(_contador)
    p = Paciente(
        tipo_documento=TipoDocumento.CC,
        documento_hash=hashlib.sha256(f"ficticio-{n}".encode()).hexdigest(),
        documento_cifrado="cifrado-ficticio",
        documento_ultimos4=f"{n:04d}"[-4:],
        nombres=f"Paciente{n}", apellidos="Ficticio", fecha_nacimiento=datetime(1990, 1, 1).date(),
        **extra,
    )
    db.add(p)
    db.flush()
    if con_consentimiento:
        db.add(ConsentimientoDatos(paciente_id=p.id, politica_version="demo-1", aceptado=True, canal="PRUEBA"))
        db.flush()
    return p


def crear_base(db) -> SimpleNamespace:
    sede = Sede(nombre=f"Sede {uuid.uuid4().hex[:6]}")
    db.add(sede)
    db.flush()
    cons1 = Consultorio(sede_id=sede.id, nombre="C1")
    cons2 = Consultorio(sede_id=sede.id, nombre="C2")
    esp = Especialidad(nombre=f"Esp {uuid.uuid4().hex[:6]}")
    prof1 = Profesional(nombre_completo="Dra. Ficticia Uno")
    prof2 = Profesional(nombre_completo="Dr. Ficticio Dos")
    db.add_all([cons1, cons2, esp, prof1, prof2])
    db.flush()
    return SimpleNamespace(sede=sede, cons1=cons1, cons2=cons2, esp=esp, prof1=prof1, prof2=prof2,
                           pac1=nuevo_paciente(db), pac2=nuevo_paciente(db))


def nueva_cita(db, base, *, inicio=LUNES_10, minutos=20, prof=None, cons=None, pac=None,
               estado=EstadoCita.PROGRAMADA, flush=True) -> Cita:
    c = Cita(
        codigo_reserva=uuid.uuid4().hex[:10].upper(),
        paciente_id=(pac or base.pac1).id, profesional_id=(prof or base.prof1).id,
        especialidad_id=base.esp.id, sede_id=base.sede.id, consultorio_id=(cons or base.cons1).id,
        inicio=inicio, fin=inicio + timedelta(minutes=minutos), estado=estado, canal=CanalCita.WEB,
    )
    db.add(c)
    if flush:
        db.flush()
    return c
