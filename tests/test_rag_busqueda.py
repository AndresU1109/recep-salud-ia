"""Fase 9: indexación, búsqueda semántica con filtros/vigencia/umbral y cliente de embeddings (más pruebas con el modelo real)."""
from datetime import date, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.embeddings import cliente
from app.embeddings.cliente import OllamaEmbeddings
from app.models import AuditoriaLog, Documento, DocumentoFragmento
from app.models.enums import CategoriaDocumento, EstadoDocumento, Rol
from app.services import busqueda_service as bs
from app.services import documento_service, indexacion_service
from tests.factories import crear_base
from tests.test_auth_api import cabecera, crear_usuario

A = "/api/v1"
HOY = date.today()
CANCELAR = "Para cancelar o reprogramar una cita médica se debe avisar a la institución con la anticipación que esta defina en su política."
REQUISITOS = "Para la consulta de medicina general el paciente debe presentar su documento de identidad y el carné de afiliación vigente."
HORARIO = "La sede principal atiende pacientes de lunes a viernes durante la mañana y la tarde, y no atiende los domingos ni festivos."


@pytest.fixture
def actor(db):
    return crear_usuario(db, email="gestor@ejemplo.com", rol=Rol.COORDINADOR)


def doc(db, actor, titulo, texto, *, estado=EstadoDocumento.ACTIVO, indexar=True, categoria=CategoriaDocumento.MANUAL, version="1", **extra):
    meta = {"titulo": titulo, "categoria": categoria, "fuente": "Fuente de prueba", "version": version, **extra}
    d, _ = documento_service.crear(db, actor, meta, texto=texto, estado=estado)
    if indexar:
        indexacion_service.indexar_documento(db, d.id)
    return d


def titulos(r):
    return [x.titulo for x in r.resultados]


# ======================= búsqueda =======================
def test_encuentra_el_fragmento_correcto_con_su_fuente(db, actor):
    doc(db, actor, "Política de citas", CANCELAR, categoria=CategoriaDocumento.POLITICA, version="3")
    doc(db, actor, "Requisitos", REQUISITOS)
    doc(db, actor, "Horarios", HORARIO)
    r = bs.buscar(db, "¿cómo puedo cancelar mi cita médica con anticipación?")
    assert r.suficiente and titulos(r)[0] == "Política de citas"
    top = r.resultados[0]
    assert top.version == "3" and top.fuente == "Fuente de prueba" and top.categoria == "POLITICA" and top.score >= r.umbral and top.vigencia == "sin_fecha"
    assert top.documento_id and top.fragmento_id and "cancelar" in top.contenido


def test_una_consulta_sin_relacion_no_devuelve_nada_y_lo_dice(db, actor):
    doc(db, actor, "Política de citas", CANCELAR)
    r = bs.buscar(db, "receta tradicional de arepas con queso")
    assert r.suficiente is False and r.resultados == [] and r.mejor_score is not None and r.mejor_score < r.umbral


def test_sin_documentos_indexados_no_hay_resultados(db):
    r = bs.buscar(db, "cancelar cita médica")
    assert r.suficiente is False and r.resultados == [] and r.mejor_score is None


def test_consulta_demasiado_corta(db):
    with pytest.raises(ErrorNegocio) as e:
        bs.buscar(db, " a ")
    assert e.value.codigo == "consulta_corta"


def test_solo_se_buscan_documentos_activos_e_indexados_con_el_modelo_actual(db, actor):
    doc(db, actor, "Borrador", CANCELAR, estado=EstadoDocumento.BORRADOR)
    doc(db, actor, "Inactivo", CANCELAR, estado=EstadoDocumento.INACTIVO)
    doc(db, actor, "Sin indexar", CANCELAR, indexar=False)
    viejo = doc(db, actor, "Modelo viejo", CANCELAR)
    viejo.embedding_model = "otro-modelo:1"
    db.flush()
    assert bs.buscar(db, "cancelar cita médica con anticipación").resultados == []
    doc(db, actor, "El bueno", CANCELAR)
    assert titulos(bs.buscar(db, "cancelar cita médica con anticipación")) == ["El bueno"]


