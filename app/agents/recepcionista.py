"""La recepcionista virtual. Un turno = reglas de seguridad deterministas → confirmaciones → pasos determinísticos (identidad,
cancelar por código, elegir una opción) → consulta previa de datos (catálogo, disponibilidad, documentos) → LLM que redacta y
conversa → guardas de salida.

Diseño medido con Gemma 4 e4b: el modelo pequeño no es fiable para extraer datos ni decidir cuándo llamar herramientas
(copia los ejemplos, dice «¿lo confirma?» sin llamar la herramienta, afirma haber identificado a alguien que no verificó).
Por eso lo crítico lo hace el código; el LLM interpreta lo ambiguo, redacta con naturalidad y puede pedir herramientas;
el backend valida y ejecuta."""
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import entendimiento as nlu
from app.agents import prompts, seguridad
from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.llm import cliente as cliente_llm
from app.llm.tipos import LlamadaHerramienta, Mensaje
from app.models import Conversacion, Especialidad, Sede
from app.models.enums import CanalConversacion, EstadoConversacion, RolMensaje
from app.services import busqueda_service, cita_service, config_ia_service, conversacion_service, encuesta_service, institucion_service, politica_service
from app.tools import REGISTRO
from app.tools.contexto import ContextoHerramientas
from app.tools.registro import ResultadoHerramienta

log = logging.getLogger("agente")

MAX_ENTRADA = 800  # caracteres que se leen del paciente
MAX_PASOS = 4  # idas y vueltas con herramientas por turno
MAX_HISTORIAL = 30
MAX_RESULTADO_EN_HISTORIAL = 1200
MAX_SALIDA = 700  # una respuesta más larga no sirve por voz

INTENCION_POR_HERRAMIENTA = {
    "buscar_paciente": "identificacion", "registrar_paciente": "registro_paciente", "consultar_especialidades": "consulta_catalogo", "consultar_profesionales": "consulta_catalogo",
    "consultar_horarios": "consulta_catalogo", "consultar_requisitos": "consulta_catalogo", "consultar_disponibilidad": "consulta_disponibilidad", "agendar_cita": "agendar_cita",
    "consultar_citas": "consultar_citas", "reprogramar_cita": "reprogramar_cita", "cancelar_cita": "cancelar_cita", "consultar_politicas": "consulta_politicas",
    "consultar_inasistencias": "consulta_inasistencias", "crear_solicitud": "solicitud", "transferir_a_funcionario": "transferencia",
}

TRAMITES = {"buscar_paciente", "registrar_paciente", "agendar_cita", "cancelar_cita", "reprogramar_cita", "crear_solicitud", "transferir_a_funcionario"}

PEDIR_IDENTIDAD ="Para ayudarle con sus citas necesito verificar su identidad. ¿Me indica el tipo y número de su documento y su fecha de nacimiento?"
SIN_CAMBIOS = "Todavía no he realizado ningún cambio. ¿Qué desea hacer: agendar, cancelar o reprogramar una cita?"

# Lo que el LLM nunca debería decir; si lo dice, se sustituye la respuesta (defensa en profundidad tras las instrucciones).
_SALIDA_RIESGOSA = re.compile(r"\b\d+\s?(mg|mcg|ml|gramos|miligramos)\b|\b(tome|tomar|t[oó]mese|aplique|apl[ií]quese|inyecte)\b[^.]{0,40}\b(pastilla|tableta|c[aá]psula|gotas?|dosis|acetaminof[eé]n|ibuprofeno|paracetamol|aspirina|antibi[oó]tico)\b"
                              r"|\busted (tiene|padece|sufre) (diabetes|c[aá]ncer|covid|hipertensi[oó]n|una infecci[oó]n|asma)\b|\b(su|el) diagn[oó]stico es\b", re.IGNORECASE)
# Afirmaciones que solo el SISTEMA puede hacer (tras ejecutar algo): si salen del LLM son falsas.
_AFIRMA_HECHO = re.compile(r"qued[oó] (agendad|cancelad|reprogramad|registrad)|ha sido (agendad|cancelad|reprogramad)|\b(he|hemos) (agendado|cancelado|reprogramado|registrado)\b|\bya (agend|cancel|reprogram)[eé]\b|\bqueda(r[aá])? (agendad|cancelad)", re.IGNORECASE)
_AFIRMA_IDENTIDAD = re.compile(r"ya (la|lo) (identifiqu|verifiqu|tengo identificad)|identidad (ya )?(fue |est[aá] |qued[oó] )?(verificad|confirmad)|qued[oó] identificad|ya est[aá] identificad|(la|lo) identifiqu[eé]", re.IGNORECASE)
_PIDE_CONFIRMAR = re.compile(r"[¿]\s*(lo |la )?(confirma|confirmamos|desea confirmar|quiere confirmar|me confirma)\b|\bpor favor conf[ií]rme", re.IGNORECASE)


@dataclass
class Pendiente:
    nombre: str
    argumentos: dict
    resumen: str


@dataclass
class Respuesta:
    texto: str
    intencion: str
    fuentes: list[dict] = field(default_factory=list)
    herramientas: list[str] = field(default_factory=list)
    transferida: bool = False
    emergencia: bool = False
    requiere_confirmacion: bool = False
    en_historial: bool = False  # True si el turno ya dejó su rastro en el historial del modelo (lo hace _con_llm)
    terminar: bool = False  # el paciente dijo que no necesita más ayuda: tras decir esto, la conversación se cierra
    institucion: dict | None = None  # {id, nombre, tipo} cuando en ESTE turno quedó elegida la institución con la que se habla


def _saludo_horario(ahora: datetime) -> str:
    return "Buenos días" if ahora.hour < 12 else "Buenas tardes" if ahora.hour < 19 else "Buenas noches"


def _deletrear(codigo: str) -> str:
    return " ".join(codigo)


_MARCAS_DE_DEMO = re.compile(r"\s*\((?:demo|ficticio|ficticia)\)", re.IGNORECASE)


def _hablado(nombre: str) -> str:
    """Un nombre de catálogo tal como se dice en voz alta: sin marcas internas como «(demo)» o «(ficticia)»."""
    return _MARCAS_DE_DEMO.sub("", nombre).strip()


def _profesional_hablado(nombre: str) -> str:
    n = _hablado(nombre)
    return f"la doctora {n[4:].strip()}" if n.startswith("Dra.") else f"el doctor {n[3:].strip()}" if n.startswith("Dr.") else n


