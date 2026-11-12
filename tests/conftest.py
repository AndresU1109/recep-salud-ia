"""Pruebas contra un PostgreSQL real: se crea una base propia con las migraciones y se borra al final."""
import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings

# Un nombre distinto por proceso: dos ejecuciones de pytest a la vez no se borran la base una a la otra.
NOMBRE_BD_PRUEBAS = f"recep_salud_test_{os.getpid()}"


def _url_admin_y_pruebas():
    base = make_url(get_settings().database_url)
    return base.set(database="postgres"), base.set(database=NOMBRE_BD_PRUEBAS)


def crear_bd_temporal(nombre: str):
    """Crea una base vacía y devuelve su URL; el llamador la elimina con eliminar_bd."""
    admin, _ = _url_admin_y_pruebas()
    eng = create_engine(admin, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{nombre}"'))
    eng.dispose()
    return admin.set(database=nombre)


def eliminar_bd(nombre: str) -> None:
    admin, _ = _url_admin_y_pruebas()
    eng = create_engine(admin, isolation_level="AUTOCOMMIT")
    with eng.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{nombre}" WITH (FORCE)'))
    eng.dispose()


def migrar(url, destino: str = "head", origen_downgrade: bool = False) -> None:
    cfg = Config("alembic.ini")
    cfg.attributes["url"] = url.render_as_string(hide_password=False)
    (command.downgrade if origen_downgrade else command.upgrade)(cfg, destino)


class EmbedderFalso:
    """Embeddings deterministas por palabras (bolsa de palabras hasheada): textos con palabras en común se parecen.
    Sirve para probar la lógica sin Ollama; la calidad semántica real se mide en las pruebas marcadas `ollama`."""

    def __init__(self):
        self.caido = False
        self.llamadas = 0

    @staticmethod
    def _vector(texto: str) -> list[float]:
        import hashlib
        import math
        import re

        dim = get_settings().embedding_dimensions
        v = [0.0] * dim
        for palabra in re.findall(r"\w+", texto.lower()):
            if len(palabra) < 4:  # palabras vacías ("de", "la", "que"...) no aportan significado
                continue
            h = hashlib.sha256(palabra.encode()).digest()
            for i in range(0, 24, 3):
                v[int.from_bytes(h[i:i + 2], "big") % dim] += 1.0 if h[i + 2] % 2 else -1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norma for x in v]

    def _revisar(self):
        from app.core.errores import ErrorNegocio

        self.llamadas += 1
        if self.caido:
            raise ErrorNegocio("embeddings_no_disponible", "El servicio de embeddings no está disponible: prueba.", 503)

    def embed_documentos(self, textos):
        self._revisar()
        return [self._vector(t) for t in textos]

    def embed_consulta(self, consulta):
        self._revisar()
        return self._vector(consulta)


@pytest.fixture(autouse=True)
def embedder_falso(request, monkeypatch):
    """Todas las pruebas usan embeddings falsos, salvo las marcadas @pytest.mark.ollama."""
    from app.embeddings import cliente

    falso = EmbedderFalso()
    if not request.node.get_closest_marker("ollama"):
        monkeypatch.setattr(cliente, "obtener_embedder", lambda: falso)
    return falso


@pytest.fixture(autouse=True)
def carpeta_de_subidas_temporal(tmp_path, monkeypatch):
    """Los archivos que suben las pruebas no deben quedar en data/uploads."""
    monkeypatch.setattr(get_settings(), "uploads_dir", str(tmp_path / "subidas"))


@pytest.fixture(scope="session")
def engine_pruebas():
    try:
        url = crear_bd_temporal(NOMBRE_BD_PRUEBAS)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL no disponible para pruebas ({type(exc).__name__}). Ejecutar: docker compose up -d db")
    migrar(url)
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    yield engine
    engine.dispose()
    eliminar_bd(NOMBRE_BD_PRUEBAS)


@pytest.fixture
def client(db):
    """Cliente HTTP de la API usando la sesión transaccional de pruebas (todo se revierte al final)."""
    from fastapi.testclient import TestClient

    from app.database.session import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def db(engine_pruebas):
    """Sesión dentro de una transacción que se revierte al terminar la prueba."""
    conn = engine_pruebas.connect()
    trans = conn.begin()
    session = Session(bind=conn, autoflush=False, join_transaction_mode="create_savepoint")  # igual que producción
    yield session
    session.close()
    trans.rollback()
    conn.close()
