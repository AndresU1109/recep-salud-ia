"""Reglas DETERMINISTAS del asistente. Lo crítico para la seguridad del paciente no depende de que el modelo de lenguaje
"se acuerde" de las instrucciones: emergencias, orientación médica y pedir hablar con una persona se resuelven aquí, antes del LLM."""
import re
import unicodedata


def normalizar(texto: str) -> str:
    """Minúsculas, sin tildes y con espacios simples: 'DOLOR  de Pecho' → 'dolor de pecho'."""
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", t)).strip()


# ---------- emergencias ----------
_EMERGENCIA = [
    r"dolor (fuerte |intenso |muy fuerte )?(de|en el|en mi|del) pecho", r"dolor de pecho", r"opresion (en el )?pecho", r"infarto", r"ataque (al|del) corazon",
    r"no (puedo|puede|puedes|logro|logra|consigo|consigue) respirar", r"(me )?falta (mucho )?el aire", r"dificultad (para|al) respirar", r"(me )?ahogo", r"se (esta )?ahogando",
    r"(se )?desmay(o|e|ado|a|an)", r"perdio (el )?(conocimiento|la conciencia)", r"inconsciente", r"no (reacciona|despierta|responde)\b",
    r"convulsion", r"convulsiona", r"sangrado (muy )?(abundante|fuerte|que no para)", r"no (para|deja) de sangrar", r"hemorragia",
    r"(derrame|embolia|trombosis)", r"paralisis", r"(no|ya no) (puedo|siento) (mover|el brazo|la cara)", r"cara (caida|torcida)",
    r"sobredosis", r"envenen", r"intoxicacion", r"(me|se) (esta|estoy) muriendo", r"me voy a morir", r"accidente grave", r"(me )?atropell", r"quemadura (grave|extensa)",
    r"reaccion alergica (grave|fuerte)", r"(se )?hincho (la )?(cara|lengua|garganta)", r"parto", r"(estoy|esta) (en )?labor de parto", r"rompi(o|i) fuente",
    r"(quiero|voy a|pienso en|ganas de) (suicidar|quitarme la vida|matarme|morir)", r"suicid", r"hacer(me|le)? dano",
]
_RE_EMERGENCIA = re.compile("|".join(f"(?:{p})" for p in _EMERGENCIA))
# Quien PREGUNTA por el servicio de urgencias (ubicación, horario) no está en una emergencia.
_NEGACION = re.compile(r"\b(no es (una )?emergencia|sin emergencia|nunca|antes tuve|hace (meses|anos)|el (protocolo|servicio) de urgencias|donde (queda|esta) urgencias|horario de urgencias)\b")


def es_emergencia(texto: str) -> bool:
    t = normalizar(texto)
    return bool(_RE_EMERGENCIA.search(t)) and not _NEGACION.search(t)


MENSAJE_EMERGENCIA = ("Lo que me cuenta puede ser una emergencia. Por favor llame ahora mismo a la línea de emergencias 123 o diríjase de inmediato al servicio de urgencias "
                      "más cercano; si hay alguien con usted, pídale ayuda. No espere una cita. Yo no puedo atender emergencias por este medio.")


# ---------- orientación médica (no diagnosticar ni recetar) ----------
_MEDICA = [
    r"que (me )?(puedo|debo|debe|puede|recomiendas|recomienda|sugiere|sugieres) (tomar|aplicar|usar|hacer|ponerme)", r"que (medicamento|pastilla|remedio|jarabe|antibiotico|analgesico)",
    r"(puedo|debo|puede) tomar", r"cuantas? (pastillas?|miligramos|gotas)", r"\bdosis\b", r"receta (medica|de medicamentos)", r"\brecet(ar|ame|en|e|es)\b", r"formul(a|ar|ame) (medica|un medicamento)",
    r"que (enfermedad|tengo|puede ser|tendre|sera)", r"(tengo|sera|puede ser) (diabetes|cancer|covid|gripa|infeccion|alergia|asma|hipertension)", r"diagnostic",
    r"es (grave|normal|peligroso) (si|que|mi)", r"sintomas? de", r"estoy enferm[oa] de", r"(me )?duele (la |el |mi )?(cabeza|estomago|garganta|espalda|muela|oido|barriga)",
    r"tengo (fiebre|tos|dolor|nauseas|mareo|diarrea|vomito|migrana|gripa|gripe)", r"mi (hijo|hija|bebe|mama|papa) tiene (fiebre|tos|dolor|diarrea)",
]
_RE_MEDICA = re.compile("|".join(f"(?:{p})" for p in _MEDICA))


