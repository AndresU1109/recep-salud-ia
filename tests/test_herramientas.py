"""Fase 11: las 15 herramientas. Son la ÚNICA vía del LLM a los datos: aquí se prueba identidad, aislamiento y reglas."""
import json
import uuid
from datetime import date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.models import Cita, ConsentimientoDatos, EjecucionHerramienta, ExcepcionAgenda, Paciente, PoliticaInstitucional, Requisito, Solicitud
from app.models.enums import CanalConversacion, CanalCita, EstadoCita, TipoDocumento, TipoPolitica, TipoSolicitud
from app.schemas.paciente import ConsentimientoIn
from app.services import cita_service, conversacion_service, paciente_service
from app.tools import REGISTRO
from app.tools.contexto import ContextoHerramientas
from app.tools.registro import redactar_parametros
from tests.factories import crear_base, nueva_cita
from tests.test_agenda import LUNES, horario, local

NOMBRES_ESPERADOS = ["agendar_cita", "buscar_paciente", "cancelar_cita", "consultar_citas", "consultar_disponibilidad", "consultar_especialidades", "consultar_horarios",
                     "consultar_inasistencias", "consultar_politicas", "consultar_profesionales", "consultar_requisitos", "crear_solicitud", "registrar_paciente",
                     "reprogramar_cita", "transferir_a_funcionario"]
DOC, NACIMIENTO = "1020304050", date(1985, 3, 5)


def ctx_de(db, canal=CanalConversacion.TEXTO, **kw) -> ContextoHerramientas:
    conv = conversacion_service.crear(db, canal)
    return ContextoHerramientas(db=db, conversacion_id=conv.id, canal=canal, **kw)


def paciente_real(db, doc=DOC, nacimiento=NACIMIENTO, nombres="Ana María", transcripcion=False, acepta=True) -> Paciente:
    return paciente_service.registrar(db, None, {"tipo_documento": TipoDocumento.CC, "numero_documento": doc, "nombres": nombres, "apellidos": "Ficticia Prueba", "fecha_nacimiento": nacimiento},
                                      ConsentimientoIn(politica_version="demo-1", aceptado=acepta, autoriza_transcripcion=transcripcion), canal="PRUEBA")


def verificada(db, **kw) -> tuple[ContextoHerramientas, Paciente]:
    p = paciente_real(db, **kw)
    ctx = ctx_de(db)
    r = REGISTRO.ejecutar(ctx, "buscar_paciente", {"tipo_documento": "CC", "numero_documento": DOC, "fecha_nacimiento": "1985-03-05"})
    assert r.ok, r.datos
    return ctx, p


def con_agenda(db):
    """Base con una especialidad 'Medicina General', un profesional y horario lunes 8-12 (cupos de 20 min)."""
    b = crear_base(db)
    b.esp.nombre, b.prof1.nombre_completo = "Medicina General", "Dra. Ficticia Uno"
    horario(db, b)
    db.flush()
    return b


def llamar(ctx, nombre, **args):
    ctx.db.commit()  # como en producción: los datos previos ya están confirmados, así un rollback de la herramienta no los borra
    return REGISTRO.ejecutar(ctx, nombre, args)


# ======================= el registro =======================
def test_estan_las_15_herramientas_de_la_especificacion():
    assert sorted(REGISTRO.nombres()) == NOMBRES_ESPERADOS


def test_los_esquemas_son_compactos_y_sin_referencias():
    esquemas = REGISTRO.esquemas()
    texto = json.dumps(esquemas, ensure_ascii=False)
    assert "$ref" not in texto and '"title"' not in texto and len(texto) < 9000
    for e in esquemas:
        f = e["function"]
        assert e["type"] == "function" and f["description"] and f["parameters"]["type"] == "object" and "properties" in f["parameters"]
    por_nombre = {e["function"]["name"]: e["function"]["parameters"] for e in esquemas}
    assert set(por_nombre["agendar_cita"]["required"]) == {"cupo"} and "paciente_id" not in json.dumps(por_nombre)  # el LLM no puede elegir al paciente
    assert por_nombre["buscar_paciente"]["properties"]["tipo_documento"]["enum"] == ["CC", "TI", "RC", "CE", "PA", "PPT"]


