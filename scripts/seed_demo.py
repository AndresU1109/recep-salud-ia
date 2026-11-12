"""Catálogo DEMO ficticio para desarrollo (sede, especialidades, profesionales, horarios).

No crea políticas ni documentos institucionales: eso lo define la institución desde el panel.
Es idempotente: se puede ejecutar varias veces. Uso:  python -m scripts.seed_demo
"""
from datetime import time

from sqlalchemy import select

from app.core.config import get_settings
from app.database.session import get_session_factory
from app.models import Consultorio, Especialidad, HorarioProfesional, Profesional, Sede, Servicio
from app.services import politica_service

ESPECIALIDADES = {
    "Medicina General (demo)": [("Consulta de primera vez", 20), ("Consulta de control", 15)],
    "Pediatría (demo)": [("Consulta pediátrica", 30)],
}
PROFESIONALES = {
    "Dra. Prueba Uno (ficticia)": "Medicina General (demo)",
    "Dr. Prueba Dos (ficticio)": "Pediatría (demo)",
}


def _obtener_o_crear(db, modelo, filtro: dict, extra: dict | None = None):
    obj = db.execute(select(modelo).filter_by(**filtro)).scalar_one_or_none()
    if obj is None:
        obj = modelo(**filtro, **(extra or {}))
        db.add(obj)
        db.flush()
    return obj


def sembrar() -> dict:
    if get_settings().app_env == "production":
        raise SystemExit("Los datos demo no se cargan en producción.")
    with get_session_factory()() as db:
        sede = _obtener_o_crear(db, Sede, {"nombre": "Sede Demo Centro"}, {"ciudad": "Ciudad Demo"})
        consultorios = [_obtener_o_crear(db, Consultorio, {"sede_id": sede.id, "nombre": n}) for n in ("Consultorio 101", "Consultorio 102")]
        esp = {}
        for nombre, servicios in ESPECIALIDADES.items():
            esp[nombre] = _obtener_o_crear(db, Especialidad, {"nombre": nombre})
            for s_nombre, minutos in servicios:
                _obtener_o_crear(db, Servicio, {"especialidad_id": esp[nombre].id, "nombre": s_nombre},
                                 {"duracion_minutos": minutos})
        for i, (nombre, esp_nombre) in enumerate(PROFESIONALES.items()):
            prof = _obtener_o_crear(db, Profesional, {"nombre_completo": nombre})
            if esp[esp_nombre] not in prof.especialidades:
                prof.especialidades.append(esp[esp_nombre])
            ya = db.execute(select(HorarioProfesional.id).where(HorarioProfesional.profesional_id == prof.id)).first()
            if not ya:
                for dia in range(5):  # lunes a viernes
                    db.add(HorarioProfesional(
                        profesional_id=prof.id, especialidad_id=esp[esp_nombre].id, sede_id=sede.id,
                        consultorio_id=consultorios[i].id, dia_semana=dia,
                        hora_inicio=time(8, 0), hora_fin=time(12, 0), duracion_cita_min=20, vigente_desde=politica_service.hoy_local(),
                    ))
        db.commit()
        return {
            "sedes": db.query(Sede).count(), "especialidades": db.query(Especialidad).count(),
            "profesionales": db.query(Profesional).count(), "horarios": db.query(HorarioProfesional).count(),
        }


if __name__ == "__main__":
    print("Datos demo listos:", sembrar())