def pide_orientacion_medica(texto: str) -> bool:
    return bool(_RE_MEDICA.search(normalizar(texto)))


MENSAJE_SIN_ORIENTACION_MEDICA = ("Lamento que se sienta así, pero yo soy una asistente administrativa y no puedo dar orientación médica, diagnósticos ni indicar medicamentos. "
                                  "Un profesional de salud puede valorarlo: si quiere, le ayudo a agendar una cita. Si los síntomas son fuertes o empeoran, acuda a urgencias o llame al 123.")


# ---------- pedir a una persona ----------
_OBJETO = r"(persona|humano|funcionari[oa]|asesor[a]?|operador[a]?|recepcionista|encargad[oa]|secretari[oa]|alguien de (la )?(institucion|clinica|hospital|recepcion))"
_VERBO = r"(habl\w+|comun\w+|pas(a|ar|e)\w*|transfi\w+|atienda\w*|atender\w*|contact\w+|quiero|necesito|prefiero)"
_HUMANO = re.compile(rf"\b{_VERBO}( \w+){{0,4}} {_OBJETO}\b")
_HUMANO_2 = re.compile(r"\b(persona real|ser humano|agente real|no (quiero|me gusta) hablar con (un |una )?(robot|maquina|asistente|bot|computador))\b")
_HUMANO_3 = re.compile(r"\b(comun\w+|pas(a|ame|arme|e|eme)|transfi\w+|habl\w+) (\w+ ){0,3}(con|a) (la )?recepcion\b")  # "comuníqueme con recepción"


def pide_humano(texto: str) -> bool:
    t = normalizar(texto)
    return bool(_HUMANO.search(t) or _HUMANO_2.search(t) or _HUMANO_3.search(t))


# ---------- confirmaciones ----------
# Una confirmación es SOLO una afirmación, con a lo sumo una palabra de cortesía: "sí pero a las tres" NO confirma nada.
_AFIRMA = (r"(si|sii+|claro|correcto|confirmo|confirmado|confirmada|confirmela|confirmelo|confirmalo|confirmame|dale|de acuerdo|ok|okay|listo|perfecto|exacto|adelante|hagale|hagalo|hagala|"
           r"agendela|agendalo|agendamela|cancelela|cancelelo|cancelamela|reprogramela|reprogramelo|asi es|esta bien|esta correcto|procede|proceda|procedamos|afirmativo|cierto|vale|por favor)")
_CORTESIA = r"(por favor|gracias|claro|confirmo|confirmela|confirmelo|confirmalo|hagalo|hagala|agendela|agendalo|cancelela|cancelelo|correcto|adelante|dale|si|perfecto|listo|proceda)"
_SI = re.compile(rf"^{_AFIRMA}( {_CORTESIA}){{0,2}}$")
_NO = re.compile(r"^(no|nop|nel|negativo|mejor no|cancela(r)? eso|olvidalo|dejalo asi|espera|esperate|alto|detente|todavia no)( (gracias|por ahora|por favor|mejor no))?$")


def es_afirmativo(texto: str) -> bool:
    return bool(_SI.match(normalizar(texto)))


def es_negativo(texto: str) -> bool:
    return bool(_NO.match(normalizar(texto)))


def solo_cortesia(texto: str) -> bool:
    """Saludos y agradecimientos: no hace falta buscar en los documentos."""
    palabras = ("hola buenas buenos dias dia tardes noches gracias muchas mil adios chao hasta luego eso es todo nada mas ok listo perfecto genial excelente amable por favor").split()
    return bool(re.fullmatch(rf"(?:{'|'.join(palabras)})(?: (?:{'|'.join(palabras)}))*", normalizar(texto)))
