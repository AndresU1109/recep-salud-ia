// Estado del índice semántico y prueba de búsqueda (lo mismo que verá el asistente).
import { api } from "../api.js";
import { aviso, h, insignia } from "../ui.js";

const CATEGORIAS = { REGLAMENTO: "Reglamento", PROTOCOLO: "Protocolo", MANUAL: "Manual", PREGUNTAS_FRECUENTES: "Preguntas frecuentes", ESPECIALIDAD: "Especialidad",
                     REQUISITOS: "Requisitos", POLITICA: "Política", SERVICIOS: "Servicios", OTRO: "Otro" };

export async function probarBusqueda(ctx) {
  const [sedes, especialidades] = await Promise.all([api("/sedes"), api("/especialidades")]);
  const zonaEstado = h("div", { class: "card-body" });
  const zonaResultados = h("div", { class: "mt-3" });
  let temporizador = null;

  async function pintarEstado() {
    const e = await api("/rag/estado");
    const d = e.documentos;
    const problemas = [];
    if (!e.dimension_consistente) problemas.push(`La columna de vectores es de ${e.dimension_columna} dimensiones y la configuración espera ${e.dimensiones}.`);
    if (e.ollama.estado !== "ok") problemas.push(`Ollama: ${e.ollama.detalle ?? e.ollama.estado}`);
    if (d.activos_sin_poder_buscarse) problemas.push(`${d.activos_sin_poder_buscarse} documento(s) activo(s) no se pueden buscar todavía: reindexa.`);
    const dato = (k, v) => h("div", { class: "col-6 col-md-3" }, h("div", { class: "small text-body-secondary" }, k), h("div", { class: "fw-semibold" }, v));
    zonaEstado.replaceChildren(
      ...problemas.map((p) => h("div", { class: "alert alert-warning py-2" }, p)),
      h("div", { class: "row g-3" }, dato("Modelo", e.modelo), dato("Dimensiones", `${e.dimensiones} (columna: ${e.dimension_columna ?? "—"})`),
        dato("Documentos indexados", `${d.indexados} de ${d.total}`), dato("Fragmentos", e.fragmentos),
        dato("Pendientes", d.pendientes), dato("Con otro modelo", d.con_otro_modelo), dato("Umbral de similitud", e.umbral)),
      h("div", { class: "d-flex flex-wrap gap-2 mt-3" },
        h("button", { class: "btn btn-sm btn-primary", type: "button", onClick: () => reindexar(true) }, "Indexar pendientes"),
        h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onClick: () => { if (confirm("Se recalcularán TODOS los vectores. Puede tardar. ¿Continuar?")) reindexar(false); } }, "Recalcular todo")),
      h("div", { id: "progreso", class: "mt-2 small text-body-secondary" }));
  }

  async function reindexar(soloPendientes) {
    try { await api("/rag/reindexar", { metodo: "POST", cuerpo: { solo_pendientes: soloPendientes } }); } catch (e) { aviso(e.message, "danger"); return; }
    clearInterval(temporizador);
    temporizador = setInterval(async () => {
      if (!document.getElementById("progreso")) { clearInterval(temporizador); return; }  // el usuario se fue de la pantalla
      const t = await api("/rag/reindexar/estado");
      document.getElementById("progreso").textContent = t.estado === "ejecutando" ? `Indexando… ${t.hechos} de ${t.total}` : `Terminado: ${t.hechos} de ${t.total}${t.errores.length ? ` (${t.errores.length} con error)` : ""}.`;
      if (t.estado !== "ejecutando") { clearInterval(temporizador); for (const e of t.errores) aviso(`Documento ${e.documento_id}: ${e.error}`, "danger"); await pintarEstado(); }
    }, 1200);
  }

  // ----- búsqueda -----
  const consulta = h("input", { class: "form-control", id: "consulta", maxlength: 500, placeholder: "Escribe como preguntaría un paciente…", required: true });
  const sede = h("select", { class: "form-select", "aria-label": "Sede" }, h("option", { value: "" }, "Cualquier sede"), sedes.map((s) => h("option", { value: s.id }, s.nombre)));
  const esp = h("select", { class: "form-select", "aria-label": "Especialidad" }, h("option", { value: "" }, "Cualquier especialidad"), especialidades.map((s) => h("option", { value: s.id }, s.nombre)));
  const cat = h("select", { class: "form-select", "aria-label": "Categoría" }, h("option", { value: "" }, "Cualquier categoría"), Object.entries(CATEGORIAS).map(([v, t]) => h("option", { value: v }, t)));

  async function buscar(ev) {
    ev.preventDefault();
    const cuerpo = { consulta: consulta.value };
    if (sede.value) cuerpo.sede_id = Number(sede.value);
    if (esp.value) cuerpo.especialidad_id = Number(esp.value);
    if (cat.value) cuerpo.categorias = [cat.value];
    zonaResultados.replaceChildren(h("div", { class: "spinner-border", role: "status" }));
    try {
      const r = await api("/rag/buscar", { metodo: "POST", cuerpo });
      zonaResultados.replaceChildren(
        r.suficiente ? h("div", { class: "alert alert-success py-2" }, `Hay información suficiente (${r.resultados.length} fuente(s)). Mejor similitud: ${r.mejor_score}.`)
          : h("div", { class: "alert alert-warning py-2" }, `Sin información suficiente: nada supera el umbral de ${r.umbral}${r.mejor_score !== null ? ` (lo mejor fue ${r.mejor_score})` : ""}. El asistente debe decir que no tiene esa información.`),
        ...r.resultados.map((x, i) => h("div", { class: "card mb-2" }, h("div", { class: "card-body" },
          h("div", { class: "d-flex flex-wrap justify-content-between gap-2 mb-1" },
            h("div", {}, h("strong", {}, `[${i + 1}] ${x.titulo}`), h("span", { class: "text-body-secondary small" }, ` · v${x.version} · ${x.fuente} · ${CATEGORIAS[x.categoria] ?? x.categoria}`)),
            h("div", { class: "d-flex gap-1 align-items-center" }, x.desactualizado ? insignia("Documento vencido", "danger") : null, (x.sede || x.especialidad) ? insignia([x.sede, x.especialidad].filter(Boolean).join(" · "), "info") : null,
              h("progress", { class: "barra-score", max: 1, value: Math.max(0, x.score), "aria-label": "Similitud" }), h("span", { class: "small fw-semibold" }, x.score.toFixed(3)))),
          h("div", { class: "small texto-fragmento" }, x.contenido)))));
    } catch (e) { zonaResultados.replaceChildren(h("div", { class: "alert alert-danger" }, e.message)); }
  }

  await pintarEstado();
  return h("section", {},
    h("h1", { class: "h3 mb-1" }, "Probar búsqueda"), h("p", { class: "text-body-secondary" }, "Comprueba qué fuentes encontraría el asistente para una pregunta, y mantén al día el índice."),
    h("div", { class: "card mb-3" }, h("div", { class: "card-header fw-semibold" }, "Estado del índice"), zonaEstado),
    h("form", { class: "card", onSubmit: buscar }, h("div", { class: "card-body" },
      h("label", { class: "form-label", for: "consulta" }, "Pregunta"), h("div", { class: "input-group mb-2" }, consulta, h("button", { class: "btn btn-primary", type: "submit" }, "Buscar")),
      h("div", { class: "row g-2" }, h("div", { class: "col-md-4" }, sede), h("div", { class: "col-md-4" }, esp), h("div", { class: "col-md-4" }, cat)))),
    zonaResultados);
}
