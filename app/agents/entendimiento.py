"""Comprensión determinista del lenguaje del paciente: documento, fecha de nacimiento, códigos de reserva, elección de una
opción y qué quiere consultar. Existe porque el modelo pequeño no es fiable para estas extracciones; el LLM se queda con
conversar y redactar. Todo aquí es conservador: ante la duda NO extrae nada y deja que el LLM pregunte."""
import re
from difflib import SequenceMatcher
from datetime import date, datetime, timedelta

from app.agents.seguridad import normalizar

_MESES = {"enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6, "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12}
_DIAS = {"lunes": 0, "martes": 1, "miercoles": 2, "jueves": 3, "viernes": 4, "sabado": 5, "domingo": 6}


# ======================= identidad =======================
_TIPOS = [(r"\bcedula de extranjeria\b|\bc ?e\b", "CE"), (r"\btarjeta de identidad\b|\bt ?i\b", "TI"), (r"\bregistro civil\b|\br ?c\b", "RC"), (r"\bpasaporte\b", "PA"),
          (r"\bppt\b|\bpermiso por proteccion temporal\b", "PPT"), (r"\bcedula\b|\bc ?c\b", "CC")]


def extraer_tipo_documento(texto: str) -> str | None:
    t = normalizar(texto)
    for patron, tipo in _TIPOS:
        if re.search(patron, t):
            return tipo
    return None