def test_filtros_de_sede_y_especialidad(db, actor):
    a, b = crear_base(db), crear_base(db)
    doc(db, actor, "General", CANCELAR)
    doc(db, actor, "Solo sede A", CANCELAR, sede_id=a.sede.id, version="2")
    doc(db, actor, "Solo especialidad B", CANCELAR, especialidad_id=b.esp.id, version="3")
    q = "cancelar cita médica con anticipación"
    assert sorted(titulos(bs.buscar(db, q, sede_id=a.sede.id))) == ["General", "Solo especialidad B", "Solo sede A"]
    assert sorted(titulos(bs.buscar(db, q, sede_id=b.sede.id))) == ["General", "Solo especialidad B"]  # lo de la sede A no aplica en B
    assert sorted(titulos(bs.buscar(db, q, especialidad_id=a.esp.id))) == ["General", "Solo sede A"]
    assert sorted(titulos(bs.buscar(db, q, sede_id=a.sede.id, especialidad_id=a.esp.id))) == ["General", "Solo sede A"]
    assert len(bs.buscar(db, q).resultados) == 3  # sin filtros: todo, con su alcance a la vista
    assert {r.titulo: r.sede for r in bs.buscar(db, q).resultados}["Solo sede A"] == a.sede.nombre


def test_filtro_por_categoria(db, actor):
    doc(db, actor, "Un reglamento", CANCELAR, categoria=CategoriaDocumento.REGLAMENTO)
    doc(db, actor, "Una guía", CANCELAR, categoria=CategoriaDocumento.MANUAL, version="2")
    r = bs.buscar(db, "cancelar cita médica con anticipación", categorias=[CategoriaDocumento.REGLAMENTO])
    assert titulos(r) == ["Un reglamento"]


def test_vigencia_pendiente_fuera_vencido_marcado_como_desactualizado(db, actor):
    doc(db, actor, "Aún no vigente", CANCELAR, vigente_desde=HOY + timedelta(days=10), version="1")
    doc(db, actor, "Vencido", CANCELAR, vigente_hasta=HOY - timedelta(days=10), version="2")
    doc(db, actor, "Vigente", CANCELAR, vigente_desde=HOY - timedelta(days=10), vigente_hasta=HOY + timedelta(days=10), version="3")
    r = bs.buscar(db, "cancelar cita médica con anticipación")
    por_titulo = {x.titulo: x for x in r.resultados}
    assert set(por_titulo) == {"Vencido", "Vigente"}
    assert por_titulo["Vencido"].desactualizado is True and por_titulo["Vigente"].desactualizado is False
    assert "VENCIDO" in bs.construir_contexto(r.resultados)
    assert titulos(bs.buscar(db, "cancelar cita médica con anticipación", incluir_vencidos=False)) == ["Vigente"]


def test_resultados_ordenados_por_similitud_y_limitados_por_documento(db, actor):
    largo = "\n\n".join(f"Sección {i}. {CANCELAR} Detalle {i}." for i in range(1, 8))
    doc(db, actor, "Documento largo", largo)
    doc(db, actor, "Documento corto", REQUISITOS, version="2")
    r = bs.buscar(db, "cancelar cita médica con anticipación", k=10)
    assert sum(1 for x in r.resultados if x.titulo == "Documento largo") <= bs.MAX_POR_DOCUMENTO
    scores = [x.score for x in r.resultados]
    assert scores == sorted(scores, reverse=True)


def test_el_contexto_numera_y_cita_cada_fuente(db, actor):
    b = crear_base(db)
    doc(db, actor, "Política de citas", CANCELAR, version="4", sede_id=b.sede.id)
    ctx = bs.construir_contexto(bs.buscar(db, "cancelar cita médica con anticipación").resultados)
    assert ctx.startswith("[1] Política de citas (versión 4; fuente: Fuente de prueba; aplica a: ") and CANCELAR in ctx


# ======================= indexación =======================
def test_indexar_solo_calcula_lo_pendiente_y_forzar_recalcula(db, actor, embedder_falso):
    d = doc(db, actor, "Doc", CANCELAR, indexar=False)
    assert indexacion_service.indexar_documento(db, d.id) == 1 and d.embedding_model == get_settings().embedding_model and d.indexado_en
    llamadas = embedder_falso.llamadas
    assert indexacion_service.indexar_documento(db, d.id) == 0 and embedder_falso.llamadas == llamadas  # ya estaba: no se llama al modelo
    assert indexacion_service.indexar_documento(db, d.id, forzar=True) == 1 and embedder_falso.llamadas == llamadas + 1