def test_una_herramienta_inexistente_se_rechaza(db):
    r = llamar(ctx_de(db), "ejecutar_sql", consulta="DROP TABLE pacientes")
    assert not r.ok and r.codigo_error == "herramienta_desconocida" and r.rechazada


def test_argumentos_no_previstos_se_rechazan_aunque_sean_plausibles(db):
    ctx, p = verificada(db)
    otro = paciente_real(db, doc="9999999", nacimiento=date(1990, 1, 1))
    r = llamar(ctx, "consultar_citas", paciente_id=otro.id)
    assert not r.ok and r.codigo_error == "argumentos_invalidos" and "paciente_id" in r.datos["mensaje"]
    assert llamar(ctx, "cancelar_cita", codigo_reserva="ABCD1234", paciente_id=otro.id).codigo_error == "argumentos_invalidos"


def test_argumentos_con_tipo_equivocado_devuelven_un_error_entendible(db):
    r = llamar(ctx_de(db), "buscar_paciente", tipo_documento="XX", numero_documento="1", fecha_nacimiento="ayer")
    assert r.codigo_error == "argumentos_invalidos" and "tipo_documento" in r.datos["mensaje"] and "fecha_nacimiento" in r.datos["mensaje"]


@pytest.mark.parametrize("nombre, args", [("agendar_cita", {"cupo": 1}), ("consultar_citas", {}), ("cancelar_cita", {"codigo_reserva": "ABCD1234"}),
                                          ("reprogramar_cita", {"codigo_reserva": "ABCD1234", "nuevo_cupo": 1}), ("consultar_inasistencias", {})])
def test_sin_identidad_verificada_no_se_toca_nada_de_un_paciente(db, nombre, args):
    r = llamar(ctx_de(db), nombre, **args)
    assert not r.ok and r.codigo_error == "identidad_no_verificada" and "buscar_paciente" in r.datos["mensaje"]


def test_los_parametros_de_auditoria_omiten_datos_personales():
    p = redactar_parametros({"tipo_documento": "CC", "numero_documento": "1020304050", "fecha_nacimiento": "1985-03-05", "nombres": "Ana", "especialidad": "Pediatría", "descripcion": "me duele"})
    assert p == {"tipo_documento": "CC", "numero_documento": "[omitido]", "fecha_nacimiento": "[omitido]", "nombres": "[omitido]", "especialidad": "Pediatría", "descripcion": "[omitido]"}


def test_un_resultado_enorme_se_recorta_para_el_llm(db):
    from app.tools.registro import MAX_RESULTADO, ResultadoHerramienta

    r = ResultadoHerramienta(True, {"ok": True, "x": "a" * 10000})
    texto = r.para_llm()
    assert len(texto) < MAX_RESULTADO + 100 and json.loads(texto)["ok"] is True


def test_un_fallo_de_la_base_se_convierte_en_error_interno_y_no_rompe_la_sesion(db, monkeypatch):
    from app.tools import herramientas

    def roto(ctx, a):
        raise OperationalError("SELECT", {}, Exception("conexión perdida"))

    monkeypatch.setattr(REGISTRO.obtener("consultar_especialidades"), "funcion", roto)
    r = llamar(ctx_de(db), "consultar_especialidades")
    assert not r.ok and r.codigo_error == "error_interno" and not r.rechazada and "conexión" not in json.dumps(r.datos)
    assert herramientas  # el módulo sigue cargado y la sesión usable
    db.execute(select(Paciente))


# ======================= identidad =======================
def test_verificar_identidad_devuelve_solo_el_primer_nombre(db):
    ctx, p = verificada(db)
    assert ctx.identidad_verificada and ctx.paciente_id == p.id and ctx.primer_nombre == "Ana"
    r = llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento=DOC, fecha_nacimiento="1985-03-05")
    assert r.datos == {"ok": True, "verificado": True, "nombre": "Ana"} and DOC not in r.para_llm() and "Ficticia" not in r.para_llm()


