"""Fase 2: el esquema y sus restricciones, contra PostgreSQL real."""
import os
import threading
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models import (
    AuditoriaLog, Base, Cita, Documento, DocumentoFragmento, HorarioProfesional, Usuario,
)
from app.models.enums import CategoriaDocumento, EstadoCita, EstadoDocumento, Rol
from app.services.health_service import dimension_columna_vectores
from tests.conftest import crear_bd_temporal, eliminar_bd, migrar
from tests.factories import LUNES_10, crear_base, nueva_cita, nuevo_paciente


def rechaza(db, accion, error=IntegrityError):
    """La operación debe fallar en PostgreSQL; el savepoint deja la sesión utilizable."""
    with pytest.raises(error):
        with db.begin_nested():
            accion()


# ---------- migraciones ----------
def test_extensiones_instaladas(db):
    nombres = set(db.execute(text("SELECT extname FROM pg_extension")).scalars())
    assert {"vector", "btree_gist"} <= nombres


def test_migracion_coincide_con_los_modelos(engine_pruebas):
    with engine_pruebas.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], f"Los modelos y la migración difieren: {diff}"


def test_migracion_sube_y_baja_sin_residuos(engine_pruebas):
    nombre = f"recep_salud_migr_{os.getpid()}"
    url = crear_bd_temporal(nombre)
    try:
        migrar(url)
        migrar(url, "base", origen_downgrade=True)
        from sqlalchemy import create_engine
        eng = create_engine(url)
        with eng.connect() as c:
            tablas = set(c.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'")).scalars())
            ext = set(c.execute(text("SELECT extname FROM pg_extension")).scalars())
        eng.dispose()
        assert tablas <= {"alembic_version"}
        assert "vector" not in ext
        migrar(url)  # vuelve a subir sin errores
    finally:
        eliminar_bd(nombre)


def test_dimension_de_vectores_es_768(db):
    assert dimension_columna_vectores(db.connection()) == 768


# ---------- doble reserva ----------
def test_profesional_no_puede_tener_dos_citas_solapadas(db):
    b = crear_base(db)
    nueva_cita(db, b, cons=b.cons1, pac=b.pac1)
    rechaza(db, lambda: nueva_cita(db, b, inicio=LUNES_10 + timedelta(minutes=10), cons=b.cons2, pac=b.pac2))


def test_citas_consecutivas_si_se_permiten(db):
    b = crear_base(db)
    nueva_cita(db, b, minutos=20)
    nueva_cita(db, b, inicio=LUNES_10 + timedelta(minutes=20), minutos=20)  # empieza justo al terminar la otra


def test_consultorio_no_puede_usarse_dos_veces_a_la_vez(db):
    b = crear_base(db)
    nueva_cita(db, b, prof=b.prof1, pac=b.pac1)
    rechaza(db, lambda: nueva_cita(db, b, prof=b.prof2, pac=b.pac2))  # mismo consultorio


def test_paciente_no_puede_tener_dos_citas_a_la_vez(db):
    b = crear_base(db)
    nueva_cita(db, b, prof=b.prof1, cons=b.cons1)
    rechaza(db, lambda: nueva_cita(db, b, prof=b.prof2, cons=b.cons2))  # mismo paciente


def test_cita_cancelada_libera_el_cupo(db):
    b = crear_base(db)
    c = nueva_cita(db, b)
    c.estado = EstadoCita.CANCELADA
    db.flush()
    nueva_cita(db, b, pac=b.pac2)  # misma franja y profesional: ahora sí se puede


def test_cita_con_fin_antes_del_inicio_se_rechaza(db):
    b = crear_base(db)
    rechaza(db, lambda: nueva_cita(db, b, minutos=-10))


def test_codigo_de_reserva_es_unico(db):
    b = crear_base(db)
    c1 = nueva_cita(db, b)
    otro = nueva_cita(db, b, inicio=LUNES_10 + timedelta(hours=2), flush=False)
    otro.codigo_reserva = c1.codigo_reserva
    rechaza(db, db.flush)


def test_estado_invalido_se_rechaza_en_la_base(db):
    b = crear_base(db)
    c = nueva_cita(db, b)
    rechaza(db, lambda: db.execute(text("UPDATE citas SET estado='INVENTADO' WHERE id=:i"), {"i": c.id}))


def test_reservas_concurrentes_solo_una_gana(engine_pruebas):
    """Dos transacciones reales intentan la misma franja a la vez: PostgreSQL deja pasar solo una."""
    with Session(engine_pruebas) as s:
        b = crear_base(s)
        ids = SimpleNamespace(**{k: SimpleNamespace(id=v.id) for k, v in vars(b).items()})
        s.commit()
    barrera = threading.Barrier(2)
    resultados: list[str] = []

    def reservar(pac, cons):
        with Session(engine_pruebas) as s:
            nueva_cita(s, ids, pac=pac, cons=cons, flush=False)
            barrera.wait()
            try:
                s.commit()
                resultados.append("ok")
            except (IntegrityError, OperationalError):  # el conflicto puede salir como violación EXCLUDE o como deadlock (40P01)
                s.rollback()
                resultados.append("rechazada")

    hilos = [threading.Thread(target=reservar, args=(ids.pac1, ids.cons1)),
             threading.Thread(target=reservar, args=(ids.pac2, ids.cons2))]
    try:
        [h.start() for h in hilos]
        [h.join(30) for h in hilos]
        assert sorted(resultados) == ["ok", "rechazada"]
    finally:
        with engine_pruebas.begin() as c:
            c.execute(text("TRUNCATE citas, citas_historial, pacientes, consultorios, profesionales, "
                           "especialidades, sedes RESTART IDENTITY CASCADE"))


# ---------- integridad ----------
def test_documento_de_paciente_no_se_repite(db):
    p = nuevo_paciente(db)
    clon = nuevo_paciente(db)
    clon.documento_hash = p.documento_hash
    rechaza(db, db.flush)


def test_correo_de_usuario_unico_sin_importar_mayusculas(db):
    db.add(Usuario(email="Ana@Ejemplo.test", nombre="Ana", password_hash="x", rol=Rol.CONSULTA))
    db.flush()
    rechaza(db, lambda: (db.add(Usuario(email="ana@ejemplo.TEST", nombre="Otra", password_hash="x", rol=Rol.CONSULTA)), db.flush()))


def test_horario_con_fin_antes_del_inicio_se_rechaza(db):
    b = crear_base(db)
    rechaza(db, lambda: (db.add(HorarioProfesional(
        profesional_id=b.prof1.id, especialidad_id=b.esp.id, sede_id=b.sede.id, dia_semana=0,
        hora_inicio=time(12), hora_fin=time(8), duracion_cita_min=20)), db.flush()))


def test_horario_con_dia_invalido_se_rechaza(db):
    b = crear_base(db)
    rechaza(db, lambda: (db.add(HorarioProfesional(
        profesional_id=b.prof1.id, especialidad_id=b.esp.id, sede_id=b.sede.id, dia_semana=7,
        hora_inicio=time(8), hora_fin=time(12), duracion_cita_min=20)), db.flush()))


# ---------- auditoría inmutable ----------
def test_auditoria_se_puede_escribir_pero_no_modificar_ni_borrar(db):
    reg = AuditoriaLog(actor="sistema", accion="prueba")
    db.add(reg)
    db.flush()
    rechaza(db, lambda: db.execute(text("UPDATE auditoria_log SET accion='alterada' WHERE id=:i"), {"i": reg.id}), DBAPIError)
    rechaza(db, lambda: db.execute(text("DELETE FROM auditoria_log WHERE id=:i"), {"i": reg.id}), DBAPIError)
    assert db.get(AuditoriaLog, reg.id).accion == "prueba"


# ---------- pgvector ----------
def _vec(*posiciones_en_uno):
    v = [0.0] * 768
    for i in posiciones_en_uno:
        v[i] = 1.0
    return v


def _documento(db, titulo, estado=EstadoDocumento.ACTIVO):
    d = Documento(titulo=titulo, categoria=CategoriaDocumento.OTRO, fuente="prueba", version="1", estado=estado)
    db.add(d)
    db.flush()
    return d


def test_vector_con_dimension_equivocada_se_rechaza(db):
    d = _documento(db, "Doc dim")
    rechaza(db, lambda: (db.add(DocumentoFragmento(documento_id=d.id, orden=0, contenido="x", embedding=[0.1] * 3)), db.flush()),
            (DBAPIError, ValueError))


def test_busqueda_por_similitud_devuelve_primero_lo_mas_cercano(db):
    d = _documento(db, "Doc sim")
    for orden, v in enumerate([_vec(0), _vec(1), _vec(0, 1)]):
        db.add(DocumentoFragmento(documento_id=d.id, orden=orden, contenido=f"frag{orden}", embedding=v))
    db.flush()
    consulta = _vec(1)
    filas = db.execute(
        select(DocumentoFragmento.contenido, DocumentoFragmento.embedding.cosine_distance(consulta).label("dist"))
        .where(DocumentoFragmento.documento_id == d.id)
        .order_by("dist").limit(3)
    ).all()
    assert [f.contenido for f in filas] == ["frag1", "frag2", "frag0"]
    assert filas[0].dist == pytest.approx(0, abs=1e-6)


def test_fragmentos_sin_indexar_permitidos_y_se_borran_con_el_documento(db):
    d = _documento(db, "Doc cascada", EstadoDocumento.BORRADOR)
    db.add(DocumentoFragmento(documento_id=d.id, orden=0, contenido="sin vector", embedding=None))
    db.flush()
    db.expire_all()
    db.delete(db.get(Documento, d.id))
    db.flush()
    assert db.scalar(select(text("count(*)")).select_from(DocumentoFragmento)) == 0


def test_mismo_titulo_y_version_de_documento_no_se_repite(db):
    _documento(db, "Doc unico")
    rechaza(db, lambda: _documento(db, "Doc unico"))