def test_cambiar_de_modelo_deja_los_documentos_sin_poder_buscarse_hasta_reindexar(db, actor, monkeypatch):
    d = doc(db, actor, "Doc", CANCELAR)
    assert titulos(bs.buscar(db, "cancelar cita médica con anticipación")) == ["Doc"]
    monkeypatch.setattr(get_settings(), "embedding_model", "modelo-nuevo:1")
    estado = indexacion_service.estado_indice(db)
    assert estado["documentos"]["con_otro_modelo"] == 1 and estado["requiere_reindexar"] is True and estado["documentos"]["activos_sin_poder_buscarse"] == 1
    assert bs.buscar(db, "cancelar cita médica con anticipación").resultados == []  # nunca se mezclan vectores de modelos distintos
    assert indexacion_service.indexar_documento(db, d.id) == 1  # el modelo cambió: se recalculan todos los fragmentos
    assert d.embedding_model == "modelo-nuevo:1" and titulos(bs.buscar(db, "cancelar cita médica con anticipación")) == ["Doc"]
    assert indexacion_service.estado_indice(db)["requiere_reindexar"] is False


def test_documento_con_fragmentos_a_medias_no_cuenta_como_indexado(db, actor):
    d = doc(db, actor, "Doc", "\n\n".join(f"Párrafo {i}. " + CANCELAR * 3 for i in range(5)))
    frag = db.scalars(select(DocumentoFragmento).where(DocumentoFragmento.documento_id == d.id)).first()
    frag.embedding = None
    db.flush()
    assert indexacion_service.estado_indice(db)["documentos"]["pendientes"] == 1
    assert indexacion_service.indexar_documento(db, d.id) == 1  # solo el que faltaba


def test_indexar_sin_embeddings_falla_con_mensaje_claro_y_no_deja_nada_a_medias(db, actor, embedder_falso):
    d = doc(db, actor, "Doc", CANCELAR, indexar=False)
    embedder_falso.caido = True
    with pytest.raises(ErrorNegocio) as e:
        indexacion_service.indexar_documento(db, d.id)
    assert e.value.codigo == "embeddings_no_disponible" and d.embedding_model is None


def test_ids_a_reindexar_y_trabajo_en_curso(db, actor):
    pendiente = doc(db, actor, "Pendiente", CANCELAR, indexar=False)
    listo = doc(db, actor, "Listo", REQUISITOS, version="2")
    assert indexacion_service._ids_a_reindexar(db, solo_pendientes=True) == [pendiente.id]
    assert set(indexacion_service._ids_a_reindexar(db, solo_pendientes=False)) == {pendiente.id, listo.id}
    indexacion_service._trabajo.update(estado="ejecutando")
    try:
        with pytest.raises(ErrorNegocio) as e:
            indexacion_service.iniciar_reindexado(db)
        assert e.value.codigo == "trabajo_en_curso"
    finally:
        indexacion_service._trabajo.update(estado="inactivo")


# ======================= cliente de embeddings =======================
class Respuesta:
    def __init__(self, status=200, datos=None):
        self.status_code, self._datos = status, datos or {}

    def json(self):
        return self._datos


def cliente_con(monkeypatch, respuesta=None, error=None, capturar=None):
    def falso_post(url, json=None, timeout=None):
        if capturar is not None:
            capturar.append(json)
        if error:
            raise error
        return respuesta if respuesta is not None else Respuesta(datos={"embeddings": [[0.0] * 768 for _ in json["input"]]})

    monkeypatch.setattr(httpx, "post", falso_post)
    return OllamaEmbeddings()


def test_el_cliente_aplica_el_formato_de_prompt_de_embeddinggemma(monkeypatch):
    enviado = []
    c = cliente_con(monkeypatch, capturar=enviado)
    c.embed_consulta("¿horario?")
    c.embed_documentos(["texto del documento"])
    assert enviado[0]["input"] == ["task: search result | query: ¿horario?"] and enviado[1]["input"] == ["title: none | text: texto del documento"]
    monkeypatch.setattr(get_settings(), "embedding_usar_prompts", False)
    enviado.clear()
    c.embed_consulta("¿horario?")
    assert enviado[0]["input"] == ["¿horario?"]


