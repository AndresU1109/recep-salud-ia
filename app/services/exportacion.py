"""Exportar reportes a Excel (.xlsx) y PDF. Cuidado con lo que viene de la base de datos:
- Excel: una celda de texto que empieza por = + - @ (o tabulador/retorno) se interpreta como FÓRMULA al abrir el archivo; se neutraliza con un apóstrofo.
- PDF: el texto de ReportLab admite marcas (<b>, <font>…); se escapa para que un nombre no pueda inyectar nada."""
import io
import re
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.config import get_settings
from app.services.reporte_service import Reporte, Tabla

MAX_FILAS_EXCEL = 50_000  # límite de seguridad (Excel admite 1 048 576)
_PELIGROSO = ("=", "+", "-", "@", "\t", "\r")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")  # openpyxl rechaza estos caracteres


def seguro_excel(valor: Any) -> Any:
    if isinstance(valor, str):
        valor = _CONTROL.sub("", valor)
        return "'" + valor if valor.startswith(_PELIGROSO) else valor
    if isinstance(valor, datetime) and valor.tzinfo:
        return valor.replace(tzinfo=None)  # Excel no guarda zona horaria: se entrega ya en la hora de la institución
    return valor


def _nombre_hoja(titulo: str, usados: set[str]) -> str:
    base = re.sub(r"[\[\]\*\?/\\:]", " ", titulo).strip()[:28] or "Hoja"
    nombre, i = base, 2
    while nombre.lower() in usados:
        nombre, i = f"{base[:25]} {i}", i + 1
    usados.add(nombre.lower())
    return nombre


def _estilo_encabezado(hoja, fila: int, columnas: int) -> None:
    for c in range(1, columnas + 1):
        celda = hoja.cell(row=fila, column=c)
        celda.font, celda.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F4E79")
        celda.alignment = Alignment(vertical="center", wrap_text=True)


def _ajustar_anchos(hoja, minimo=10, maximo=60) -> None:
    for col in hoja.columns:
        ancho = max((len(str(c.value)) for c in col if c.value is not None), default=0)
        hoja.column_dimensions[get_column_letter(col[0].column)].width = min(max(ancho + 2, minimo), maximo)


def _pie(r: Reporte) -> str:
    return f"{get_settings().institution_name} · generado {r.generado_en.astimezone(_tz()).strftime('%Y-%m-%d %H:%M')}"


def _tz():
    from app.services import agenda_service

    return agenda_service._tz()


# ======================= Excel =======================
def reporte_a_excel(r: Reporte) -> bytes:
    libro, usados = Workbook(), set()
    resumen = libro.active
    resumen.title = _nombre_hoja("Resumen", usados)
    resumen["A1"], resumen["A1"].font = seguro_excel(r.titulo), Font(bold=True, size=14)
    resumen["A2"] = f"Período: {r.desde.isoformat()} a {r.hasta.isoformat()}"
    resumen["A3"] = seguro_excel(_pie(r))
    resumen.append([])
    resumen.append(["Indicador", "Valor"])
    _estilo_encabezado(resumen, 5, 2)
    for nombre, valor in r.indicadores:
        resumen.append([seguro_excel(nombre), seguro_excel(valor)])
    if r.notas:
        resumen.append([])
        resumen.append(["Notas"])
        resumen.cell(row=resumen.max_row, column=1).font = Font(bold=True)
        for nota in r.notas:
            resumen.append([seguro_excel(nota)])
    _ajustar_anchos(resumen, maximo=70)
    for t in r.tablas:
        hoja = libro.create_sheet(_nombre_hoja(t.titulo, usados))
        hoja.append([seguro_excel(c) for c in t.columnas])
        _estilo_encabezado(hoja, 1, len(t.columnas))
        for fila in t.filas:
            hoja.append([seguro_excel(v) for v in fila])
        hoja.freeze_panes = "A2"
        _ajustar_anchos(hoja)
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


def filas_a_excel(titulo: str, columnas: list[str], filas: list[list[Any]], notas: list[str] | None = None) -> bytes:
    """Una sola hoja con muchas filas (auditoría). Escribe en modo de solo escritura para no cargar todo en memoria dos veces."""
    libro = Workbook(write_only=True)
    hoja = libro.create_sheet(_nombre_hoja(titulo, set()))
    for nota in notas or []:
        hoja.append([seguro_excel(nota)])
    hoja.append([seguro_excel(c) for c in columnas])
    for fila in filas[:MAX_FILAS_EXCEL]:
        hoja.append([seguro_excel(v) for v in fila])
    salida = io.BytesIO()
    libro.save(salida)
    return salida.getvalue()


# ======================= PDF =======================
def _p(texto: Any, estilo) -> Paragraph:
    return Paragraph(escape(str(texto)), estilo)


def reporte_a_pdf(r: Reporte) -> bytes:
    estilos = getSampleStyleSheet()
    celda = ParagraphStyle("celda", parent=estilos["BodyText"], fontSize=8.5, leading=10.5)
    celda_b = ParagraphStyle("celda_b", parent=celda, fontName="Helvetica-Bold", textColor=colors.white)
    pie = _pie(r)

    def numerar(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(1.5 * cm, 1 * cm, pie)
        canvas.drawRightString(landscape(A4)[0] - 1.5 * cm, 1 * cm, f"Página {doc.page}")
        canvas.restoreState()

    salida = io.BytesIO()
    doc = SimpleDocTemplate(salida, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm, topMargin=1.5 * cm, bottomMargin=1.8 * cm,
                            title=r.titulo, author=get_settings().institution_name)
    ancho = landscape(A4)[0] - 3 * cm
    tabla_estilo = TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E79")), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#BBBBBB")),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F6FA")]), ("VALIGN", (0, 0), (-1, -1), "TOP")])
    historia = [_p(r.titulo, estilos["Title"]), _p(f"Período: {r.desde.isoformat()} a {r.hasta.isoformat()}", estilos["Normal"]), Spacer(1, 0.4 * cm)]

    def bloque(t: Tabla | None, columnas: list[str], filas: list[list[Any]], anchos: list[float] | None = None):
        datos = [[_p(c, celda_b) for c in columnas]] + [[_p(v, celda) for v in f] for f in filas]
        tabla = Table(datos, colWidths=anchos, repeatRows=1)
        tabla.setStyle(tabla_estilo)
        return tabla

    historia += [_p("Indicadores", estilos["Heading2"]), bloque(None, ["Indicador", "Valor"], [[a, b] for a, b in r.indicadores], [ancho * 0.75, ancho * 0.25])]
    for t in r.tablas:
        historia += [Spacer(1, 0.4 * cm), _p(t.titulo, estilos["Heading3"])]
        historia.append(bloque(t, t.columnas, t.filas) if t.filas else _p("Sin datos en el período.", estilos["Italic"]))
    if r.notas:
        historia += [Spacer(1, 0.4 * cm), _p("Notas", estilos["Heading3"])] + [_p(f"- {n}", estilos["Normal"]) for n in r.notas]
    doc.build(historia, onFirstPage=numerar, onLaterPages=numerar)
    return salida.getvalue()