_UNIDADES = {"uno": 1, "una": 1, "primero": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9}
_ESPECIALES = {"diez": 10, "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
               "veintiuno": 21, "veintiun": 21, "veintidos": 22, "veintitres": 23, "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27, "veintiocho": 28, "veintinueve": 29}
_DECENAS = {"treinta": 30, "cuarenta": 40, "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80, "noventa": 90}
_HASTA_99 = {**_UNIDADES, **_ESPECIALES, **_DECENAS, **{f"{d} y {u}": dv + uv for d, dv in _DECENAS.items() for u, uv in _UNIDADES.items() if u not in ("una", "primero")}}
_NUM = "|".join(sorted(_HASTA_99, key=len, reverse=True))


def _cifras_de_fecha(t: str) -> str:
    """«veintiuno de agosto de mil novecientos noventa y tres» → «21 de agosto de 1993» (Whisper casi siempre ya escribe cifras; esto cubre cuando no)."""
    t = re.sub(rf"\b(mil novecientos|dos mil)(?: (?:y )?({_NUM}))?\b", lambda m: str((1900 if m.group(1).startswith("mil") else 2000) + (_HASTA_99[m.group(2)] if m.group(2) else 0)), t)
    return re.sub(rf"\b({_NUM}) de ({'|'.join(_MESES)})\b", lambda m: f"{_HASTA_99[m.group(1)]} de {m.group(2)}", t)


def _fechas(texto: str) -> list[date]:
    t = _cifras_de_fecha(normalizar(texto))
    encontradas: list[date] = []
    for d, m, a in re.findall(r"\b(\d{1,2}) (?:de )?(" + "|".join(_MESES) + r") (?:de |del )?(\d{4})\b", t):
        encontradas.append(_fecha(int(a), _MESES[m], int(d)))
    for a, m, d in re.findall(r"\b(\d{4}) (\d{1,2}) (\d{1,2})\b", t):  # 1985-03-05
        encontradas.append(_fecha(int(a), int(m), int(d)))
    for d, m, a in re.findall(r"\b(\d{1,2}) (\d{1,2}) (\d{4})\b", t):  # 05/03/1985 (día/mes/año, como se usa en Colombia)
        encontradas.append(_fecha(int(a), int(m), int(d)))
    return [f for f in encontradas if f]


def _fecha(a: int, m: int, d: int) -> date | None:
    try:
        return date(a, m, d)
    except ValueError:
        return None


def extraer_fecha_nacimiento(texto: str, hoy: date) -> date | None:
    """Una fecha pasada y plausible. Si el mensaje trae varias, no se arriesga."""
    pasadas = [f for f in _fechas(texto) if date(1900, 1, 1) <= f < hoy]
    return pasadas[0] if len(pasadas) == 1 else None


_DIGITOS_HABLADOS = {"cero": "0", "uno": "1", "una": "1", "dos": "2", "tres": "3", "cuatro": "4", "cinco": "5", "seis": "6", "siete": "7", "ocho": "8", "nueve": "9"}


def unir_digitos_dictados(texto: str) -> str:
    """Por voz el documento llega dictado: «1-0-2-0-3-0…», «1 0 2 0 3 0…» o «uno cero dos cero…». Se convierte en «102030…» (solo si son 5 o más
    dígitos seguidos, para no tocar números sueltos ni fechas)."""
    palabras = "|".join(_DIGITOS_HABLADOS)

    def a_digitos(m: re.Match) -> str:
        return "".join(_DIGITOS_HABLADOS[w.lower()] for w in re.findall(palabras, m.group(0), flags=re.IGNORECASE)) + " "

    texto = re.sub(rf"\b(?:(?:{palabras})\b[\s,.\-]*){{5,}}", a_digitos, texto, flags=re.IGNORECASE)
    return re.sub(r"(?<![\d/\-])\d(?:[\s\-.,]\d){4,}(?![\d/\-])", lambda m: re.sub(r"\D", "", m.group(0)), texto)


def extraer_numero_documento(texto: str) -> str | None:
    texto = unir_digitos_dictados(texto)
    sin_fechas = re.sub(r"\b\d{1,2}[/\-. ]\d{1,2}[/\-. ]\d{4}\b|\b\d{4}[/\-. ]\d{1,2}[/\-. ]\d{1,2}\b", " ", texto)
    candidatos = re.findall(r"\b\d{1,4}(?:[. \-]\d{3,4}){1,3}\b|\b\d{5,12}\b", sin_fechas)  # Whisper escribe 918-374-6502 como un teléfono
    numeros = {re.sub(r"\D", "", c) for c in candidatos if 5 <= len(re.sub(r"\D", "", c)) <= 12}
    return numeros.pop() if len(numeros) == 1 else None


def extraer_identidad(texto: str, hoy: date) -> tuple[str, str, date] | None:
    """(tipo, número, fecha de nacimiento) si el mensaje trae los TRES datos de forma inequívoca; si no, None."""
    tipo, numero, nacimiento = extraer_tipo_documento(texto), extraer_numero_documento(texto), extraer_fecha_nacimiento(texto, hoy)
    return (tipo, numero, nacimiento) if tipo and numero and nacimiento else None


# ======================= citas =======================
def candidatos_a_codigo(texto: str) -> list[str]:
    """Palabras de 8 caracteres con pinta de código de reserva. Se confirman contra la base: 'CANCELAR' también tiene 8 letras."""
    return [c for c in re.findall(r"\b[A-Za-z0-9]{8}\b", texto.upper()) if re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", c)]


def quiere_cancelar(texto: str) -> bool:
    return bool(re.search(r"\b(cancel\w+|anul\w+|elimin\w+|borr\w+|dar de baja)\b", normalizar(texto)))


_SOLICITUDES = [("QUEJA", r"\bqueja\b|\bquejarme\b|\bme atendieron (muy )?mal\b|\bmala atencion\b|\bmal servicio\b|\bpesim[oa] (atencion|servicio)\b"),
                ("RECLAMO", r"\breclamo\b|\breclamar\b"), ("SUGERENCIA", r"\bsugerencia\b|\bsugiero\b|\bles sugiero\b")]


def tipo_solicitud(texto: str) -> str | None:
    """QUEJA / RECLAMO / SUGERENCIA si el paciente ya está contando el motivo (no solo anunciando que quiere poner una)."""
    t = normalizar(texto)
    for tipo, patron in _SOLICITUDES:
        if re.search(patron, t):
            cuenta_el_motivo = len(t) >= 45 or bool(re.search(r"\b(porque|por que|ya que|debido|cuando|cuando fui)\b", t))
            return tipo if cuenta_el_motivo else None
    return None


def quiere_reprogramar(texto: str) -> bool:
    return bool(re.search(r"\b(reprogram\w+|reagend\w+|cambiar (mi|la|de) \w*\s?(cita|fecha|dia|hora)|mover (mi|la) cita|otra fecha)\b", normalizar(texto)))


_ORDINALES = {"primera": 0, "primer": 0, "primero": 0, "1ra": 0, "segunda": 1, "segundo": 1, "2da": 1, "tercera": 2, "tercero": 2, "tercer": 2, "3ra": 2,
              "cuarta": 3, "cuarto": 3, "quinta": 4, "quinto": 4, "sexta": 5, "sexto": 5, "ultima": -1, "ultimo": -1}


def elegir_opcion(texto: str, horas: list[tuple[int, int]]) -> int | None:
    """Índice (0-based) de la opción que el paciente eligió entre las ofrecidas, dadas sus horas locales (h, m). None si no es claro."""
    t = normalizar(texto)
    if len(t) > 80:  # un mensaje largo no es una simple elección
        return None
    for palabra, idx in _ORDINALES.items():
        if re.search(rf"\b{palabra}\b", t):
            return (len(horas) - 1) if idx == -1 else (idx if idx < len(horas) else None)
    m = re.search(r"\b(?:opcion|numero|la|el|n)\s*(\d|uno|dos|tres|cuatro)\b", t)
    if m:
        n = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4}.get(m.group(1)) or int(m.group(1))
        if 1 <= n <= len(horas):
            return n - 1
    def coincide(hora_pedida: int, hora_opcion: int) -> bool:  # "2:00" puede ser la de las 14:00; "14:30" es exacta
        return hora_pedida == hora_opcion or (hora_pedida <= 12 and hora_pedida % 12 == hora_opcion % 12)

    m = re.search(r"\b(\d{1,2}) (?:y )?(\d{2})\b", t)  # el texto ya está normalizado: "8:20" y "8.20" quedan "8 20"; también "8 y 20"
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        coinciden = [i for i, (hh, mm) in enumerate(horas) if mm == mi and coincide(h, hh)]
        return coinciden[0] if len(coinciden) == 1 else None
    m = re.search(r"\blas\s+(\d{1,2})\b", t)  # "a las 8" → la de las 8:00 si es única
    if m:
        h = int(m.group(1))
        coinciden = [i for i, (hh, mm) in enumerate(horas) if mm == 0 and coincide(h, hh)]
        return coinciden[0] if len(coinciden) == 1 else None
    return None


# ======================= qué consultar =======================
def consulta_catalogo(texto: str) -> set[str]:
    """Herramientas de solo lectura cuyo resultado conviene traer ANTES de responder: 'especialidades', 'profesionales', 'horarios', 'requisitos', 'politicas'."""
    t = normalizar(texto)
    out = set()
    if re.search(r"\bespecialidades\b|\bque (servicios|areas|consultas) (tienen|ofrecen|hay|atienden)\b|\bque (atienden|ofrecen)\b", t):
        out.add("especialidades")
    if re.search(r"\bprofesionales\b|\bmedic[oa]s\b|\bdoctor(es|as)?\b|\bquien(es)? atiende", t):
        out.add("profesionales")
    if re.search(r"\bhorarios?\b|\ba que hora\b|\bque dias (atienden|abren|hay)\b|\bcuando atienden\b|\babren\b|\bcierran\b|\batienden (los )?(sabados|domingos|festivos)\b", t):
        out.add("horarios")
    if re.search(r"\brequisitos?\b|\bque (debo|tengo que|necesito|hay que) (llevar|presentar|traer)\b|\bque documentos\b|\bque papeles\b", t):
        out.add("requisitos")
    if re.search(r"\bpoliticas?\b|\bcuanto tiempo (antes|de anticipacion)\b|\banticipacion\b|\bpenaliza\w*|\bsancion\w*|\bmulta\w*|\bcastigo\b|\bsi no (llego|asisto|voy)\b|\binasistencia\w*", t):
        out.add("politicas")
    return out


def tipo_politica(texto: str) -> list[str]:
    t = normalizar(texto)
    tipos = []
    if re.search(r"\bcancel\w+|\banul\w+", t):
        tipos.append("CANCELACION")
    if re.search(r"\breprogram\w+|\bcambiar (mi|la) cita|\bmover (mi|la) cita", t):
        tipos.append("REPROGRAMACION")
    if re.search(r"\binasisten\w+|\bno (llego|asisto|voy|me presento)\b|\bfalto a\b|\bpierd\w+ la cita", t):
        tipos.append("INASISTENCIA")
    return tipos or ["CANCELACION", "REPROGRAMACION", "INASISTENCIA"]


def mencion(texto: str, nombres: list[str]) -> str | None:
    """El nombre de catálogo (especialidad, etc.) que el paciente menciona; None si ninguno o varios."""
    t = normalizar(texto)
    halladas = [n for n in nombres if normalizar(n) in t]
    if len(halladas) > 1:  # "Medicina General" contiene a "Medicina": gana la más larga si las demás están contenidas en ella
        mas_larga = max(halladas, key=len)
        if all(normalizar(n) in normalizar(mas_larga) for n in halladas):
            return mas_larga
        return None
    return halladas[0] if halladas else None


def quiere_cita(texto: str) -> bool:
    return bool(re.search(r"\b(cita|citas|turno|agendar|agenda|consulta|control|hay cupo|cupos?|disponib\w+)\b", normalizar(texto)))


def _sin_fechas_pasadas(t: str, hoy: date) -> str:
    """Quita del texto (ya normalizado) las fechas completas que ya pasaron."""
    meses = "|".join(_MESES)
    for patron, orden in ((r"\b(\d{1,2}) (?:de )?(" + meses + r") (?:de |del )?(\d{4})\b", "dma"), (r"\b(\d{4}) (\d{1,2}) (\d{1,2})\b", "amd"), (r"\b(\d{1,2}) (\d{1,2}) (\d{4})\b", "dma")):
        def quitar(m: re.Match, orden=orden) -> str:
            g = m.groups()
            d, mes, a = (int(g[0]), _MESES.get(g[1]) or int(g[1]), int(g[2])) if orden == "dma" else (int(g[2]), int(g[1]), int(g[0]))
            f = _fecha(a, mes, d)
            return " " if f and f < hoy else m.group(0)
        t = re.sub(patron, quitar, t)
    return t


def rango_de_fechas(texto: str, hoy: date) -> tuple[date, date] | None:
    """El rango que el paciente pide para su cita: una fecha concreta, mañana, un día de la semana, esta/próxima semana. None si no hay."""
    t = _sin_fechas_pasadas(_cifras_de_fecha(normalizar(texto)), hoy)  #«nací el 5 de marzo de 1985» es una fecha de nacimiento, no el día que quiere la cita
    explicitas = [f for f in _fechas(texto) if f >= hoy]
    if explicitas:
        return explicitas[0], explicitas[0]
    m = re.search(r"\b(\d{1,2}) (?:de )?(" + "|".join(_MESES) + r")\b", t)
    if m:
        d, mes = int(m.group(1)), _MESES[m.group(2)]
        for anio in (hoy.year, hoy.year + 1):
            f = _fecha(anio, mes, d)
            if f and f >= hoy:
                return f, f
    if re.search(r"\bpasado manana\b", t):
        return hoy + timedelta(days=2), hoy + timedelta(days=2)
    if _manana_como_dia(t):  # "mañana" día, no la franja del día («en la mañana»)
        return hoy + timedelta(days=1), hoy + timedelta(days=1)
    if re.search(r"\bhoy\b", t):
        return hoy, hoy
    if re.search(r"\b(proxima semana|semana proxima|semana que viene|siguiente semana)\b", t):
        lunes = hoy + timedelta(days=7 - hoy.weekday())
        return lunes, lunes + timedelta(days=6)
    if re.search(r"\besta semana\b", t):
        return hoy, hoy + timedelta(days=6 - hoy.weekday())
    for nombre, wd in _DIAS.items():
        if re.search(rf"\b{nombre}\b", t):
            dias = (wd - hoy.weekday()) % 7 or 7
            f = hoy + timedelta(days=dias)
            return f, f
    return None


def _manana_como_dia(t: str) -> bool:
    """«para mañana en la mañana»: la primera es el día, la segunda la franja. Una «mañana» precedida de «en la», «por la», «de la»… es la franja."""
    return any(not re.search(r"\b(?:en|por|de|a|y|la) (?:la )?$", t[:m.start()]) for m in re.finditer(r"\bmanana\b", t))


def franja(texto: str) -> str | None:
    """«manana» o «tarde» si el paciente pidió expresamente esa parte del día; None si no."""
    t = normalizar(texto)
    if re.search(r"\b(?:en|por|de|a|y) la manana\b|\btemprano\b|\bantes del mediodia\b|\bprimera hora\b", t):
        return "manana"
    if re.search(r"\b(?:en|por|de|a|y) la tarde\b|\bdespues del almuerzo\b|\bmas tarde\b|\bhacia la tarde\b", t):
        return "tarde"
    return None


def quiere_agendar(texto: str) -> bool:
    """Pide una cita nueva (no cancelar ni cambiar una existente)."""
    t = normalizar(texto)
    if quiere_cancelar(t) or quiere_reprogramar(t):
        return False
    return bool(re.search(r"\bagendar\b|\b(?:reservar|programar|apartar|pedir|solicitar|sacar|asignar|necesito|quiero|quisiera|me gustaria|deseo|busco|tomar)\b[^.?!]{0,45}\b(?:cita|turno|consulta|control|valoracion)\b|\b(?:cita|turno) (?:de|con|para|en)\b", t))


def elegir_ordinal(texto: str, cuantas: int) -> int | None:
    """«la segunda», «el último», «opción tres»: índice (0-based) dentro de una lista de ``cuantas`` cosas que se le leyeron."""
    t = normalizar(texto)
    if len(t) > 60:
        return None
    for palabra, idx in _ORDINALES.items():
        if re.search(rf"\b{palabra}\b", t):
            return cuantas - 1 if idx == -1 else (idx if idx < cuantas else None)
    m = re.search(r"\b(?:opcion|numero|la|el|n)\s*(\d|uno|dos|tres|cuatro|cinco|seis|siete)\b", t)
    if m:
        n = {"uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7}.get(m.group(1)) or int(m.group(1))
        return n - 1 if 1 <= n <= cuantas else None
    return None


def confirma_dato(texto: str) -> bool:
    """Un «sí» hablado para corregir o aceptar un dato leído en voz alta: «sí», «es correcto», «exacto», «así es»."""
    t = normalizar(texto)
    return bool(re.match(r"^(?:si|sii|claro|correcto|exacto|asi es|eso es|es correcto|es ese|ese es|esta bien|esta correcto|ok|listo|perfecto|afirmativo|efectivamente|todo bien)\b", t)) and not re.search(r"\b(?:no|incorrecto|mal|equivocad\w+)\b", t)


def pide_otras_opciones(texto: str) -> bool:
    """«otro día», «más tarde», «ninguna me sirve»: quiere que se le ofrezcan otros horarios."""
    return bool(re.search(r"\botr[oa]s? (?:dias?|horas?|fechas?|horarios?|opciones?|cupos?)\b|\bmas (?:tarde|temprano|opciones)\b|\bningun[oa]?\b|\bno me (?:sirve|queda|conviene)\b|\bprefiero otr[oa]\b|\bpara otro dia\b", normalizar(texto)))


def pregunta_sedes(texto: str) -> bool:
    """Pregunta dónde están las sedes (no solo nombra una sede al agendar)."""
    return bool(re.search(r"\b(?:que|cuantas|cuales|donde|direccion\w*|ubicacion\w*) [^.?!]{0,30}\bsedes?\b|\bsedes?\b[^.?!]{0,25}\b(?:tienen|hay|quedan|estan|ubicad\w+|atienden)\b|\bdonde (?:queda|quedan|estan|esta|se encuentran|los encuentro)\b|\bdireccion\b|\bubicacion\b|\bcomo (?:llego|llegar)\b", normalizar(texto)))


def hora_local(dt: datetime) -> tuple[int, int]:
    return dt.hour, dt.minute



# ======================= despedida =======================
# Solo una frase corta que SEA la despedida («no, eso es todo», «gracias, nada más»): «nada más quiero cancelar mi cita» NO es una despedida.
_ANTES = r"(?:no|gracias|muchas gracias|pues|listo|ok|ya|entonces|bueno|si|senor|senora|senorita|no gracias|no senor|no senora|por ahora)"
_DESPEDIDA = (r"(?:eso es todo|es todo|eso seria todo|seria todo|nada mas|ya no necesito nada mas|no necesito nada mas|ya no necesito mas|no necesito mas|ya no necesito nada|no necesito nada|"
              r"ya no requiero nada mas|con eso es suficiente|con eso basta|con eso estoy bien|eso es todo lo que necesitaba|ya termine|ya es todo|es todo por ahora|hasta luego|hasta pronto|adios|chao|chau|nos vemos|que tenga buen dia)")
_DESPUES = r"(?:gracias|muchas gracias|por ahora|por favor|senor|senora|senorita|igualmente|hasta luego|adios|chao|que le vaya bien|buen dia)"
_RE_DESPEDIDA = re.compile(rf"(?:{_ANTES} )*{_DESPEDIDA}(?: {_DESPUES})*")
_RE_SIN_MAS = re.compile(r"(?:no|nada|ninguna|ninguno|no senor|no senora)(?: (?:gracias|muchas gracias|por ahora|nada mas|senor|senora))*")


def despide(texto: str) -> bool:
    """El paciente dice que ya no necesita más ayuda («eso es todo», «nada más, gracias», «hasta luego»)."""
    return bool(_RE_DESPEDIDA.fullmatch(normalizar(texto)))


def sin_mas(texto: str) -> bool:
    """«No», «no gracias», «nada»: solo cuenta como despedida si el asistente acababa de preguntar si necesita algo más."""
    return bool(_RE_SIN_MAS.fullmatch(normalizar(texto)))


# ======================= instituciones =======================
_GENERICAS = {"centro": 0.3, "salud": 0.3, "regional": 0.3, "general": 0.3}
_VACIAS = {"de", "del", "la", "el", "los", "las", "y", "en", "con", "un", "una", "por", "para", "al", "me", "mi", "que", "quiero", "quisiera", "comunicar", "comunicarme", "hablar", "cita", "gracias"}


def _raices(texto: str) -> set[str]:
    return {p[:6] for p in normalizar(texto).split() if len(p) >= 3 and p not in _VACIAS}


def institucion_nombrada(texto: str, instituciones: list[tuple[int, str, str | None]]) -> int | None:
    """El id de la institución que el paciente nombra («el hospital», «sonrisa plena», «la IPS»); None si ninguna o si hay duda.
    ``instituciones``: (id, nombre dicho, tipo). Se comparan raíces de 6 letras: «odontológico» y «odontología» son la misma; las palabras
    comunes («centro», «salud») valen menos que las distintivas."""
    dicho = _raices(texto)
    if not dicho:
        return None
    puntajes = []
    for id_, nombre, tipo in instituciones:
        propias = _raices(f"{nombre} {tipo or ''}")
        puntajes.append((sum(_GENERICAS.get(r, 1.0) for r in propias & dicho), id_))
    puntajes.sort(reverse=True)
    if not puntajes or puntajes[0][0] < 1.0:
        return None
    if len(puntajes) > 1 and puntajes[0][0] - puntajes[1][0] < 0.5:
        return None
    return puntajes[0][1]


def pide_cambiar_institucion(texto: str) -> bool:
    t = normalizar(texto)
    return bool(re.search(r"\b(otra|diferente|distinta) (institucion|clinica|hospital|ips|centro)\b|\bcambiar de institucion\b", t))


# ======================= lo que el reconocimiento de voz deforma =======================
def mencion_aproximada(texto: str, nombres: list[str]) -> str | None:
    """La especialidad que el paciente PARECE nombrar cuando el reconocimiento de voz la deformó («porto pedio» → Ortopedia). Solo frases cortas sin
    números y con un parecido alto y claramente mejor que el de las demás; el asistente igual se la confirma («¿se refiere a…?»)."""
    t = normalizar(texto)
    toks = t.split()
    if not toks or len(toks) > 6 or re.search(r"\d", t):
        return None
    candidatas = {c for i in range(len(toks)) for j in range(i + 1, min(i + 4, len(toks)) + 1) for c in ("".join(toks[i:j]), " ".join(toks[i:j])) if len(c) >= 4}
    puntajes = []
    for n in nombres:
        nn = normalizar(n)
        objetivos = {nn, nn.replace(" ", ""), nn.split()[0]}
        puntajes.append((max(SequenceMatcher(None, c, o).ratio() for c in candidatas for o in objetivos) if candidatas else 0.0, n))
    puntajes.sort(reverse=True)
    if not puntajes or puntajes[0][0] < 0.8 or (len(puntajes) > 1 and puntajes[0][0] - puntajes[1][0] < 0.1):
        return None
    return puntajes[0][1]


def pide_horarios(texto: str) -> bool:
    """Pide VER los horarios o fechas con cupo («quiero ver las fechas disponibles», «qué horarios hay», «hay disponibilidad»)."""
    t = normalizar(texto)
    return bool(re.search(r"\b(fechas|horarios|dias|cupos|citas|horas) (disponibles|libres)\b|\bdisponibilidad\b|\bhay (cupo|cupos|horarios|citas|fechas)\b|\bque (fechas|horarios|dias|horas) (hay|tienen|tiene|manejan)\b|\bcuando (hay|tienen|puedo)\b|\bver (las |los )?(fechas|horarios|cupos|horas)\b", t))



# ======================= encuesta de satisfacción =======================
_NUMEROS = {"uno": 1, "1": 1, "dos": 2, "2": 2, "tres": 3, "3": 3, "cuatro": 4, "4": 4, "cinco": 5, "5": 5}
_CALIDAD = [  # de lo más negativo a lo más positivo: «muy mala» se mira antes que «mala», y «mala» antes que «buena»
    (r"\b(pesim[ao]|horrible|terrible|muy mal[ao]?|fatal|p[eé]sim[ao])\b", 1), (r"\b(mal[ao]?|deficiente|insatisfecho|insatisfecha)\b", 2),
    (r"\b(regular|normal|aceptable|m[aá]s o menos|medio|ni bien ni mal)\b", 3), (r"\b(buen[ao]?|bien|satisfecho|satisfecha)\b", 4),
    (r"\b(excelente|perfect[ao]|muy buen[ao]|s[uú]per|genial|maravillos[ao]|de lo mejor)\b", 5)]


def puntaje(texto: str) -> int | None:
    """La calificación de 1 a 5 que dice el paciente («cinco», «un 4», «excelente», «más o menos»); None si no hay una clara."""
    t = normalizar(texto)
    if len(t.split()) > 8:
        return None
    nums = {_NUMEROS[w] for w in t.split() if w in _NUMEROS}
    if len(nums) == 1:
        return nums.pop()
    if nums:
        return None  # «tres o cuatro»: no se adivina
    for patron, valor in _CALIDAD:
        if re.search(normalizar_patron(patron), t):
            return valor
    return None


def normalizar_patron(patron: str) -> str:
    """Los patrones de calidad se escriben con tildes para leerse mejor; el texto ya viene sin ellas."""
    return patron.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")


def rechaza_encuesta(texto: str) -> bool:
    """«No quiero responder», «prefiero no», «omitir»: se respeta sin insistir."""
    return bool(re.fullmatch(r"(?:no(?: gracias| quiero| deseo| por ahora)?(?: responder| calificar| contestar| hacerla| la encuesta)?|prefiero no(?: responder| calificar| contestar)?|omitir|paso|saltar|sin calificar|ninguna|nada)(?: gracias| por favor)?", normalizar(texto)))