def test_el_cliente_agrupa_en_lotes(monkeypatch):
    enviado = []
    monkeypatch.setattr(get_settings(), "embedding_batch_size", 4)
    cliente_con(monkeypatch, capturar=enviado).embed_documentos([f"t{i}" for i in range(10)])
    assert [len(x["input"]) for x in enviado] == [4, 4, 2]


@pytest.mark.parametrize("respuesta, error, codigo", [
    (Respuesta(datos={"embeddings": [[0.1, 0.2, 0.3]]}), None, "dimension_incorrecta"),
    (Respuesta(404), None, "embeddings_no_disponible"),
    (Respuesta(500), None, "embeddings_no_disponible"),
    (Respuesta(datos={"embeddings": []}), None, "embeddings_no_disponible"),
    (None, httpx.ConnectError("sin conexión"), "embeddings_no_disponible"),
    (None, httpx.ReadTimeout("lento"), "embeddings_no_disponible"),
])
def test_el_cliente_traduce_los_errores(monkeypatch, respuesta, error, codigo):
    with pytest.raises(ErrorNegocio) as e:
        cliente_con(monkeypatch, respuesta, error).embed_consulta("hola")
    assert e.value.codigo == codigo


def test_el_error_de_modelo_no_instalado_dice_como_instalarlo(monkeypatch):
    with pytest.raises(ErrorNegocio) as e:
        cliente_con(monkeypatch, Respuesta(404)).embed_consulta("hola")
    assert "ollama pull" in e.value.mensaje


# ======================= API =======================
def test_api_de_busqueda_y_permisos(client, db, actor):
    doc(db, actor, "Política de citas", CANCELAR)
    h = cabecera(client, "gestor@ejemplo.com")
    r = client.post(f"{A}/rag/buscar", headers=h, json={"consulta": "cancelar cita médica con anticipación"})
    assert r.status_code == 200 and r.json()["suficiente"] is True and r.json()["resultados"][0]["titulo"] == "Política de citas"
    assert r.json()["umbral"] == get_settings().rag_umbral_similitud
    sin = client.post(f"{A}/rag/buscar", headers=h, json={"consulta": "receta tradicional de arepas con queso"}).json()
    assert sin["suficiente"] is False and sin["resultados"] == []
    crear_usuario(db, email="rec@ejemplo.com", rol=Rol.RECEPCIONISTA)
    assert client.post(f"{A}/rag/buscar", headers=cabecera(client, "rec@ejemplo.com"), json={"consulta": "cancelar"}).status_code == 403
    assert client.post(f"{A}/rag/buscar", json={"consulta": "cancelar"}).status_code == 401


def test_la_auditoria_de_busquedas_no_guarda_lo_que_se_pregunto(client, db, actor):
    h = cabecera(client, "gestor@ejemplo.com")
    client.post(f"{A}/rag/buscar", headers=h, json={"consulta": "mi cédula 1023456789 y mi correo ana@ejemplo.com"})
    r = db.scalars(select(AuditoriaLog).where(AuditoriaLog.accion == "rag_busqueda")).one()
    assert set(r.detalle) == {"resultados", "suficiente"}


def test_api_de_estado_e_indexado_manual(client, db, actor):
    d = doc(db, actor, "Doc", CANCELAR, indexar=False)
    h = cabecera(client, "gestor@ejemplo.com")
    est = client.get(f"{A}/rag/estado", headers=h).json()
    assert est["modelo"] == get_settings().embedding_model and est["dimensiones"] == 768 and est["dimension_columna"] == 768 and est["dimension_consistente"] is True
    assert est["documentos"]["pendientes"] == 1 and est["requiere_reindexar"] is True
    assert client.post(f"{A}/rag/documentos/{d.id}/indexar", headers=h).json() == {"fragmentos_calculados": 1}
    assert client.get(f"{A}/rag/estado", headers=h).json()["requiere_reindexar"] is False
    assert client.post(f"{A}/rag/documentos/999999/indexar", headers=h).status_code == 404