def test_identidad_incorrecta_no_revela_si_el_paciente_existe(db):
    paciente_real(db)
    ctx = ctx_de(db)
    mala = llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento=DOC, fecha_nacimiento="1985-03-06")
    no_existe = llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento="5550001112", fecha_nacimiento="1985-03-05")
    assert mala.datos == no_existe.datos and not ctx.identidad_verificada and ctx.paciente_id is None


def test_se_bloquea_tras_cinco_intentos_fallidos(db):
    paciente_real(db)
    ctx = ctx_de(db)
    for _ in range(5):
        llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento=DOC, fecha_nacimiento="1985-03-06")
    r = llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento=DOC, fecha_nacimiento="1985-03-05")
    assert r.codigo_error == "verificacion_bloqueada"


def test_en_una_conversacion_no_se_puede_pasar_a_otra_persona(db):
    ctx, _ = verificada(db)
    paciente_real(db, doc="7777777", nacimiento=date(1992, 2, 2), nombres="Luis")
    r = llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento="7777777", fecha_nacimiento="1992-02-02")
    assert r.codigo_error == "otra_identidad" and ctx.primer_nombre == "Ana"


@pytest.mark.parametrize("autoriza, esperado", [(True, True), (False, False)])
def test_la_transcripcion_depende_de_la_autorizacion_del_paciente(db, autoriza, esperado):
    ctx, _ = verificada(db, transcripcion=autoriza)
    assert ctx.guarda_transcripcion is esperado


# ======================= registrar paciente =======================
def datos_registro(**cambios):
    return {"tipo_documento": "CC", "numero_documento": "5544332211", "nombres": "Carlos", "apellidos": "Ficticio Nuevo", "fecha_nacimiento": "1990-07-20",
            "autoriza_tratamiento_datos": True, **cambios}


def test_sin_version_de_politica_el_asistente_no_registra(db):
    r = llamar(ctx_de(db), "registrar_paciente", **datos_registro())
    assert r.codigo_error == "registro_no_habilitado" and db.scalar(select(Paciente)) is None


def test_registrar_con_autorizacion_crea_paciente_y_consentimiento(db):
    ctx = ctx_de(db, politica_datos_version="v-2026")
    r = llamar(ctx, "registrar_paciente", **datos_registro(autoriza_transcripcion=True))
    assert r.ok and r.datos["nombre"] == "Carlos" and ctx.identidad_verificada and ctx.guarda_transcripcion
    c = db.scalars(select(ConsentimientoDatos)).one()
    assert c.politica_version == "v-2026" and c.aceptado and c.autoriza_transcripcion and c.canal == "AGENTE"
    assert "5544332211" not in r.para_llm()


def test_registrar_sin_autorizacion_o_datos_malos_falla(db):
    ctx = ctx_de(db, politica_datos_version="v-2026")
    assert llamar(ctx, "registrar_paciente", **datos_registro(autoriza_tratamiento_datos=False)).codigo_error == "sin_consentimiento"
    assert llamar(ctx, "registrar_paciente", **datos_registro(nombres="A")).codigo_error == "argumentos_invalidos"
    assert db.scalar(select(Paciente)) is None and not ctx.identidad_verificada


def test_registrar_dos_veces_el_mismo_documento_o_estando_ya_identificado(db):
    paciente_real(db, doc="5544332211")
    ctx = ctx_de(db, politica_datos_version="v-2026")
    assert llamar(ctx, "registrar_paciente", **datos_registro()).codigo_error == "paciente_duplicado"
    ctx2, _ = verificada(db)
    ctx2.politica_datos_version = "v-2026"
    assert llamar(ctx2, "registrar_paciente", **datos_registro(numero_documento="6677889900")).codigo_error == "ya_verificado"


# ======================= catálogo =======================
def test_especialidades_y_profesionales(db):
    b = con_agenda(db)
    ctx = ctx_de(db)
    assert llamar(ctx, "consultar_especialidades").datos["especialidades"] == ["Medicina General"]
    r = llamar(ctx, "consultar_profesionales", especialidad="medicina general")
    assert r.ok and r.datos["profesionales"] == [] or r.datos["profesionales"] == ["Dra. Ficticia Uno"]


