"""Los datos demo deben ser coherentes (sin necesidad de correr el script): consultorios sin choques, referencias válidas, todo marcado como ficticio."""
from collections import defaultdict
from datetime import time

import pytest

from app.services.politica_service import validar_parametros
from scripts import seed_demo_completo as demo


def _minutos(t: time) -> int:
    return t.hour * 60 + t.minute


def test_cada_profesional_apunta_a_una_especialidad_sede_y_consultorio_que_existen():
    consultorios = {(s, n) for s, lista in demo.CONSULTORIOS.items() for n, _ in lista}
    for nombre, _, esp, sede, cons, jornadas in demo.PROFESIONALES:
        assert esp in demo.ESPECIALIDADES and sede in demo.SEDES and (sede, cons) in consultorios, nombre
        assert all(d in range(7) and ini < fin and minutos > 0 for dias, ini, fin, minutos in jornadas for d in dias), nombre


def test_ningun_consultorio_se_usa_a_la_vez_por_dos_profesionales():
    ocupado = defaultdict(list)  # (sede, consultorio, día) → [(inicio, fin, profesional)]
    for nombre, _, _, sede, cons, jornadas in demo.PROFESIONALES:
        for dias, ini, fin, _ in jornadas:
            for d in dias:
                ocupado[(sede, cons, d)].append((_minutos(ini), _minutos(fin), nombre))
    for clave, franjas in ocupado.items():
        franjas.sort()
        for (i1, f1, n1), (i2, f2, n2) in zip(franjas, franjas[1:]):
            assert f1 <= i2 or n1 == n2, f"{clave}: {n1} y {n2} chocan"


def test_los_datos_son_visiblemente_ficticios():
    assert all("demo" in n.lower() for n in demo.SEDES) and all("(demo)" in e for e in demo.ESPECIALIDADES)
    assert all("(fictici" in p[0] for p in demo.PROFESIONALES)
    for tipo, doc, *_ in demo.PACIENTES:
        assert doc.startswith("9") and len(doc) == 10  # numeración que no corresponde a documentos reales
    assert len({p[1] for p in demo.PACIENTES}) == len(demo.PACIENTES)  # sin documentos repetidos
    assert all(u[0].endswith("@example.com") for u in demo.ROLES_DEMO)  # dominio reservado: nunca llega a una persona real


def test_las_politicas_demo_son_validas():
    for tipo, titulo, parametros, mensaje in demo.POLITICAS:
        validar_parametros(tipo, parametros)
        assert "(demo)" in titulo and mensaje


def test_las_respuestas_de_las_preguntas_frecuentes_coinciden_con_los_horarios_cargados():
    sabado = [p for p in demo.PROFESIONALES if any(5 in dias for dias, *_ in p[5])]
    assert [p[2] for p in sabado] == ["Psicología (demo)"]  # la FAQ dice que solo Psicología atiende los sábados
    texto = " ".join(r for _, _, r in demo.FAQS)
    for esp, (_, servicios) in demo.ESPECIALIDADES.items():
        assert str(servicios[0][1]) in texto  # las duraciones citadas existen en el catálogo
