"""Fase 8 (BD + API): documentos, versiones, estados, vigencia, descargas y preguntas frecuentes."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.models import AuditoriaLog, Documento, DocumentoFragmento
from app.models.enums import CategoriaDocumento, EstadoDocumento, Rol
from tests.factories import crear_base
from tests.test_auth_api import cabecera, crear_usuario
from tests.test_rag_texto import docx_con, pdf_con_texto

A = "/api/v1"
TEXTO = "Para cancelar una cita se debe avisar con la anticipación que defina la institución.\n\nEl paciente puede reprogramar desde el portal."


@pytest.fixture
def coord(client, db):
    crear_usuario(db, email="coord@ejemplo.com", rol=Rol.COORDINADOR)
    return cabecera(client, "coord@ejemplo.com")


def datos(**cambios):
    base = {"titulo": "Manual de citas (demo)", "categoria": "MANUAL", "fuente": "Documento de prueba", "version": "1"}
    return {**base, **cambios}


def subir(client, h, texto=TEXTO, archivo=None, **cambios):
    cuerpo = datos(**cambios)
    if archivo is None:
        cuerpo["texto"] = texto
        return client.post(f"{A}/documentos", headers=h, data=cuerpo)
    return client.post(f"{A}/documentos", headers=h, data=cuerpo, files={"archivo": archivo})


# ---------- carga ----------
def test_crear_desde_texto_queda_en_borrador_sin_indexar(client, coord):
    r = subir(client, coord)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["estado"] == "BORRADOR" and d["n_fragmentos"] == 1 and d["n_indexados"] == 0 and d["indexado"] is False and d["vigencia"] == "sin_fecha"
    assert d["archivo_nombre"] is None and d["advertencias"] == []


CONTENIDOS = {
    "reglamento.pdf": lambda: pdf_con_texto(["Politica de cancelacion de citas para pacientes."]),
    "requisitos.docx": lambda: docx_con(["Requisitos para la consulta de medicina general en la sede."]),
    "faq.txt": lambda: "Atendemos de lunes a viernes en horario institucional.".encode(),
}


@pytest.mark.parametrize("nombre", list(CONTENIDOS))
def test_subir_archivos_de_cada_tipo(client, coord, nombre):
    r = subir(client, coord, archivo=(nombre, CONTENIDOS[nombre](), "application/octet-stream"), titulo=f"Doc {nombre}")
    assert r.status_code == 201, r.text
    assert r.json()["archivo_nombre"] == nombre and r.json()["n_fragmentos"] >= 1


def test_el_archivo_original_se_guarda_con_nombre_seguro_y_se_puede_descargar(client, coord, db):
    contenido = "Texto del reglamento interno de la institución de prueba.".encode()
    r = subir(client, coord, archivo=("../../etc/reglamento malicioso.txt", contenido, "text/plain"))
    assert r.status_code == 201
    d = db.get(Documento, r.json()["id"])
    ruta = Path(d.archivo_ruta)
    assert ruta.parent == Path(get_settings().uploads_dir) and ruta.name != d.archivo_nombre and ruta.suffix == ".txt"
    assert d.archivo_nombre == "reglamento malicioso.txt"  # solo el nombre, sin carpetas
    bajada = client.get(f"{A}/documentos/{d.id}/archivo", headers=coord)
    assert bajada.status_code == 200 and bajada.content == contenido and bajada.headers["x-content-type-options"] == "nosniff"


def test_descargar_un_documento_creado_con_texto_da_404(client, coord):
    assert client.get(f"{A}/documentos/{subir(client, coord).json()['id']}/archivo", headers=coord).status_code == 404


def test_no_se_sirve_un_archivo_fuera_de_la_carpeta_de_subidas(client, coord, db):
    d = db.get(Documento, subir(client, coord).json()["id"])
    d.archivo_ruta = str(Path(__file__))  # alguien manipuló la ruta en la BD
    db.flush()
    assert client.get(f"{A}/documentos/{d.id}/archivo", headers=coord).status_code == 404


def test_archivo_invalido_y_sin_texto_se_rechazan_con_mensaje_claro(client, coord):
    assert subir(client, coord, archivo=("virus.exe", b"MZ" + b"0" * 100, "application/octet-stream")).json()["error"]["codigo"] == "tipo_no_soportado"
    assert subir(client, coord, archivo=("roto.pdf", b"no es pdf" * 10, "application/pdf")).json()["error"]["codigo"] == "archivo_invalido"
    assert client.post(f"{A}/documentos", headers=coord, data=datos()).json()["error"]["codigo"] == "fuente_invalida"  # ni archivo ni texto
    assert subir(client, coord, texto="corto").json()["error"]["codigo"] == "sin_texto"


def test_limite_de_tamano_del_archivo(client, coord, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    r = subir(client, coord, archivo=("grande.txt", b"a" * (1024 * 1024 + 10), "text/plain"))
    assert r.status_code == 413 and r.json()["error"]["codigo"] == "archivo_muy_grande"


def test_advertencias_por_datos_personales_y_contenido_repetido(client, coord):
    r = subir(client, coord, texto="Contacto institucional: escribir a recepcion@institucion.example.com o llamar al 300 123 4567 para más información.")
    assert r.status_code == 201 and any("datos personales" in a for a in r.json()["advertencias"])
    igual = subir(client, coord, texto="Contacto institucional: escribir a recepcion@institucion.example.com o llamar al 300 123 4567 para más información.", version="2")
    assert any("mismo contenido" in a for a in igual.json()["advertencias"])


def test_titulo_y_version_no_se_repiten_pero_otra_version_si(client, coord):
    assert subir(client, coord).status_code == 201
    r = subir(client, coord)
    assert r.status_code == 409 and r.json()["error"]["codigo"] == "documento_duplicado"
    assert subir(client, coord, version="2", texto="Texto distinto de la versión dos del manual.").status_code == 201


def test_validaciones_de_metadatos(client, coord, db):
    b = crear_base(db)
    assert subir(client, coord, titulo="ab").status_code == 422
    assert subir(client, coord, categoria="INVENTADA").status_code == 422
    assert subir(client, coord, vigente_desde="2030-02-01", vigente_hasta="2030-01-01").json()["error"]["codigo"] == "vigencia_invalida"
    assert subir(client, coord, sede_id="999999").json()["error"]["codigo"] == "referencia_invalida"
    ok = subir(client, coord, sede_id=str(b.sede.id), especialidad_id=str(b.esp.id))
    assert ok.status_code == 201 and ok.json()["sede_id"] == b.sede.id


# ---------- permisos ----------
@pytest.mark.parametrize("rol", [Rol.RECEPCIONISTA, Rol.PROFESIONAL_SALUD, Rol.CONSULTA])
def test_solo_coordinacion_y_administracion_gestionan_documentos(client, db, rol):
    crear_usuario(db, email="otro@ejemplo.com", rol=rol)
    h = cabecera(client, "otro@ejemplo.com")
    assert client.get(f"{A}/documentos", headers=h).status_code == 403
    assert subir(client, h).status_code == 403
    assert client.get(f"{A}/faqs", headers=h).status_code == 403


def test_documentos_exigen_sesion(client):
    assert client.get(f"{A}/documentos").status_code == 401
    assert client.post(f"{A}/documentos", data=datos(texto=TEXTO)).status_code == 401


# ---------- estados y vigencia ----------
def test_activar_indexa_y_desactivar_lo_saca(client, coord):
    d = subir(client, coord).json()
    r = client.post(f"{A}/documentos/{d['id']}/activar", headers=coord)
    assert r.status_code == 200 and r.json()["estado"] == "ACTIVO" and r.json()["indexado"] is True and r.json()["advertencias"] == []
    assert client.post(f"{A}/documentos/{d['id']}/desactivar", headers=coord).json()["estado"] == "INACTIVO"


def test_activar_con_embeddings_caidos_activa_pero_avisa(client, coord, embedder_falso):
    d = subir(client, coord).json()
    embedder_falso.caido = True
    r = client.post(f"{A}/documentos/{d['id']}/activar", headers=coord)
    assert r.status_code == 200 and r.json()["estado"] == "ACTIVO" and r.json()["indexado"] is False
    assert any("No se pudo indexar" in a for a in r.json()["advertencias"])


def test_vigencia_calculada_y_aviso_de_documento_vencido(client, coord):
    hoy = date.today()
    casos = {"vigente": (hoy - timedelta(days=5), hoy + timedelta(days=5)), "pendiente": (hoy + timedelta(days=5), None), "vencido": (None, hoy - timedelta(days=5))}
    for i, (esperado, (desde, hasta)) in enumerate(casos.items()):
        cuerpo = {"version": f"v{i}", "texto": TEXTO + f" Variante {i} del manual."}
        if desde: cuerpo["vigente_desde"] = desde.isoformat()
        if hasta: cuerpo["vigente_hasta"] = hasta.isoformat()
        d = client.post(f"{A}/documentos", headers=coord, data=datos(**cuerpo)).json()
        assert d["vigencia"] == esperado
        if esperado == "vencido":
            assert any("vencido" in a for a in client.post(f"{A}/documentos/{d['id']}/activar", headers=coord).json()["advertencias"])


def test_editar_metadatos_pero_no_el_contenido(client, coord):
    d = subir(client, coord).json()
    r = client.patch(f"{A}/documentos/{d['id']}", headers=coord, json={"titulo": "Manual actualizado", "fuente": "Dirección administrativa"})
    assert r.status_code == 200 and r.json()["titulo"] == "Manual actualizado"
    assert client.patch(f"{A}/documentos/{d['id']}", headers=coord, json={"texto_extraido": "nuevo contenido"}).status_code == 422
    assert client.patch(f"{A}/documentos/{d['id']}", headers=coord, json={"vigente_desde": "2030-05-01", "vigente_hasta": "2030-01-01"}).status_code == 422
    assert client.patch(f"{A}/documentos/999999", headers=coord, json={"titulo": "xyz"}).status_code == 404


def test_nueva_version_copia_metadatos_y_no_toca_la_anterior(client, coord, db):
    b = crear_base(db)
    viejo = subir(client, coord, sede_id=str(b.sede.id), especialidad_id=str(b.esp.id)).json()
    client.post(f"{A}/documentos/{viejo['id']}/activar", headers=coord)
    r = client.post(f"{A}/documentos/{viejo['id']}/nueva-version", headers=coord, data={"version": "2", "texto": "Contenido completamente nuevo de la segunda versión."})
    assert r.status_code == 201
    nuevo = r.json()
    assert nuevo["estado"] == "BORRADOR" and nuevo["version"] == "2" and nuevo["titulo"] == viejo["titulo"] and nuevo["sede_id"] == b.sede.id
    assert client.get(f"{A}/documentos/{viejo['id']}", headers=coord).json()["estado"] == "ACTIVO"
    assert client.post(f"{A}/documentos/{viejo['id']}/nueva-version", headers=coord, data={"version": "2", "texto": "Otro contenido de la misma versión."}).status_code == 409


def test_listar_y_filtrar(client, coord, db):
    b = crear_base(db)
    subir(client, coord, titulo="Reglamento general", categoria="REGLAMENTO")
    subir(client, coord, titulo="Protocolo de urgencias", categoria="PROTOCOLO", especialidad_id=str(b.esp.id), texto="Texto del protocolo de urgencias de la institución.")
    listar = lambda **p: [x["titulo"] for x in client.get(f"{A}/documentos", headers=coord, params=p).json()]  # noqa: E731
    assert len(listar()) == 2
    assert listar(categoria="PROTOCOLO") == ["Protocolo de urgencias"]
    assert listar(especialidad_id=b.esp.id) == ["Protocolo de urgencias"]
    assert listar(q="reglamento") == ["Reglamento general"]
    assert listar(q="%") == []  # el comodín no se interpreta
    assert listar(estado="ACTIVO") == []


def test_detalle_muestra_los_fragmentos(client, coord):
    largo = "\n\n".join(f"Sección {i}. " + "Texto de relleno del reglamento institucional. " * 12 for i in range(8))
    d = subir(client, coord, texto=largo).json()
    det = client.get(f"{A}/documentos/{d['id']}", headers=coord).json()
    assert len(det["fragmentos"]) == d["n_fragmentos"] > 1 and det["caracteres"] > 3000
    assert [f["orden"] for f in det["fragmentos"]] == list(range(len(det["fragmentos"])))


def test_la_auditoria_no_guarda_el_contenido_del_documento(client, coord, db):
    subir(client, coord, texto="Contenido sensible de ejemplo que no debe ir a la auditoría.")
    r = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "documento_creado")).one()
    assert "sensible" not in str(r.detalle) and r.detalle["fragmentos"] == 1


# ---------- preguntas frecuentes ----------
def test_una_faq_crea_su_documento_espejo_activo(client, coord, db):
    r = client.post(f"{A}/faqs", headers=coord, json={"pregunta": "¿Cómo cancelo una cita?", "respuesta": "Puedes cancelarla desde recepción o por el asistente."})
    assert r.status_code == 201 and r.json()["documento_id"]
    doc = db.get(Documento, r.json()["documento_id"])
    assert doc.categoria == CategoriaDocumento.PREGUNTAS_FRECUENTES and doc.estado == EstadoDocumento.ACTIVO
    frag = db.scalars(select(DocumentoFragmento).where(DocumentoFragmento.documento_id == doc.id)).one()
    assert frag.contenido == "Pregunta: ¿Cómo cancelo una cita?\nRespuesta: Puedes cancelarla desde recepción o por el asistente."


def test_editar_la_faq_rehace_el_fragmento_y_lo_reindexa(client, coord, db, embedder_falso):
    faq = client.post(f"{A}/faqs", headers=coord, json={"pregunta": "¿Cuál es el horario?", "respuesta": "Respuesta inicial."}).json()
    doc = db.get(Documento, faq["documento_id"])
    vector_viejo = list(db.scalars(select(DocumentoFragmento.embedding).where(DocumentoFragmento.documento_id == doc.id)).one())
    assert doc.embedding_model is not None  # al crearla quedó indexada
    client.patch(f"{A}/faqs/{faq['id']}", headers=coord, json={"respuesta": "Respuesta corregida por la institución."})
    db.expire_all()
    frag = db.scalars(select(DocumentoFragmento).where(DocumentoFragmento.documento_id == faq["documento_id"])).one()
    assert "corregida" in frag.contenido and frag.embedding is not None
    assert list(frag.embedding) != vector_viejo  # el vector cambió con el texto


def test_si_los_embeddings_caen_la_faq_se_guarda_y_queda_pendiente(client, coord, db, embedder_falso):
    embedder_falso.caido = True
    r = client.post(f"{A}/faqs", headers=coord, json={"pregunta": "¿Cuál es el horario?", "respuesta": "Respuesta de prueba."})
    assert r.status_code == 201
    assert db.get(Documento, r.json()["documento_id"]).embedding_model is None


def test_desactivar_la_faq_desactiva_su_documento_y_hereda_sede_y_especialidad(client, coord, db):
    b = crear_base(db)
    faq = client.post(f"{A}/faqs", headers=coord, json={"pregunta": "¿Dónde queda la sede?", "respuesta": "En la dirección registrada.", "sede_id": b.sede.id, "especialidad_id": b.esp.id}).json()
    doc = db.get(Documento, faq["documento_id"])
    assert doc.sede_id == b.sede.id and doc.especialidad_id == b.esp.id
    client.patch(f"{A}/faqs/{faq['id']}", headers=coord, json={"activa": False})
    db.expire_all()
    assert db.get(Documento, faq["documento_id"]).estado == EstadoDocumento.INACTIVO
    client.patch(f"{A}/faqs/{faq['id']}", headers=coord, json={"activa": True})
    db.expire_all()
    assert db.get(Documento, faq["documento_id"]).estado == EstadoDocumento.ACTIVO


def test_validaciones_de_faq(client, coord):
    assert client.post(f"{A}/faqs", headers=coord, json={"pregunta": "abc", "respuesta": "xyz"}).status_code == 422
    r = client.post(f"{A}/faqs", headers=coord, json={"pregunta": "¿Pregunta válida aquí?", "respuesta": "Respuesta válida.", "sede_id": 999999})
    assert r.status_code == 422 and r.json()["error"]["codigo"] == "referencia_invalida"
    assert client.patch(f"{A}/faqs/999999", headers=coord, json={"activa": False}).status_code == 404
    assert len(client.get(f"{A}/faqs", headers=coord).json()) == 0