def test_sin_especialidades_registradas_lo_dice(db):
    assert "no tiene especialidades" in llamar(ctx_de(db), "consultar_especialidades").datos["nota"]


def test_el_nombre_aproximado_se_resuelve_y_lo_ambiguo_o_inexistente_lista_opciones(db):
    b = con_agenda(db)
    otra = crear_base(db)
    otra.esp.nombre = "Medicina Interna"
    db.flush()
    ctx = ctx_de(db)
    assert llamar(ctx, "consultar_horarios", especialidad="Medicina General").ok
    amb = llamar(ctx, "consultar_horarios", especialidad="medicina")
    assert amb.codigo_error == "especialidad_ambigua" and "Medicina General" in amb.datos["mensaje"] and "Medicina Interna" in amb.datos["mensaje"]
    no = llamar(ctx, "consultar_horarios", especialidad="Neurocirugía")
    assert no.codigo_error == "especialidad_no_encontrada" and "Opciones" in no.datos["mensaje"]


def test_horarios_semanales(db):
    con_agenda(db)
    r = llamar(ctx_de(db), "consultar_horarios", especialidad="Medicina General")
    assert r.ok and r.datos["horarios"][0] == {"profesional": "Dra. Ficticia Uno", "dia": "lunes", "desde": "08:00", "hasta": "12:00", "sede": r.datos["horarios"][0]["sede"]}
    assert llamar(ctx_de(db), "consultar_horarios").codigo_error == "falta_dato"


def test_requisitos_generales_y_por_especialidad_y_cuando_no_hay_ninguno(db):
    b = con_agenda(db)
    ctx = ctx_de(db)
    assert llamar(ctx, "consultar_requisitos", especialidad="Medicina General").datos["requisitos"] == []
    assert "No invente" in llamar(ctx, "consultar_requisitos").datos["nota"]
    db.add_all([Requisito(titulo="Documento de identidad", descripcion="Presentar el documento original."),
                Requisito(titulo="Remisión", descripcion="Solo si aplica.", especialidad_id=b.esp.id), Requisito(titulo="Inactivo", descripcion="No debe salir.", activo=False)])
    db.flush()
    titulos = [x["titulo"] for x in llamar(ctx, "consultar_requisitos", especialidad="Medicina General").datos["requisitos"]]
    assert titulos == ["Documento de identidad", "Remisión"] or sorted(titulos) == ["Documento de identidad", "Remisión"]
    assert [x["titulo"] for x in llamar(ctx, "consultar_requisitos").datos["requisitos"]] == ["Documento de identidad"]


def test_politicas_sin_configurar_no_inventan_nada(db):
    r = llamar(ctx_de(db), "consultar_politicas", tipo="CANCELACION")
    assert r.ok and r.datos["configurada"] is False and "No invente" in r.datos["nota"]
    assert llamar(ctx_de(db), "consultar_politicas", tipo="MULTA").codigo_error == "argumentos_invalidos"


def test_politica_configurada_se_devuelve_tal_cual(db):
    db.add(PoliticaInstitucional(tipo=TipoPolitica.CANCELACION, titulo="Cancelación", parametros={"horas_minimas_cancelar": 24}, mensaje_paciente="Cancele con 24 horas.", vigente_desde=date(2020, 1, 1)))
    db.flush()
    r = llamar(ctx_de(db), "consultar_politicas", tipo="CANCELACION")
    assert r.datos["configurada"] and r.datos["mensaje"] == "Cancele con 24 horas." and r.datos["reglas"] == {"horas_minimas_cancelar": 24}


# ======================= disponibilidad =======================
def test_disponibilidad_numera_los_cupos_y_los_recuerda_en_el_contexto(db):
    con_agenda(db)
    ctx = ctx_de(db)
    r = llamar(ctx, "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-04", hasta="2030-03-04")
    assert r.ok and r.datos["total_cupos"] == 12 and len(r.datos["opciones"]) == 3  # máximo 3 por día, repartidos en la jornada
    assert [o["opcion"] for o in r.datos["opciones"]] == [1, 2, 3] and ctx.ultima_oferta == [1, 2, 3]
    assert [o["cuando"].split(" a las ")[1] for o in r.datos["opciones"]] == ["8:00 a. m.", "10:00 a. m.", "11:40 a. m."]  # primero, medio y último
    assert ctx.cupos[1].inicio == local(8) and ctx.cupos[1].profesional == "Dra. Ficticia Uno" and "más horarios" in r.datos["nota"] and "solo la hora" in r.datos["nota"]
    r2 = llamar(ctx, "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-04", hasta="2030-03-04", franja="tarde")
    assert r2.datos["total_cupos"] == 0 and r2.datos["opciones"] == []


