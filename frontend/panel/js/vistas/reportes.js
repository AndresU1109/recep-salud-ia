// Reportes agregados (sin datos de pacientes) y su descarga en Excel o PDF.
import { api, descargar } from "../api.js";
import { aviso, h, tabla } from "../ui.js";
import { hoyEn } from "../tiempo.js";

function restarDias(iso, dias) {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() - dias);
  return d.toISOString().slice(0, 10);
}

export async function reportes(ctx) {
  const zona = ctx.config.zona;
  const disponibles = await api("/reportes");
  const hoy = hoyEn(zona);
  const clave = h("select", { class: "form-select", id: "reporte", "aria-label": "Reporte" }, disponibles.map((r) => h("option", { value: r.clave }, r.titulo)));
  const desde = h("input", { class: "form-control", type: "date", id: "desde", value: restarDias(hoy, 29), "aria-label": "Desde" });
  const hasta = h("input", { class: "form-control", type: "date", id: "hasta", value: hoy, "aria-label": "Hasta" });
  const cuerpo = h("div", { class: "mt-3" });
  const botonesDescarga = h("div", { class: "d-flex gap-2" });

  const parametros = () => `desde=${desde.value}&hasta=${hasta.value}`;

  async function ver() {
    cuerpo.replaceChildren(h("p", { class: "text-body-secondary" }, "Calculando…"));
    try {
      const r = await api(`/reportes/${clave.value}?${parametros()}`);
      cuerpo.replaceChildren(
        h("p", { class: "text-body-secondary small" }, `Período: ${r.desde} a ${r.hasta} (hora de la institución).`),
        h("div", { class: "card mb-3" }, h("div", { class: "card-header" }, "Indicadores"), h("div", { class: "card-body p-0" }, tabla([{ titulo: "Indicador", render: (f) => f[0] }, { titulo: "Valor", render: (f) => h("strong", {}, String(f[1])) }], r.indicadores))),
        ...r.tablas.map((t) => h("div", { class: "card mb-3" }, h("div", { class: "card-header" }, t.titulo),
          h("div", { class: "card-body p-0" }, tabla(t.columnas.map((titulo, i) => ({ titulo, render: (f) => String(f[i]) })), t.filas, "Sin datos en el período.")))),
        r.notas.length ? h("ul", { class: "small text-body-secondary" }, r.notas.map((n) => h("li", {}, n))) : null);
    } catch (e) { cuerpo.replaceChildren(); aviso(e.message, "danger"); }
  }

  async function bajar(formato) {
    try { await descargar(`/reportes/${clave.value}/exportar?formato=${formato}&${parametros()}`, `reporte-${clave.value}-${desde.value}-${hasta.value}.${formato}`); }
    catch (e) { aviso(e.message, "danger"); }
  }

  botonesDescarga.append(h("button", { class: "btn btn-outline-success", type: "button", onClick: () => bajar("xlsx") }, "Descargar Excel"),
                         h("button", { class: "btn btn-outline-danger", type: "button", onClick: () => bajar("pdf") }, "Descargar PDF"));
  clave.addEventListener("change", ver); desde.addEventListener("change", ver); hasta.addEventListener("change", ver);
  await ver();
  return h("section", {},
    h("h1", { class: "h3 mb-1" }, "Reportes"),
    h("p", { class: "text-body-secondary" }, "Cifras agregadas: ningún reporte trae nombres, documentos ni contactos de pacientes. Cada descarga queda registrada en la auditoría."),
    h("div", { class: "row g-2 align-items-end" },
      h("div", { class: "col-12 col-md-4" }, h("label", { class: "form-label", for: "reporte" }, "Reporte"), clave),
      h("div", { class: "col-6 col-md-2" }, h("label", { class: "form-label", for: "desde" }, "Desde"), desde),
      h("div", { class: "col-6 col-md-2" }, h("label", { class: "form-label", for: "hasta" }, "Hasta"), hasta),
      h("div", { class: "col-12 col-md-4 d-flex justify-content-md-end" }, botonesDescarga)),
    cuerpo);
}