def _lista_hablada(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " y " + items[-1]


def _direccion_hablada(direccion: str) -> str:
    """«Calle 10 # 5-20» se dice «Calle 10 número 5 20»: el símbolo y el guion no se leen bien en voz alta."""
    return re.sub(r"(\d)\s*-\s*(\d)", r"\1 \2", direccion.replace("#", " número ")).replace("  ", " ").strip()


def _limpiar_para_voz(texto: str) -> str:
    texto = _MARCAS_DE_DEMO.sub("", texto)
    t = re.sub(r"[*_#`>~|]+", "", texto)
    t = re.sub(r"^\s*[-•]\s+", "", t, flags=re.MULTILINE)
    t = re.sub(r"\s+", " ", t).strip()
    return t if len(t) <= MAX_SALIDA else t[:MAX_SALIDA].rsplit(" ", 1)[0].rstrip(",;:") + "…"


class Recepcionista:
    def __init__(self, conversacion_id, canal: CanalConversacion, config: config_ia_service.ConfigIA, institucion: str, institucion_id: int | None = None,
                 bienvenida_institucional: str | None = None, elegir_institucion: bool = False):
        self.config, self.institucion = config, _hablado(institucion)  # se dice «Clínica Santa Aurora», no «… (ficticia)»
        self.institucion_id, self.bienvenida_institucional = institucion_id, bienvenida_institucional
        # Con varias instituciones y ninguna indicada, el paciente la elige HABLANDO (por su nombre o por la especialidad que pide).
        self.eligiendo_institucion = elegir_institucion and institucion_id is None
        self.texto_con_especialidad: str | None = None  # lo que dijo al pedir una especialidad antes de elegir institución
        self.pregunto_algo_mas = False  # lo último que dijo el asistente fue «¿le ayudo con algo más?»: un «no» significa que ya terminó
        self.estado_encuesta: str | None = None  # None | "puntaje" | "comentario": la encuesta de satisfacción de antes de despedirse
        self.intentos_encuesta = 0
        self.encuesta_hecha = False
        self.espec_por_confirmar: str | None = None  # «¿se refiere a Ortopedia?»: lo que se entendió a medias y espera su sí o no
        self._pregunta_especialidad: str | None = None
        self.pregunto_institucion = False  # ya se le preguntó con cuál institución desea comunicarse (para no repetir la misma frase)
        self._prefijo = ""
        self._institucion_nueva: dict | None = None
        self.ctx = ContextoHerramientas(db=None, conversacion_id=conversacion_id, canal=canal, institucion_id=institucion_id,
                                        politica_datos_version=config.politica_datos_version)  # type: ignore[arg-type]
        self.historial: list[Mensaje] = []
        self.pendiente: Pendiente | None = None
        self.reprogramando: str | None = None  # código de la cita que el paciente quiere mover
        # Lo que el paciente va diciendo POCO A POCO (por voz casi nunca da todo en una frase): se recuerda entre turnos.
        self.deseo: str | None = None  # "agendar" mientras está pidiendo una cita nueva
        self.espec_deseada: str | None = None
        self.rango_deseado: tuple[date, date] | None = None
        self.franja: str | None = None  # "manana" | "tarde"
        self.ultimo_dia_ofrecido: date | None = None
        self.ident: dict = {}  # tipo, numero, nacimiento reunidos de a poco
        self.numero_por_confirmar: str | None = None  # el número de documento que se le leyó en voz alta y espera su «sí»
        self.pidiendo_identidad = False
        self.fallos_identidad = 0
        self.lista_ofrecida: list[str] = []  # especialidades que se le acaban de leer en voz alta (para «la segunda»)
        self.ultimo_uso = datetime.now()

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(get_settings().timezone)

    # ---------- API pública ----------
    def saludo(self, db: Session, conv: Conversacion) -> Respuesta:
        ahora = datetime.now(self.tz)
        nombre = self.config.nombre_asistente.strip()
        if self.eligiendo_institucion:
            quien = "Soy el asistente virtual" if nombre.lower() == "asistente virtual" else f"Soy {nombre}, asistente virtual"
            texto = f"{_saludo_horario(ahora)}. {quien}. {self._ofrecer_instituciones(db)}"
            conversacion_service.guardar_mensaje(db, conv, RolMensaje.AGENTE, texto, "saludo")
            return Respuesta(texto, "saludo")
        quien = f"Soy el asistente virtual de {self.institucion}" if nombre.lower() == "asistente virtual" else f"Soy {nombre}, asistente virtual de {self.institucion}"
        texto = self.bienvenida_institucional or self.config.mensaje_bienvenida or f"{_saludo_horario(ahora)}. {quien}. ¿En qué puedo ayudarle?"
        conversacion_service.guardar_mensaje(db, conv, RolMensaje.AGENTE, texto, "saludo")
        return Respuesta(texto, "saludo")

    def responder(self, db: Session, conv: Conversacion, texto_usuario: str) -> Respuesta:
        self.ctx.db = db
        self.ultimo_uso = datetime.now()
        try:
            texto = (texto_usuario or "").strip()[:MAX_ENTRADA]
            if not texto:
                return self._cerrar_turno(db, conv, None, Respuesta("No alcancé a entenderle. ¿Podría repetirlo, por favor?", "no_entendido"), texto)
            msg_usuario = conversacion_service.guardar_mensaje(db, conv, RolMensaje.PACIENTE, texto)
            try:
                r = self._turno(db, conv, texto, msg_usuario)
            except ErrorNegocio as e:  # LLM caído, etc.: se responde con honestidad y se ofrece una salida
                db.rollback()
                log.warning("turno fallido codigo=%s", e.codigo)
                r = Respuesta("En este momento tengo un problema técnico para atenderle. Puede intentarlo de nuevo en unos minutos o pedir que una persona de la institución le ayude.", "error_agente")
            return self._cerrar_turno(db, conv, msg_usuario, r, texto)
        finally:
            self.ctx.db = None  # type: ignore[assignment]  # la sesión de BD es de la petición, no del agente

    # ---------- un turno ----------
    def _turno(self, db: Session, conv: Conversacion, texto: str, msg_usuario) -> Respuesta:
        if self.ctx.transferida:
            return Respuesta("Su solicitud ya quedó registrada para que una persona de la institución continúe con su atención. Por este medio no puedo hacer más trámites.", "transferencia", transferida=True)
        if seguridad.es_emergencia(texto):
            return Respuesta(seguridad.MENSAJE_EMERGENCIA, "emergencia", emergencia=True)

        # Cuando el paciente confirma que no necesita más ayuda, la conversación termina (no antes: un «no» suelto solo vale tras «¿le ayudo con algo más?»).
        if self.estado_encuesta:
            return self._encuesta(db, conv, texto)
        if nlu.despide(texto) or (self.pregunto_algo_mas and self.pendiente is None and nlu.sin_mas(texto)):
            self.pendiente = None
            if self.config.encuesta_activa and self.ctx.canal == CanalConversacion.VOZ and not self.encuesta_hecha and not self.ctx.transferida:
                self.estado_encuesta = "puntaje"
                return Respuesta("Con gusto. Antes de despedirnos, ¿cómo calificaría mi atención de uno a cinco, donde cinco es excelente?", "encuesta")
            return self._despedida("Con gusto.")

        if self.pendiente:
            pend, self.pendiente = self.pendiente, None  # una confirmación vale para UN turno: si dicen otra cosa, se olvida
            if seguridad.es_afirmativo(texto):
                return self._ejecutar_confirmada(db, conv, msg_usuario, pend)
            if seguridad.es_negativo(texto):
                return Respuesta("De acuerdo, no hice ningún cambio. ¿Le ayudo con algo más?", INTENCION_POR_HERRAMIENTA.get(pend.nombre, "conversacion"))

        if seguridad.pide_humano(texto):
            res = self._ejecutar(db, conv, msg_usuario, "transferir_a_funcionario", {"motivo": "El paciente pidió hablar con una persona."})
            texto_r = ("Con gusto. Dejé registrada su solicitud para que una persona de la institución continúe con su atención; no puedo prometerle cuánto tardará. "
                       "Por este medio no haré más trámites.") if res.ok else "Lo comunicaré con una persona, pero tuve un problema registrando la solicitud. Por favor llame a la institución."
            return Respuesta(texto_r, "transferencia", herramientas=["transferir_a_funcionario"], transferida=self.ctx.transferida)

        if seguridad.pide_orientacion_medica(texto):
            return Respuesta(seguridad.MENSAJE_SIN_ORIENTACION_MEDICA, "orientacion_medica")

        tipo_sol = nlu.tipo_solicitud(texto)
        if tipo_sol:  # registrar lo que el paciente cuenta con sus palabras es de bajo riesgo y no se deja al criterio del modelo
            res = self._ejecutar(db, conv, msg_usuario, "crear_solicitud", {"tipo": tipo_sol, "descripcion": texto})
            if res.ok:
                nombre = {"QUEJA": "queja", "RECLAMO": "reclamo", "SUGERENCIA": "sugerencia"}[tipo_sol]
                return Respuesta(f"Lamento lo ocurrido. Dejé registrada su {nombre} con el número {res.datos['numero']} para que una persona de la institución la revise. ¿Le ayudo con algo más?",
                                 "solicitud", herramientas=["crear_solicitud"])

        recien_elegida = False
        if self.eligiendo_institucion or (self.institucion_id is not None and nlu.pide_cambiar_institucion(texto)):
            r = self._elegir_institucion(db, conv, texto)
            if r:
                return r
            recien_elegida = True  # quedó elegida: el resto del mensaje (p. ej. «quiero una cita de cardiología») se atiende ya con esa institución
        if self.espec_por_confirmar:  # respuesta a «¿se refiere a …?»
            candidata, self.espec_por_confirmar = self.espec_por_confirmar, None
            if seguridad.es_afirmativo(texto) or nlu.confirma_dato(texto):
                self.deseo, self.espec_deseada, self.lista_ofrecida, recien_elegida = "agendar", candidata, [], True
            elif seguridad.es_negativo(texto):
                self.lista_ofrecida = self._nombres_especialidades(db)
                return Respuesta(f"De acuerdo. ¿Para qué especialidad desea la cita? Tenemos {_lista_hablada([_hablado(e) for e in self.lista_ofrecida])}.", "agendar_cita")
        cambio = self._actualizar_deseo(db, texto) or recien_elegida
        if self._pregunta_especialidad:  # el reconocimiento de voz deformó el nombre: se confirma antes de actuar
            self.espec_por_confirmar, self._pregunta_especialidad = self._pregunta_especialidad, None
            return Respuesta(f"¿Se refiere a {_hablado(self.espec_por_confirmar)}?", "agendar_cita")
        r = self._identidad_guiada(db, conv, msg_usuario, texto)
        if r:
            return r
        r = self._transaccion(db, conv, msg_usuario, texto)
        if r:
            return r
        r = self._guiar_agendamiento(db, conv, msg_usuario, texto, cambio)
        if r:
            return r
        r = self._informar(db, conv, msg_usuario, texto)
        if r:
            return r
        previos: list[Mensaje] = []
        usadas: list[str] = []
        previos += self._precargar(db, conv, msg_usuario, texto, usadas)
        return self._con_llm(db, conv, msg_usuario, texto, previos, usadas)

    # ---------- despedida y encuesta ----------
    def _despedida(self, inicio: str) -> Respuesta:
        donde = f" con {self.institucion}" if self.institucion_id is not None else ""
        return Respuesta(f"{inicio} Gracias por comunicarse{donde}. Que tenga un excelente día. Hasta luego.", "despedida", terminar=True)

    def _encuesta(self, db: Session, conv: Conversacion, texto: str) -> Respuesta:
        """Una pregunta (la calificación) y, solo si fue baja, una más (qué mejorar). Siempre se puede no responder."""
        if self.estado_encuesta == "comentario":
            self.estado_encuesta, self.encuesta_hecha = None, True
            if not (nlu.rechaza_encuesta(texto) or nlu.despide(texto)):
                encuesta_service.comentar(db, conv, texto)
            return self._despedida("Gracias por contármelo, lo tendremos en cuenta.")
        valor = nlu.puntaje(texto)
        if valor is None:
            self.intentos_encuesta += 1
            if nlu.rechaza_encuesta(texto) or self.intentos_encuesta >= 2:
                self.estado_encuesta, self.encuesta_hecha = None, True
                return self._despedida("Está bien, no hay problema.")
            return Respuesta("Disculpe, no le entendí. ¿Me dice un número del uno al cinco, donde cinco es excelente?", "encuesta")
        encuesta_service.registrar(db, conv, valor)
        if valor <= 3:
            self.estado_encuesta = "comentario"
            return Respuesta("Gracias, y lamento no haber estado a la altura. ¿Qué podríamos mejorar? Puede decirlo en una frase, o decir nada.", "encuesta")
        self.estado_encuesta, self.encuesta_hecha = None, True
        return self._despedida("Muchas gracias por su calificación.")

    # ---------- con qué institución se habla (se decide conversando) ----------
    def _ofrecer_instituciones(self, db: Session) -> str:
        nombres = [_hablado(i.nombre) for i in institucion_service.listar(db)]
        return f"Atendemos a {_lista_hablada(nombres)}. ¿Con cuál institución desea comunicarse?" if nombres else "¿En qué puedo ayudarle?"

    def _instituciones_de_especialidad(self, db: Session, texto: str, activas: list) -> tuple[str, list[int]] | None:
        """Si el paciente nombra una especialidad: (cómo se llama, ids de las instituciones que la atienden). None si no nombra ninguna."""
        todas = {i.id for i in activas}
        por_nombre: dict[str, set[int]] = {}
        for nombre, inst in db.execute(select(Especialidad.nombre, Especialidad.institucion_id).where(Especialidad.activa)).all():
            por_nombre.setdefault(_hablado(nombre), set()).update(todas if inst is None else {inst} & todas)
        t = seguridad.normalizar(texto)
        halladas = [n for n in por_nombre if seguridad.normalizar(n) in t]
        halladas = [n for n in halladas if not any(n != m and seguridad.normalizar(n) in seguridad.normalizar(m) for m in halladas)]  # «Medicina» dentro de «Medicina General»
        if not halladas:
            return None
        return halladas[0], sorted(set().union(*(por_nombre[n] for n in halladas)))

    def _elegir_institucion(self, db: Session, conv: Conversacion, texto: str) -> Respuesta | None:
        """Una respuesta (pregunta, aclaración) mientras no quede elegida; None cuando quedó elegida y el resto del mensaje debe atenderse."""
        activas = institucion_service.listar(db)
        cambio = self.institucion_id is not None  # solo llega aquí con una institución ya elegida si pidió cambiar
        if cambio:
            self._soltar_institucion(conv)
        if not activas:
            self.eligiendo_institucion = False
            return None
        if self.deseo is None and nlu.quiere_agendar(texto):
            self.deseo = "agendar"
        esp = self._instituciones_de_especialidad(db, texto, activas)
        t = seguridad.normalizar(texto)
        sin_esp = t.replace(seguridad.normalizar(esp[0]), " ") if esp else t  # «cita de odontología» nombra una especialidad, no al Centro Odontológico
        elegida = nlu.institucion_nombrada(sin_esp, [(i.id, _hablado(i.nombre), i.tipo) for i in activas])
        via_especialidad = None
        if esp:
            self.deseo, self.texto_con_especialidad = "agendar", texto
        if elegida is None and esp:
            nombre_esp, ids = esp
            if not ids:
                return Respuesta(f"No tengo {nombre_esp} en ninguna de nuestras instituciones. {self._ofrecer_instituciones(db)}", "consulta_catalogo")
            if len(ids) > 1:
                nombres = _lista_hablada([_hablado(i.nombre) for i in activas if i.id in ids])
                return Respuesta(f"{nombre_esp} la atienden {nombres}. ¿Con cuál desea comunicarse?", "seleccion_institucion")
            elegida, via_especialidad = ids[0], nombre_esp
        if elegida is None:
            pide_algo = self.deseo or nlu.consulta_catalogo(texto) or nlu.pregunta_sedes(texto) or cambio
            intro = "Con gusto. Primero necesito saber con cuál institución desea comunicarse. " if pide_algo and not self.pregunto_institucion else "Disculpe, no identifiqué la institución. "
            self.pregunto_institucion = True
            return Respuesta(intro + self._ofrecer_instituciones(db), "seleccion_institucion")
        inst = next(i for i in activas if i.id == elegida)
        self._fijar_institucion(db, conv, inst)
        nombre = _hablado(inst.nombre)
        if self.texto_con_especialidad and not via_especialidad and not esp:
            self._actualizar_deseo(db, self.texto_con_especialidad)  # la especialidad que pidió antes de decir la institución
        self.texto_con_especialidad = None
        if not (self.deseo or nlu.consulta_catalogo(texto) or nlu.pregunta_sedes(texto)):
            return Respuesta(f"Perfecto, le atiende {nombre}. ¿En qué puedo ayudarle?", "seleccion_institucion")
        self._prefijo = f"Para {via_especialidad}, le atiende {nombre}." if via_especialidad else f"Con gusto, le atiende {nombre}."
        return None

    def _fijar_institucion(self, db: Session, conv: Conversacion, inst) -> None:
        self.institucion_id, self.institucion, self.bienvenida_institucional = inst.id, _hablado(inst.nombre), inst.mensaje_bienvenida
        self.eligiendo_institucion = False
        self.ctx.institucion_id = inst.id
        conv.institucion_id = inst.id
        self.lista_ofrecida, self.ctx.ultima_oferta, self.ctx.cupos, self.espec_deseada = [], [], {}, None
        self._institucion_nueva = {"id": inst.id, "nombre": _hablado(inst.nombre), "tipo": inst.tipo}
        db.commit()

    def _soltar_institucion(self, conv: Conversacion) -> None:
        self.institucion_id, self.institucion, self.bienvenida_institucional = None, "", None
        self.eligiendo_institucion, self.pregunto_institucion = True, False
        self.ctx.institucion_id = None
        conv.institucion_id = None
        self.lista_ofrecida, self.ctx.ultima_oferta, self.ctx.cupos, self.espec_deseada = [], [], {}, None

    # ---------- pasos deterministas ----------
    # ---------- lo que el paciente va diciendo, de a poco ----------
    def _reiniciar_deseo(self) -> None:
        self.deseo = self.espec_deseada = self.rango_deseado = self.franja = self.ultimo_dia_ofrecido = None

    def _nombres_especialidades(self, db: Session) -> list[str]:
        return [e.nombre for e in db.scalars(select(Especialidad).where(Especialidad.activa, institucion_service.alcance(Especialidad.institucion_id, self.institucion_id)).order_by(Especialidad.nombre))]

    def _mencion_especialidad(self, db: Session, texto: str) -> str | None:
        """La especialidad que el paciente nombra. Se compara con el nombre COMO SE DICE: «medicina general», no «Medicina General (demo)»."""
        nombres = self._nombres_especialidades(db)
        dicho = nlu.mencion(texto, [_hablado(n) for n in nombres])
        return next((n for n in nombres if _hablado(n) == dicho), None) if dicho else None

    def _actualizar_deseo(self, db: Session, texto: str) -> bool:
        """Recuerda especialidad, día y franja que el paciente va dando para su cita. True si algo cambió en este turno."""
        cambio = False
        if self.deseo is None and not self.reprogramando and nlu.quiere_agendar(texto):
            self.deseo, cambio = "agendar", True
        if self.deseo != "agendar":
            return cambio
        especialidad = self._mencion_especialidad(db, texto)
        if not especialidad and not self.espec_deseada and self.lista_ofrecida:  # «la segunda», dicho tras oírle la lista de especialidades
            idx = nlu.elegir_ordinal(texto, len(self.lista_ofrecida))
            especialidad = self.lista_ofrecida[idx] if idx is not None else None
        if especialidad and especialidad != self.espec_deseada:
            self.espec_deseada, self.lista_ofrecida, cambio = especialidad, [], True
        if not especialidad and not self.espec_deseada and not self.pidiendo_identidad and not self.ident:
            nombres = self._nombres_especialidades(db)
            aprox = nlu.mencion_aproximada(texto, [_hablado(n) for n in nombres])
            self._pregunta_especialidad = next((n for n in nombres if _hablado(n) == aprox), None) if aprox else None
        rango = nlu.rango_de_fechas(texto, politica_service.hoy_local())
        if rango and rango != self.rango_deseado:
            self.rango_deseado, self.ultimo_dia_ofrecido, cambio = rango, None, True
        if nlu.pide_horarios(texto):  # «quiero ver las fechas disponibles»: se le muestran (sin pedirle que invente una fecha)
            cambio = True
        franja = nlu.franja(texto)
        if franja and franja != self.franja:
            self.franja, cambio = franja, True
        return cambio

    # ---------- identidad por partes (por voz casi nunca llega todo en una frase) ----------
    def _identidad_guiada(self, db, conv, msg_usuario, texto: str) -> Respuesta | None:
        """Reúne tipo, número y fecha de nacimiento a lo largo de varios turnos. El número se lee en voz alta para que el paciente lo corrija
        (el reconocimiento de voz se equivoca con dígitos repetidos); cuando están los tres, se verifica sin depender del modelo."""
        if self.ctx.identidad_verificada:
            return None
        tipo, numero, nacimiento = nlu.extraer_tipo_documento(texto), nlu.extraer_numero_documento(texto), nlu.extraer_fecha_nacimiento(texto, politica_service.hoy_local())
        confirmo = False
        if self.numero_por_confirmar:
            leido, self.numero_por_confirmar = self.numero_por_confirmar, None
            if numero is None:
                if nlu.confirma_dato(texto) or seguridad.es_afirmativo(texto):
                    self.ident["numero"], confirmo = leido, True
                elif seguridad.es_negativo(texto) or not (tipo or nacimiento):
                    return Respuesta("Disculpe. Dígame de nuevo su número de documento, despacio y dígito por dígito.", "identificacion")
        if not (tipo or numero or nacimiento or confirmo):
            return None
        if not (self.pidiendo_identidad or self.deseo or self.ident or confirmo) and not (tipo and numero and nacimiento):
            return None  # un dato suelto fuera de contexto (p. ej. una fecha dentro de otra pregunta) no es un intento de identificarse
        self.pidiendo_identidad = True
        if tipo:
            self.ident["tipo"] = tipo
        if nacimiento:
            self.ident["nacimiento"] = nacimiento
        if numero:
            if self.ctx.canal == CanalConversacion.VOZ and nacimiento is None:
                self.numero_por_confirmar = numero  # el número llegó solo: se le lee para que lo corrija antes de seguir (el reconocimiento de voz falla con los dígitos)
                return Respuesta(f"Entendí el documento {' '.join(numero)}. ¿Es correcto?", "identificacion")
            self.ident["numero"] = numero
        faltan = [k for k in ("tipo", "numero", "nacimiento") if k not in self.ident]
        if faltan:
            if "tipo" in faltan and "numero" in faltan:
                texto_pedido = "Dígame el tipo y número de su documento."
            else:
                texto_pedido = {"tipo": "¿Su documento es cédula de ciudadanía, tarjeta de identidad u otro?", "numero": "¿Cuál es el número de su documento?",
                                "nacimiento": "Ahora dígame su fecha de nacimiento: día, mes y año."}[faltan[0]]
            return Respuesta(f"Gracias. {texto_pedido}", "identificacion")
        return self._verificar(db, conv, msg_usuario)

    def _verificar(self, db, conv, msg_usuario) -> Respuesta:
        ident, self.ident = self.ident, {}
        res = self._ejecutar(db, conv, msg_usuario, "buscar_paciente", {"tipo_documento": ident["tipo"], "numero_documento": ident["numero"], "fecha_nacimiento": ident["nacimiento"].isoformat()})
        if res.ok:
            self.pidiendo_identidad, self.fallos_identidad = False, 0
            return self._tras_identificar(db, conv, msg_usuario)
        self.fallos_identidad += 1
        if res.codigo_error == "verificacion_bloqueada":
            return Respuesta("Por seguridad no puedo seguir verificando ese documento por ahora. Puede intentarlo más tarde o pedir que una persona de la institución le ayude.", "identificacion", herramientas=["buscar_paciente"])
        ayuda = " Si lo prefiere, puedo pasarlo con una persona de la institución." if self.fallos_identidad >= 2 else ""
        return Respuesta("No pude verificar su identidad con esos datos. Intentemos de nuevo: dígame el tipo y número de su documento y su fecha de nacimiento." + ayuda, "identificacion", herramientas=["buscar_paciente"])

    def _tras_identificar(self, db, conv, msg_usuario) -> Respuesta:
        saludo = f"Gracias, {self.ctx.primer_nombre}. Ya verifiqué su identidad."
        if self.ctx.cupo_elegido:  # había elegido un horario antes de identificarse: se retoma
            cupo, self.ctx.cupo_elegido = self.ctx.cupo_elegido, None
            r = self._proponer(db, conv, msg_usuario, "agendar_cita", {"cupo": cupo})
            r.texto, r.herramientas = f"{saludo} {r.texto}", ["buscar_paciente", *r.herramientas]
            return r
        if self.deseo == "agendar":
            r = self._continuar_agendamiento(db, conv, msg_usuario)
            r.texto, r.herramientas = f"{saludo} {r.texto}", ["buscar_paciente", *r.herramientas]
            return r
        return Respuesta(f"{saludo} ¿En qué le puedo ayudar?", "identificacion", herramientas=["buscar_paciente"])

    # ---------- agendar conversando ----------
    def _guiar_agendamiento(self, db, conv, msg_usuario, texto: str, cambio: bool) -> Respuesta | None:
        if self.deseo != "agendar" or self.pendiente is not None:
            return None
        if not self.ctx.identidad_verificada:
            if cambio:
                self.pidiendo_identidad = True
                return Respuesta(PEDIR_IDENTIDAD, "agendar_cita")
            return None
        if nlu.pide_otras_opciones(texto) and self.ctx.ultima_oferta:  # «otro día», «más tarde»: se sigue buscando a partir de lo ya ofrecido
            if self.ultimo_dia_ofrecido and not nlu.rango_de_fechas(texto, politica_service.hoy_local()):
                self.rango_deseado = (self.ultimo_dia_ofrecido + timedelta(days=1), self.ultimo_dia_ofrecido + timedelta(days=7))
            cambio = True
        return self._continuar_agendamiento(db, conv, msg_usuario) if cambio else None

    def _continuar_agendamiento(self, db, conv, msg_usuario) -> Respuesta:
        """Pide lo que falta (especialidad) o consulta y OFRECE horarios en voz alta: hasta tres, con profesional y sede."""
        if not self.espec_deseada:
            self.lista_ofrecida = self._nombres_especialidades(db)  # lo que se le dice en voz alta, en ese orden: puede responder «la segunda»
            lista = _lista_hablada([_hablado(e) for e in self.lista_ofrecida])
            return Respuesta(f"¿Para qué especialidad desea la cita? Tenemos {lista}.", "agendar_cita")
        hoy = politica_service.hoy_local()
        desde, hasta = self.rango_deseado or (hoy, hoy + timedelta(days=6))
        args = {"especialidad": self.espec_deseada, "desde": desde.isoformat(), "hasta": hasta.isoformat(), **({"franja": self.franja} if self.franja else {})}
        res = self._ejecutar(db, conv, msg_usuario, "consultar_disponibilidad", args)
        nombre = _hablado(self.espec_deseada)
        if not res.ok:
            return Respuesta(f"No pude consultar los horarios de {nombre}: {res.datos.get('mensaje', 'ocurrió un problema')} ¿Quiere intentar con otra fecha?", "consulta_disponibilidad", herramientas=["consultar_disponibilidad"])
        if not res.datos.get("opciones"):
            self.rango_deseado, self.franja, self.ultimo_dia_ofrecido = None, None, None
            return Respuesta(f"No encontré horarios libres de {nombre} en esas fechas. ¿Quiere que busque en otros días?", "consulta_disponibilidad", herramientas=["consultar_disponibilidad"])
        self.ctx.ultima_oferta = self.ctx.ultima_oferta[:3]  # lo que se dice en voz alta es lo único que se puede elegir
        cupos = [self.ctx.cupos[i] for i in self.ctx.ultima_oferta]
        self.ultimo_dia_ofrecido = cupos[-1].inicio.astimezone(self.tz).date()
        palabras = ["uno", "dos", "tres"]
        ofertas = " ".join(f"Opción {palabras[k]}: {c.texto}, con {_profesional_hablado(c.profesional)}, en {_hablado(c.sede)}." for k, c in enumerate(cupos))
        pie = "¿Cuál prefiere? Puede decir la primera, la segunda, la tercera, o la hora." if len(cupos) > 1 else "¿Le sirve ese horario?"
        return Respuesta(f"Para {nombre} tengo estos horarios. {ofertas} {pie}", "consulta_disponibilidad", herramientas=["consultar_disponibilidad"])

    # ---------- información que se contesta con datos del sistema, no con el modelo ----------
    def _informar(self, db, conv, msg_usuario, texto: str) -> Respuesta | None:
        if nlu.pregunta_sedes(texto):
            sedes = list(db.scalars(select(Sede).where(Sede.activa, institucion_service.alcance(Sede.institucion_id, self.institucion_id)).order_by(Sede.nombre)))
            if not sedes:
                return Respuesta("Por ahora no tengo las sedes registradas. Si quiere, puedo pasarlo con una persona de la institución.", "consulta_catalogo")
            partes = [f"{_hablado(x.nombre)}, en {_direccion_hablada(x.direccion)}" if x.direccion else _hablado(x.nombre) for x in sedes]
            cuantas = {1: "una sede", 2: "dos sedes", 3: "tres sedes", 4: "cuatro sedes"}.get(len(sedes), f"{len(sedes)} sedes")
            return Respuesta(f"Tenemos {cuantas}: {'; '.join(partes)}. ¿Le ayudo a agendar una cita?", "consulta_catalogo")
        pedidas = nlu.consulta_catalogo(texto)
        if "especialidades" in pedidas:
            res = self._ejecutar(db, conv, msg_usuario, "consultar_especialidades", {})
            nombres = [_hablado(n) for n in res.datos.get("especialidades", [])]
            if nombres:
                self.lista_ofrecida = list(res.datos.get("especialidades", []))
                return Respuesta(f"Atendemos {_lista_hablada(nombres)}. ¿Para cuál le gustaría una cita?", "consulta_catalogo", herramientas=["consultar_especialidades"])
        if "profesionales" in pedidas:
            especialidad = self._mencion_especialidad(db, texto) or self.espec_deseada
            if not especialidad:
                return Respuesta("Con gusto. ¿De qué especialidad quiere conocer a los profesionales?", "consulta_catalogo")
            res = self._ejecutar(db, conv, msg_usuario, "consultar_profesionales", {"especialidad": especialidad})
            nombres = [_profesional_hablado(n) for n in res.datos.get("profesionales", [])]
            if nombres:
                return Respuesta(f"En {_hablado(especialidad)} atienden {_lista_hablada(nombres)}. ¿Quiere agendar una cita?", "consulta_catalogo", herramientas=["consultar_profesionales"])
        return None

    def _transaccion(self, db, conv, msg_usuario, texto: str) -> Respuesta | None:
        """Cancelar por código y elegir uno de los horarios ofrecidos: pasos críticos que no se dejan al criterio del modelo."""
        cancelar = nlu.quiere_cancelar(texto)
        codigos = nlu.candidatos_a_codigo(texto)
        if codigos and (cancelar or nlu.quiere_reprogramar(texto)):
            if not self.ctx.identidad_verificada:
                # Sin identidad no se puede consultar la base: solo se actúa si parece un código de verdad (con dígitos; «cancelar» también tiene 8 letras).
                if any(ch.isdigit() for c in codigos for ch in c):
                    return Respuesta(PEDIR_IDENTIDAD, "cancelar_cita" if cancelar else "reprogramar_cita")
                codigos = []
        if codigos and (cancelar or nlu.quiere_reprogramar(texto)):
            propia = None
            for c in codigos:
                try:
                    propia = cita_service.obtener_por_codigo(db, c, solo_paciente_id=self.ctx.paciente_id)
                    break
                except ErrorNegocio:
                    continue
            if propia is None:
                if any(ch.isdigit() for c in codigos for ch in c):
                    return Respuesta("No encontré una cita suya con ese código. ¿Puede verificarlo, o prefiere que consulte sus citas?", "cancelar_cita" if cancelar else "reprogramar_cita")
                return None  # solo palabras de 8 letras (p. ej. «cancelar»), no un código
            if cancelar:
                return self._proponer(db, conv, msg_usuario, "cancelar_cita", {"codigo_reserva": propia.codigo_reserva})
            self.reprogramando = propia.codigo_reserva
            return None  # el modelo pregunta para cuándo; luego la elección de horario la resuelve este método

        if self.ctx.ultima_oferta and not cancelar:
            horas = [(self.ctx.cupos[i].inicio.astimezone(self.tz).hour, self.ctx.cupos[i].inicio.astimezone(self.tz).minute) for i in self.ctx.ultima_oferta]
            idx = nlu.elegir_opcion(texto, horas)
            if idx is not None:
                cupo = self.ctx.ultima_oferta[idx]
                elegido = self.ctx.cupos[cupo].texto
                if not self.ctx.identidad_verificada:
                    self.ctx.cupo_elegido = cupo
                    return Respuesta(f"Perfecto, el horario del {elegido}. Para agendarlo necesito verificar su identidad: ¿me indica el tipo y número de su documento y su fecha de nacimiento?", "agendar_cita")
                if self.reprogramando:
                    return self._proponer(db, conv, msg_usuario, "reprogramar_cita", {"codigo_reserva": self.reprogramando, "nuevo_cupo": cupo})
                return self._proponer(db, conv, msg_usuario, "agendar_cita", {"cupo": cupo})
        return None

    def _proponer(self, db, conv, msg_usuario, nombre: str, argumentos: dict) -> Respuesta:
        """Deja la acción pendiente y pide la confirmación al paciente (nada se ejecuta todavía)."""
        res = self._ejecutar_o_pedir_confirmacion(db, conv, msg_usuario, nombre, argumentos)
        intencion = INTENCION_POR_HERRAMIENTA.get(nombre, "conversacion")
        if res.datos.get("requiere_confirmacion"):
            return Respuesta(f"{res.datos['resumen'].rstrip('.')}. ¿Lo confirma?", intencion, herramientas=[nombre], requiere_confirmacion=True)
        return Respuesta(f"No pude continuar: {res.datos.get('mensaje', 'ocurrió un problema')} ¿Quiere intentar otra cosa?", intencion, herramientas=[nombre])

    def _precargar(self, db, conv, msg_usuario, texto: str, usadas: list[str]) -> list[Mensaje]:
        """Datos de solo lectura que el paciente pide: se consultan ANTES de responder y se le entregan al modelo como un intercambio
        de herramienta REAL justo tras la pregunta (en el prompt de sistema los ignoraba y repetía los ejemplos)."""
        especialidades = self._nombres_especialidades(db)
        nombrada = self._mencion_especialidad(db, texto)
        espec = nombrada or (especialidades[0] if len(especialidades) == 1 else None)
        if not nombrada and self.reprogramando:
            espec = self._especialidad_de_la_cita(db) or espec
        llamadas: list[tuple[str, dict]] = []
        pedidas = nlu.consulta_catalogo(texto)
        if "especialidades" in pedidas:
            llamadas.append(("consultar_especialidades", {}))
        if "profesionales" in pedidas and espec:
            llamadas.append(("consultar_profesionales", {"especialidad": espec}))
        if "horarios" in pedidas and espec:
            llamadas.append(("consultar_horarios", {"especialidad": espec}))
        if "requisitos" in pedidas:
            llamadas.append(("consultar_requisitos", {"especialidad": espec} if espec else {}))
        if "politicas" in pedidas:
            llamadas += [("consultar_politicas", {"tipo": t}) for t in nlu.tipo_politica(texto)]
        if (nlu.quiere_cita(texto) or self.reprogramando) and not nlu.quiere_cancelar(texto):
            rango = nlu.rango_de_fechas(texto, politica_service.hoy_local())
            if rango and espec:
                llamadas.append(("consultar_disponibilidad", {"especialidad": espec, "desde": rango[0].isoformat(), "hasta": rango[1].isoformat()}))
        intercambios: list[Mensaje] = []
        for nombre, args in llamadas:
            res = self._ejecutar(db, conv, msg_usuario, nombre, args)
            usadas.append(nombre)
            intercambios += [Mensaje.asistente("", [LlamadaHerramienta(nombre, args)]), Mensaje.herramienta(nombre, res.para_llm()[:MAX_RESULTADO_EN_HISTORIAL])]
        return intercambios

    def _especialidad_de_la_cita(self, db) -> str | None:
        try:
            cita = cita_service.obtener_por_codigo(db, self.reprogramando, solo_paciente_id=self.ctx.paciente_id)
            return db.get(Especialidad, cita.especialidad_id).nombre
        except ErrorNegocio:
            return None

    # ---------- LLM ----------
    def _con_llm(self, db: Session, conv: Conversacion, msg_usuario, texto: str, previos: list[Mensaje], usadas: list[str]) -> Respuesta:
        fuentes = self._recuperar(db, texto)
        informacion = busqueda_service.construir_contexto(fuentes) if fuentes else (
            "(Ningún documento institucional se relaciona con este mensaje. Si es una pregunta informativa, responde que no tienes esa información y ofrece pasar con una persona; "
            "si es un trámite, sigue con las herramientas.)")
        estado = (f"Identidad verificada: {self.ctx.primer_nombre}." if self.ctx.identidad_verificada else "Identidad NO verificada todavía.")
        sistema = prompts.prompt_sistema(self.config.nombre_asistente, self.institucion, self.ctx.canal.value, estado_identidad=estado, informacion=informacion)
        mensajes = [Mensaje.sistema(sistema), *prompts.ejemplos(), *self.historial, Mensaje.usuario(texto), *previos]
        nuevos: list[Mensaje] = [Mensaje.usuario(texto), *previos]
        llm = cliente_llm.obtener_cliente(self.config.modelo_activo)
        texto_final, intencion = "", None
        try:
            for _ in range(MAX_PASOS):
                resp = llm.chat(mensajes, REGISTRO.esquemas(), self.config.opciones())
                if not resp.llamadas:
                    texto_final = resp.contenido
                    break
                asistente = Mensaje.asistente(resp.contenido, resp.llamadas)
                mensajes.append(asistente), nuevos.append(asistente)
                for llamada in resp.llamadas:
                    res = self._ejecutar_o_pedir_confirmacion(db, conv, msg_usuario, llamada.nombre, llamada.argumentos)
                    usadas.append(llamada.nombre)
                    intencion = intencion or INTENCION_POR_HERRAMIENTA.get(llamada.nombre)
                    m = Mensaje.herramienta(llamada.nombre, res.para_llm()[:MAX_RESULTADO_EN_HISTORIAL], llamada.id)
                    mensajes.append(m), nuevos.append(m)
                if self.ctx.transferida:
                    break
        finally:
            llm.cerrar()
        if self.ctx.transferida:
            texto_final = texto_final or "Dejé registrada su solicitud para que una persona de la institución continúe con su atención."
        texto_final, intencion = self._guardas(texto_final, intencion)
        final = Mensaje.asistente(_limpiar_para_voz(texto_final))
        nuevos.append(final)
        self._recordar(nuevos)
        # Las fuentes documentales se muestran en respuestas informativas; en un trámite (identificar, agendar, cancelar…) no aplican.
        es_tramite = any(u in TRAMITES for u in usadas)
        citadas = [{"documento_id": f.documento_id, "fragmento_id": f.fragmento_id, "titulo": f.titulo, "version": f.version, "fuente": f.fuente, "desactualizado": f.desactualizado,
                    "puntaje": f.score} for f in fuentes] if (fuentes and not es_tramite) else []
        return Respuesta(final.contenido, intencion or ("informacion" if citadas else INTENCION_POR_HERRAMIENTA.get(usadas[0], "conversacion") if usadas else "conversacion"),
                         fuentes=citadas, herramientas=usadas, transferida=self.ctx.transferida, requiere_confirmacion=self.pendiente is not None, en_historial=True)

    def _guardas(self, texto: str, intencion: str | None) -> tuple[str, str | None]:
        """Lo que el LLM dice no puede contradecir lo que el sistema hizo: si lo hace, se sustituye por un texto seguro."""
        if not texto.strip():
            return "No logré completar su solicitud. Si lo desea, puedo pasarlo con una persona de la institución.", intencion
        if _SALIDA_RIESGOSA.search(texto):
            log.warning("salida bloqueada por contener orientación médica")
            return seguridad.MENSAJE_SIN_ORIENTACION_MEDICA, "orientacion_medica"
        if not self.ctx.identidad_verificada and _AFIRMA_IDENTIDAD.search(texto):
            log.warning("salida bloqueada: afirma una identificación que no ocurrió")
            return PEDIR_IDENTIDAD, "identificacion"
        if _AFIRMA_HECHO.search(texto):  # el LLM nunca ejecuta cambios: solo el sistema, tras la confirmación, y con su propio texto
            log.warning("salida bloqueada: afirma un cambio que no se hizo")
            return SIN_CAMBIOS, intencion
        if self.pendiente is None and _PIDE_CONFIRMAR.search(texto):
            log.warning("salida bloqueada: pide confirmar algo que no está pendiente")
            return SIN_CAMBIOS, intencion
        return texto, intencion

    # ---------- herramientas ----------
    def _ejecutar_o_pedir_confirmacion(self, db, conv, msg_usuario, nombre: str, argumentos: dict) -> ResultadoHerramienta:
        h, args, rechazo = REGISTRO.preparar(self.ctx, nombre, argumentos)
        if rechazo:
            conversacion_service.registrar_ejecucion(db, conv, msg_usuario, nombre, rechazo)
            return rechazo
        if h.requiere_confirmacion:
            try:
                resumen = h.resumen(self.ctx, args)
            except ErrorNegocio as e:  # p. ej. cita ajena o cupo no ofrecido: se informa antes de pedir confirmación
                res = ResultadoHerramienta(False, {"ok": False, "error": e.codigo, "mensaje": e.mensaje}, e.codigo, True, parametros={})
                conversacion_service.registrar_ejecucion(db, conv, msg_usuario, nombre, res)
                return res
            self.pendiente = Pendiente(nombre, args.model_dump(mode="json"), resumen)
            return ResultadoHerramienta(False, {"ok": False, "requiere_confirmacion": True, "resumen": resumen,
                                                "instruccion": "Repita el resumen al paciente y pregunte si lo confirma. No ejecute nada más hasta que responda."})
        return self._ejecutar(db, conv, msg_usuario, nombre, argumentos)

    def _ejecutar(self, db, conv, msg_usuario, nombre: str, argumentos: dict) -> ResultadoHerramienta:
        res = REGISTRO.ejecutar(self.ctx, nombre, argumentos)
        conversacion_service.registrar_ejecucion(db, conv, msg_usuario, nombre, res)
        if self.ctx.identidad_verificada and conv.paciente_id != self.ctx.paciente_id:
            conversacion_service.sincronizar_identidad(conv, self.ctx.paciente_id, True, self.ctx.guarda_transcripcion)
            db.commit()
        if self.ctx.transferida:
            conv.estado = EstadoConversacion.TRANSFERIDA
            db.commit()
        return res

    def _ejecutar_confirmada(self, db, conv, msg_usuario, pend: Pendiente) -> Respuesta:
        res = self._ejecutar(db, conv, msg_usuario, pend.nombre, pend.argumentos)
        intencion = INTENCION_POR_HERRAMIENTA.get(pend.nombre, "conversacion")
        d = res.datos
        if res.ok:
            if pend.nombre in ("reprogramar_cita", "cancelar_cita"):
                self.reprogramando = None
            self.ctx.ultima_oferta = []
            if pend.nombre == "agendar_cita":
                self._reiniciar_deseo()
            texto = {
                "agendar_cita": lambda: f"Listo, su cita quedó agendada para el {d['cuando']} con {d['profesional']} en {d['sede']}. Su código de reserva es {_deletrear(d['codigo_reserva'])}. "
                                        "Con ese código puede consultarla, cambiarla o cancelarla. ¿Le ayudo con algo más?",
                "cancelar_cita": lambda: f"Listo, su cita {_deletrear(d['codigo_reserva'])} quedó cancelada. ¿Le ayudo con algo más?",
                "reprogramar_cita": lambda: f"Listo, su cita {_deletrear(d['codigo_reserva'])} quedó para el {d['cuando']} con {d['profesional']} en {d['sede']}. ¿Le ayudo con algo más?",
                "registrar_paciente": lambda: f"Listo, {d['nombre']}, quedó registrado. Ahora puedo ayudarle a agendar una cita. ¿Para qué especialidad la necesita?",
            }.get(pend.nombre, lambda: "Listo, hecho.")()
        elif res.codigo_error == "cupo_no_disponible":
            texto = "Ese horario acaba de ocuparse. ¿Quiere que le consulte otros horarios?"
        else:
            texto = f"No pude completarlo: {d.get('mensaje', 'ocurrió un problema')} ¿Quiere que intentemos otra cosa o prefiere que lo comunique con una persona?"
        return Respuesta(texto, intencion, herramientas=[pend.nombre])

    # ---------- documentos ----------
    def _recuperar(self, db: Session, texto: str) -> list:
        if seguridad.solo_cortesia(texto):
            return []
        try:
            return busqueda_service.buscar(db, texto, k=3, institucion_id=self.institucion_id).resultados
        except ErrorNegocio as e:  # sin embeddings: se sigue sin documentos (el prompt lo dice) en vez de caerse
            db.rollback()
            log.warning("recuperación documental no disponible codigo=%s", e.codigo)
            return []

    # ---------- cierre del turno ----------
    def _recordar(self, nuevos: list[Mensaje]) -> None:
        self.historial.extend(nuevos)
        if len(self.historial) > MAX_HISTORIAL:
            self.historial = self.historial[-MAX_HISTORIAL:]
            while self.historial and self.historial[0].rol != "user":  # el historial nunca empieza a mitad de un intercambio con herramientas
                self.historial.pop(0)

    def _cerrar_turno(self, db: Session, conv: Conversacion, msg_usuario, r: Respuesta, texto: str) -> Respuesta:
        if self._prefijo and not r.terminar:
            r.texto, self._prefijo = f"{self._prefijo} {r.texto}", ""
        r.institucion, self._institucion_nueva = self._institucion_nueva, None
        r.texto = _limpiar_para_voz(r.texto)  # también las respuestas deterministas: sin «(demo)», símbolos ni textos larguísimos
        self.pregunto_algo_mas = not r.terminar and bool(re.search(r"algo m[aá]s\?", r.texto, re.IGNORECASE))
        if msg_usuario is not None:
            conversacion_service.poner_intencion(msg_usuario, r.intencion)
        if not r.en_historial:  # respuestas deterministas (emergencia, confirmaciones…): el modelo debe saber qué pasó en el turno siguiente
            self._recordar([Mensaje.usuario(texto or "…"), Mensaje.asistente(r.texto)])
        msg_asistente = conversacion_service.guardar_mensaje(db, conv, RolMensaje.AGENTE, r.texto, r.intencion)
        if r.fuentes:
            conversacion_service.registrar_fuentes(db, msg_asistente, [SimpleNamespace(fragmento_id=f["fragmento_id"], score=f["puntaje"]) for f in r.fuentes])
        db.commit()
        return r