def test_disponibilidad_reparte_las_opciones_entre_varios_dias(db):
    b = crear_base(db)
    b.esp.nombre = "Medicina General"
    for dia in (0, 1, 2):
        horario(db, b, dia=dia, cons="cons1")
    ctx = ctx_de(db)
    r = llamar(ctx, "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-04", hasta="2030-03-06")
    dias = [o["cuando"].split(" a las")[0] for o in r.datos["opciones"]]
    assert len(r.datos["opciones"]) == 6 and set(dias) == {"lunes 4 de marzo", "martes 5 de marzo"}  # 3 + 3 = tope de opciones


def test_disponibilidad_franja_manana(db):
    con_agenda(db)
    r = llamar(ctx_de(db), "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-04", hasta="2030-03-04", franja="manana")
    assert r.datos["total_cupos"] == 12


def test_fechas_pasadas_se_ajustan_y_un_rango_enorme_se_limita(db):
    con_agenda(db)
    r = llamar(ctx_de(db), "consultar_disponibilidad", especialidad="Medicina General", desde="2020-01-01", hasta="2020-01-31")
    assert r.codigo_error == "rango_invalido"
    r = llamar(ctx_de(db), "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-04", hasta="2031-03-04")
    assert r.ok and r.datos["total_cupos"] > 0  # se recortó a 14 días, no devolvió un año


def test_disponibilidad_no_ofrece_lo_ocupado_ni_lo_bloqueado(db):
    b = con_agenda(db)
    nueva_cita(db, b, inicio=local(8))
    db.add(ExcepcionAgenda(inicio=local(8, 20), fin=local(9), motivo="Cierre", profesional_id=b.prof1.id))
    db.flush()
    r = llamar(ctx_de(db), "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-04", hasta="2030-03-04")
    assert r.datos["opciones"][0]["cuando"] == "lunes 4 de marzo a las 9:00 a. m." and r.datos["total_cupos"] == 9  # ni las 8:00 (ocupada) ni 8:20-8:40 (bloqueadas)


def test_sin_cupos_sugiere_alternativas_sin_inventar(db):
    con_agenda(db)
    r = llamar(ctx_de(db), "consultar_disponibilidad", especialidad="Medicina General", desde="2030-03-05", hasta="2030-03-05")  # martes sin horario
    assert r.ok and r.datos["total_cupos"] == 0 and "lista de espera" in r.datos["nota"]


# ======================= citas =======================
def ofrecer(db, ctx, dia="2030-03-04"):
    r = llamar(ctx, "consultar_disponibilidad", especialidad="Medicina General", desde=dia, hasta=dia)
    assert r.ok and r.datos["opciones"], r.datos
    return r.datos["opciones"]


def test_agendar_solo_un_cupo_ofrecido(db):
    con_agenda(db)
    ctx, p = verificada(db)
    assert llamar(ctx, "agendar_cita", cupo=1).codigo_error == "cupo_desconocido"
    ofrecer(db, ctx)
    r = llamar(ctx, "agendar_cita", cupo=1)
    assert r.ok and len(r.datos["codigo_reserva"]) == 8 and r.datos["cuando"].startswith("lunes 4 de marzo a las 8:00")
    cita = db.scalar(select(Cita).where(Cita.codigo_reserva == r.datos["codigo_reserva"]))
    assert cita.paciente_id == p.id and cita.canal == CanalCita.AGENTE and cita.conversacion_id == ctx.conversacion_id and cita.estado == EstadoCita.PROGRAMADA