def test_api_si_los_embeddings_caen_la_busqueda_responde_503(client, db, actor, embedder_falso):
    h = cabecera(client, "gestor@ejemplo.com")
    embedder_falso.caido = True
    r = client.post(f"{A}/rag/buscar", headers=h, json={"consulta": "cancelar cita"})
    assert r.status_code == 503 and r.json()["error"]["codigo"] == "embeddings_no_disponible"


# ======================= con el modelo real de Ollama =======================
@pytest.fixture
def ollama_real():
    if not indexacion_service._ollama_ok():
        pytest.skip("Ollama no está disponible")
    try:
        cliente.OllamaEmbeddings().embed_consulta("prueba")
    except ErrorNegocio as e:
        pytest.skip(f"El modelo de embeddings no responde: {e.mensaje}")


CORPUS_REAL = {
    "Cancelación y reprogramación": CANCELAR,
    "Requisitos de medicina general": REQUISITOS,
    "Horario de atención": HORARIO,
    "Qué hacer en una urgencia": "Si presenta dificultad para respirar, dolor fuerte en el pecho o sangrado abundante, diríjase de inmediato al servicio de urgencias.",
    "Entrega de resultados": "Los resultados de los exámenes de laboratorio se entregan en tres días hábiles en la ventanilla de resultados.",
}


@pytest.mark.ollama
def test_modelo_real_dimension_y_orden_semantico(ollama_real):
    c = cliente.OllamaEmbeddings()
    q, d_bueno, d_malo = c.embed_consulta("¿cómo cancelo mi cita?"), c.embed_documentos([CANCELAR])[0], c.embed_documentos([HORARIO])[0]
    assert len(q) == get_settings().embedding_dimensions == 768
    coseno = lambda a, b: sum(x * y for x, y in zip(a, b)) / ((sum(x * x for x in a) ** 0.5) * (sum(y * y for y in b) ** 0.5))  # noqa: E731
    assert coseno(q, d_bueno) > coseno(q, d_malo)


@pytest.mark.ollama
@pytest.mark.parametrize("consulta, esperado", [
    ("¿Cómo cancelo mi cita?", "Cancelación y reprogramación"), ("necesito mover mi cita para otro día", "Cancelación y reprogramación"),
    ("¿Qué papeles debo llevar a medicina general?", "Requisitos de medicina general"), ("¿atienden los domingos?", "Horario de atención"),
    ("tengo dolor en el pecho y me falta el aire", "Qué hacer en una urgencia"), ("¿cuándo me entregan los exámenes de sangre?", "Entrega de resultados"),
])
def test_modelo_real_recupera_la_fuente_correcta(db, actor, ollama_real, consulta, esperado):
    for i, (titulo, texto) in enumerate(CORPUS_REAL.items()):
        doc(db, actor, titulo, texto, version=str(i))
    r = bs.buscar(db, consulta)
    assert r.suficiente and r.resultados[0].titulo == esperado, [(x.titulo, x.score) for x in r.resultados]


@pytest.mark.ollama
@pytest.mark.parametrize("consulta", ["¿Cuál es la capital de Francia?", "receta de arepas de queso", "¿quién ganó el mundial de 2014?", "explícame cómo funciona un motor de combustión"])
def test_modelo_real_no_inventa_fuentes_para_temas_ajenos(db, actor, ollama_real, consulta):
    for i, (titulo, texto) in enumerate(CORPUS_REAL.items()):
        doc(db, actor, titulo, texto, version=str(i))
    r = bs.buscar(db, consulta)
    assert r.suficiente is False and r.resultados == [], (consulta, r.mejor_score)


@pytest.mark.ollama
def test_modelo_real_el_filtro_de_sede_excluye_lo_que_no_aplica(db, actor, ollama_real):
    a, b = crear_base(db), crear_base(db)
    doc(db, actor, "Horario sede A", HORARIO, sede_id=a.sede.id, version="1")
    doc(db, actor, "Horario general", HORARIO, version="2")
    assert sorted(titulos(bs.buscar(db, "¿atienden los domingos?", sede_id=b.sede.id))) == ["Horario general"]
