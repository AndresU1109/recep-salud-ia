"""Fase 8 (sin BD): limpieza, fragmentación y extracción de texto."""
import io
import zipfile

import pytest
from docx import Document
from pypdf import PdfWriter

from app.core.errores import ErrorNegocio
from app.rag import chunking, extractores
from app.rag.limpieza import limpiar, quitar_encabezados_y_pies


def pdf_con_texto(lineas: list[str]) -> bytes:
    """PDF mínimo de una página escrito a mano (sin librerías de generación)."""
    contenido = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({l}) Tj T*" for l in lineas) + " ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(contenido)} >>\nstream\n{contenido}\nendstream".encode(), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    salida, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + o + b"\nendobj\n"
    xref = len(salida)
    salida += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    return salida + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()


def docx_con(parrafos: list[str], tabla: list[list[str]] | None = None) -> bytes:
    d = Document()
    for p in parrafos:
        d.add_paragraph(p)
    if tabla:
        t = d.add_table(rows=len(tabla), cols=len(tabla[0]))
        for i, fila in enumerate(tabla):
            for j, celda in enumerate(fila):
                t.cell(i, j).text = celda
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def codigo(fn, *a):
    with pytest.raises(ErrorNegocio) as e:
        fn(*a)
    return e.value.codigo


# ======================= limpieza =======================
def test_limpieza_une_renglones_cortados_y_palabras_con_guion():
    assert limpiar("La cita se debe can-\ncelar con antelación\nsuficiente para el paciente.") == "La cita se debe cancelar con antelación suficiente para el paciente."


def test_limpieza_conserva_parrafos_y_quita_basura():
    t = limpiar("Primer párrafo.\r\n\r\n\r\n\r\nSegundo párrafo\x00 con   espacios.​")
    assert t == "Primer párrafo.\n\nSegundo párrafo con espacios."


def test_limpieza_no_une_un_titulo_con_el_parrafo_siguiente():
    assert limpiar("1. Requisitos\nPresentar documento de identidad.") == "1. Requisitos\nPresentar documento de identidad."


def test_limpieza_normaliza_unicode():
    assert limpiar("Café") == "Café"


def test_quita_encabezados_pies_y_numeros_de_pagina():
    paginas = [f"Manual Clínica Demo\nContenido único {i}.\nPágina {i} de 5" for i in range(1, 6)]
    limpio = quitar_encabezados_y_pies(paginas)
    assert all("Manual Clínica Demo" not in p and "Página" not in p for p in limpio)
    assert [p.strip() for p in limpio] == [f"Contenido único {i}." for i in range(1, 6)]


def test_no_se_borran_lineas_de_contenido_que_solo_difieren_en_un_numero():
    paginas = [f"Paso {i}\nAbrir la agenda y elegir el cupo {i}.\nMembrete Demo" for i in range(1, 7)]
    limpio = quitar_encabezados_y_pies(paginas)
    assert all(f"Paso {i}" in limpio[i - 1] and "Membrete" not in limpio[i - 1] for i in range(1, 7))


def test_documentos_cortos_conservan_su_texto():
    assert quitar_encabezados_y_pies(["Línea 1\nLínea 2"]) == ["Línea 1\nLínea 2"]


# ======================= fragmentación =======================
TEXTO_LARGO = "\n\n".join(f"Párrafo {i}. " + " ".join(f"Esta es la frase número {j} del párrafo {i}." for j in range(6)) for i in range(12))


def test_los_fragmentos_respetan_el_tamano_y_cubren_todo_el_texto():
    frags = chunking.dividir(TEXTO_LARGO, 500, 80)
    assert len(frags) > 3 and all(f.strip() for f in frags)
    assert all(len(f) <= 500 + 80 for f in frags)
    palabras_originales = set(TEXTO_LARGO.split())
    assert palabras_originales <= set(" ".join(frags).split())