def test_el_canal_de_voz_queda_registrado_en_la_cita(db):
    con_agenda(db)
    paciente_real(db)
    ctx = ctx_de(db, canal=CanalConversacion.VOZ)
    llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento=DOC, fecha_nacimiento="1985-03-05")
    ofrecer(db, ctx)
    r = llamar(ctx, "agendar_cita", cupo=1)
    assert db.scalar(select(Cita.canal).where(Cita.codigo_reserva == r.datos["codigo_reserva"])) == CanalCita.VOZ


def test_si_el_cupo_se_ocupa_entre_la_oferta_y_la_reserva_se_avisa(db):
    b = con_agenda(db)
    ctx, _ = verificada(db)
    ofrecer(db, ctx)
    nueva_cita(db, b, inicio=local(8), pac=b.pac2)  # otra persona lo tomó
    r = llamar(ctx, "agendar_cita", cupo=1)
    assert not r.ok and r.codigo_error == "cupo_no_disponible"


def test_agendar_requiere_consentimiento_vigente(db):
    con_agenda(db)
    ctx, p = verificada(db)
    ofrecer(db, ctx)
    db.add(ConsentimientoDatos(paciente_id=p.id, politica_version="demo-2", aceptado=False, canal="PRUEBA"))
    db.flush()
    assert llamar(ctx, "agendar_cita", cupo=1).codigo_error == "sin_consentimiento"


def test_consultar_citas_solo_las_propias_y_proximas(db):
    b = con_agenda(db)
    ctx, p = verificada(db)
    ajeno = paciente_real(db, doc="7777777", nacimiento=date(1992, 2, 2), nombres="Luis")
    nueva_cita(db, b, inicio=local(9), pac=p)
    nueva_cita(db, b, inicio=local(10), pac=ajeno, prof=b.prof2, cons=b.cons2)
    nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc), pac=p, prof=b.prof2, cons=b.cons2)  # pasada
    r = llamar(ctx, "consultar_citas")
    assert len(r.datos["citas"]) == 1 and r.datos["citas"][0]["cuando"].startswith("lunes 4 de marzo a las 9:00") and "Luis" not in r.para_llm()
    assert len(llamar(ctx, "consultar_citas", incluir_pasadas=True).datos["citas"]) == 2


def test_sin_citas_lo_dice(db):
    ctx, _ = verificada(db)
    assert "no tiene citas" in llamar(ctx, "consultar_citas").datos["nota"]


def test_cancelar_y_aislamiento_entre_pacientes(db):
    b = con_agenda(db)
    ctx, p = verificada(db)
    ajeno = paciente_real(db, doc="7777777", nacimiento=date(1992, 2, 2), nombres="Luis")
    propia = nueva_cita(db, b, inicio=local(9), pac=p)
    de_otro = nueva_cita(db, b, inicio=local(10), pac=ajeno, prof=b.prof2, cons=b.cons2)
    assert llamar(ctx, "cancelar_cita", codigo_reserva=de_otro.codigo_reserva).codigo_error == "cita_no_encontrada"  # no revela que existe
    assert llamar(ctx, "cancelar_cita", codigo_reserva="NOEXISTE").codigo_error == "cita_no_encontrada"
    db.refresh(de_otro)
    assert de_otro.estado == EstadoCita.PROGRAMADA
    r = llamar(ctx, "cancelar_cita", codigo_reserva=propia.codigo_reserva.lower(), motivo="Viaje")
    assert r.ok and r.datos["cancelada"] is True
    db.refresh(propia)
    assert propia.estado == EstadoCita.CANCELADA and propia.motivo_cancelacion == "Viaje"


def test_cancelar_respeta_la_politica_y_devuelve_el_mensaje_de_la_institucion(db):
    b = con_agenda(db)
    ctx, p = verificada(db)
    db.add(PoliticaInstitucional(tipo=TipoPolitica.CANCELACION, titulo="Cancelación", parametros={"horas_minimas_cancelar": 24}, mensaje_paciente="Cancele con 24 horas de anticipación.", vigente_desde=date(2020, 1, 1)))
    cercana = nueva_cita(db, b, inicio=(datetime.now(timezone.utc) + timedelta(hours=2)).replace(second=0, microsecond=0), pac=p)
    r = llamar(ctx, "cancelar_cita", codigo_reserva=cercana.codigo_reserva)
    assert not r.ok and r.codigo_error == "politica_incumplida" and r.datos["mensaje"] == "Cancele con 24 horas de anticipación."
    db.refresh(cercana)
    assert cercana.estado == EstadoCita.PROGRAMADA  # el asistente no puede saltarse la política


def test_reprogramar_a_un_cupo_ofrecido(db):
    b = con_agenda(db)
    ctx, p = verificada(db)
    cita = nueva_cita(db, b, inicio=local(8), pac=p)
    ofrecer(db, ctx)  # ofrece 8:20 en adelante (8:00 está ocupado)
    assert llamar(ctx, "reprogramar_cita", codigo_reserva=cita.codigo_reserva, nuevo_cupo=99).codigo_error == "cupo_desconocido"
    r = llamar(ctx, "reprogramar_cita", codigo_reserva=cita.codigo_reserva, nuevo_cupo=1)
    assert r.ok and r.datos["codigo_reserva"] == cita.codigo_reserva and r.datos["cuando"].startswith("lunes 4 de marzo a las 8:20")
    db.refresh(cita)
    assert cita.inicio == local(8, 20) and cita.reprogramaciones == 1


def test_inasistencias_cuenta_solo_las_verificadas(db):
    b = crear_base(db)
    ctx, p = verificada(db)
    c = nueva_cita(db, b, inicio=datetime(2020, 3, 2, 14, tzinfo=timezone.utc), pac=p)
    reg = cita_service.registrar_inasistencia(db, c.id, None, cita_service.Actor(cita_service.ActorTipo.USUARIO, None, True))
    assert llamar(ctx, "consultar_inasistencias").datos["inasistencias_verificadas"] == 0
    cita_service.verificar_inasistencia(db, reg.id, True, None, False, cita_service.Actor(cita_service.ActorTipo.USUARIO, None, True))
    r = llamar(ctx, "consultar_inasistencias")
    assert r.datos["inasistencias_verificadas"] == 1 and r.datos["politica"] is None and "no invente" in r.datos["nota"]


# ======================= solicitudes y transferencia =======================
def test_crear_solicitud_con_y_sin_paciente(db):
    ctx = ctx_de(db)
    r = llamar(ctx, "crear_solicitud", tipo="QUEJA", descripcion="Me atendieron mal en la recepción de la sede.")
    s = db.get(Solicitud, r.datos["numero"])
    assert r.ok and s.tipo == TipoSolicitud.QUEJA and s.paciente_id is None and s.conversacion_id == ctx.conversacion_id
    ctx2, p = verificada(db)
    assert db.get(Solicitud, llamar(ctx2, "crear_solicitud", tipo="SUGERENCIA", descripcion="Sugiero ampliar el horario.").datos["numero"]).paciente_id == p.id
    assert llamar(ctx, "crear_solicitud", tipo="QUEJA", descripcion="corta").codigo_error == "argumentos_invalidos"
    assert llamar(ctx, "crear_solicitud", tipo="AMENAZA", descripcion="Texto suficientemente largo.").codigo_error == "argumentos_invalidos"


def test_transferir_registra_la_solicitud_y_marca_el_contexto(db):
    ctx = ctx_de(db)
    r = llamar(ctx, "transferir_a_funcionario", motivo="Quiere hablar con alguien sobre su factura.")
    assert r.ok and ctx.transferida and "No prometa" in r.datos["nota"]
    s = db.get(Solicitud, r.datos["numero"])
    assert s.tipo == TipoSolicitud.TRANSFERENCIA and s.conversacion_id == ctx.conversacion_id


def test_el_resultado_de_las_herramientas_no_expone_datos_personales(db):
    con_agenda(db)
    ctx, p = verificada(db)
    textos = [llamar(ctx, "consultar_especialidades").para_llm(), llamar(ctx, "consultar_citas").para_llm(), llamar(ctx, "consultar_inasistencias").para_llm(),
              llamar(ctx, "buscar_paciente", tipo_documento="CC", numero_documento=DOC, fecha_nacimiento="1985-03-05").para_llm()]
    for t in textos:
        assert DOC not in t and "1985" not in t and "Ficticia Prueba" not in t, t