def test_hay_solapamiento_entre_fragmentos_consecutivos():
    frags = chunking.dividir(TEXTO_LARGO, 500, 120)
    for a, b in zip(frags, frags[1:]):
        ultima_frase = a.replace("\n", " ").split(". ")[-1].strip().rstrip(".")
        assert ultima_frase and ultima_frase in b.replace("\n", " ")


def test_texto_corto_es_un_solo_fragmento():
    assert chunking.dividir("Solo un párrafo corto.", 1000, 150) == ["Solo un párrafo corto."]


def test_un_parrafo_enorme_sin_puntuacion_se_parte_por_palabras():
    texto = " ".join(["palabra"] * 600)
    frags = chunking.dividir(texto, 300, 0)
    assert len(frags) >= 14 and all(len(f) <= 300 for f in frags)


def test_el_titulo_viaja_con_el_texto_que_introduce():
    frags = chunking.dividir("Requisitos para consulta\n\nPresentar documento de identidad y carné vigente.\n\nOtro tema distinto.", 1000, 0)
    assert frags == ["Requisitos para consulta\nPresentar documento de identidad y carné vigente.\n\nOtro tema distinto."]


def test_fragmentacion_es_determinista():
    assert chunking.dividir(TEXTO_LARGO, 500, 80) == chunking.dividir(TEXTO_LARGO, 500, 80)


# ======================= extracción =======================
def test_extrae_pdf_real():
    t = extractores.extraer_texto("manual.pdf", pdf_con_texto(["Politica de cancelacion de citas.", "Debe avisar con anticipacion."]))
    assert "cancelacion" in t and "anticipacion" in t


def test_extrae_docx_con_parrafos_y_tablas():
    t = extractores.extraer_texto("reglamento.DOCX", docx_con(["Requisitos para la consulta de medicina general."], [["Documento", "Cedula"], ["Carne", "Vigente"]]))
    assert "medicina general" in t and "Documento | Cedula" in t and "Carne | Vigente" in t


@pytest.mark.parametrize("codificacion", ["utf-8", "cp1252"])
def test_extrae_texto_plano_en_varias_codificaciones(codificacion):
    assert "atención médica" in extractores.extraer_texto("faq.txt", "Horario de atención médica de lunes a viernes.".encode(codificacion))


def test_extrae_markdown():
    assert "Horarios" in extractores.extraer_texto("faq.md", b"# Horarios\n\nAtendemos de lunes a viernes.")


@pytest.mark.parametrize("nombre, datos, esperado", [
    ("a.exe", b"MZ" + b"x" * 50, "tipo_no_soportado"),
    ("a.txt", b"", "archivo_vacio"),
    ("a.txt", b"abc\x00\x01" + b"x" * 100, "archivo_invalido"),
    ("a.pdf", b"esto no es un pdf" * 5, "archivo_invalido"),
    ("a.docx", b"esto no es word" * 5, "archivo_invalido"),
    ("a.txt", b"corto", "sin_texto"),
])
def test_archivos_invalidos(nombre, datos, esperado):
    assert codigo(extractores.extraer_texto, nombre, datos) == esperado


def test_pdf_sin_texto_explica_que_falta_ocr():
    w = PdfWriter()
    w.add_blank_page(612, 792)
    b = io.BytesIO()
    w.write(b)
    with pytest.raises(ErrorNegocio) as e:
        extractores.extraer_texto("escaneado.pdf", b.getvalue())
    assert e.value.codigo == "sin_texto" and "OCR" in e.value.mensaje


def test_pdf_protegido_con_contrasena():
    w = PdfWriter()
    w.add_blank_page(612, 792)
    w.encrypt("secreta")
    b = io.BytesIO()
    w.write(b)
    assert codigo(extractores.extraer_texto, "protegido.pdf", b.getvalue()) == "pdf_protegido"


def test_docx_bomba_zip_se_rechaza():
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"\0" * (101 * 1024 * 1024))
    assert len(b.getvalue()) < 1024 * 1024  # pesa poco comprimido, pero se descomprime enorme
    assert codigo(extractores.extraer_texto, "bomba.docx", b.getvalue()) == "archivo_invalido"
