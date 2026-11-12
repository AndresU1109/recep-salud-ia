// Base de conocimiento: documentos y preguntas frecuentes.
import { api, descargar } from "../api.js";
import { vistaCrud } from "../crud.js";
import { aviso, formModal, h, insignia, tabla, ventana } from "../ui.js";

const CATEGORIAS = { REGLAMENTO: "Reglamento", PROTOCOLO: "Protocolo", MANUAL: "Manual", PREGUNTAS_FRECUENTES: "Preguntas frecuentes", ESPECIALIDAD: "Especialidad",
                     REQUISITOS: "Requisitos", POLITICA: "Política", SERVICIOS: "Servicios", OTRO: "Otro" };
const ESTADO = { BORRADOR: ["Borrador", "warning"], ACTIVO: ["Activo", "success"], INACTIVO: ["Inactivo", "secondary"] };
const VIGENCIA = { vigente: ["Vigente", "success"], pendiente: ["Aún no vigente", "info"], vencido: ["Vencido", "danger"], sin_fecha: ["Sin fecha", "secondary"] };
const opciones = (lista) => lista.map((x) => ({ value: x.id, label: x.nombre }));
const nombreDe = (lista, id) => lista.find((x) => x.id === id)?.nombre ?? "—";

function camposMetadatos(sedes, especialidades, conArchivo) {
  const c = [{ name: "titulo", label: "Título", required: true, max: 250 }, { name: "categoria", label: "Categoría", type: "select", required: true, options: Object.entries(CATEGORIAS).map(([value, label]) => ({ value, label })) },
             { name: "fuente", label: "Fuente", required: true, max: 250, ayuda: "De dónde sale: área, norma, resolución…" }, { name: "version", label: "Versión", required: true, max: 30 },
             { name: "vigente_desde", label: "Vigente desde", type: "date" }, { name: "vigente_hasta", label: "Vigente hasta", type: "date" },
             { name: "sede_id", label: "Solo para una sede (opcional)", type: "select", numero: true, options: opciones(sedes) },
             { name: "especialidad_id", label: "Solo para una especialidad (opcional)", type: "select", numero: true, options: opciones(especialidades) }];
  if (conArchivo) c.push({ name: "archivo", label: "Archivo (PDF, DOCX, TXT o MD)", type: "file", accept: ".pdf,.docx,.txt,.md", ayuda: "O pega el texto abajo. Máximo 15 MB. No subas documentos con datos de pacientes." },
                         { name: "texto", label: "Texto (si no subes archivo)", type: "textarea" });
  return c;
}

function aFormData(valores) {
  const f = new FormData();
  for (const [k, v] of Object.entries(valores)) if (v !== null && v !== undefined && v !== "") f.append(k, v);
  return f;
}

export async function documentos(ctx) {
  const [sedes, especialidades] = await Promise.all([api("/sedes"), api("/especialidades")]);
  let filtros = { estado: "", categoria: "", q: "" };
  const zonaTabla = h("div", { class: "card-body p-0" });
  const sel = (nombre, etiqueta, opts) => h("select", { class: "form-select", "aria-label": etiqueta, onChange: (e) => { filtros[nombre] = e.target.value; cargar(); } },
    h("option", { value: "" }, etiqueta), opts.map(([v, t]) => h("option", { value: v }, t)));
  const buscar = h("input", { class: "form-control", type: "search", placeholder: "Buscar por título", "aria-label": "Buscar", onInput: (e) => { filtros.q = e.target.value; clearTimeout(buscar._t); buscar._t = setTimeout(cargar, 300); } });

  async function cargar() {
    const q = new URLSearchParams(Object.entries(filtros).filter(([, v]) => v));
    const lista = await api(`/documentos?${q}`);
    zonaTabla.replaceChildren(tabla([
      { titulo: "Documento", render: (d) => h("div", {}, h("strong", {}, d.titulo), h("div", { class: "small text-body-secondary" }, `v${d.version} · ${d.fuente}`)) },
      { titulo: "Categoría", render: (d) => CATEGORIAS[d.categoria] },
      { titulo: "Aplica a", render: (d) => [d.sede_id ? nombreDe(sedes, d.sede_id) : null, d.especialidad_id ? nombreDe(especialidades, d.especialidad_id) : null].filter(Boolean).join(" · ") || "Todas" },
      { titulo: "Vigencia", render: (d) => insignia(...VIGENCIA[d.vigencia]) }, { titulo: "Estado", render: (d) => insignia(...ESTADO[d.estado]) },
      { titulo: "Índice", render: (d) => d.indexado ? insignia("Indexado", "success") : insignia("Sin indexar", "warning") },
      { titulo: "", render: (d) => h("button", { class: "btn btn-sm btn-outline-primary", type: "button", onClick: () => abrir(d.id) }, "Abrir") },
    ], lista, "Aún no hay documentos. Sube el primero."));
  }

  function subir() {
    formModal({
      titulo: "Subir documento", guardarTexto: "Subir", campos: camposMetadatos(sedes, especialidades, true), valores: { version: "1" },
      onGuardar: async (v) => {
        const d = await api("/documentos", { metodo: "POST", cuerpo: aFormData(v) });
        aviso("Documento cargado como borrador.");
        for (const a of d.advertencias) aviso(a, "warning");
        await cargar();
      },
    });
  }

  async function abrir(id) {
    const { cuerpo } = ventana("Documento", { grande: true });
    async function pintar(avisos = []) {
      const d = await api(`/documentos/${id}`);
      const dato = (k, v) => h("div", { class: "col-sm-6" }, h("div", { class: "small text-body-secondary" }, k), h("div", {}, v ?? "—"));
      const acciones = [
        d.estado === "ACTIVO" ? h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onClick: () => estado("desactivar") }, "Desactivar")
          : h("button", { class: "btn btn-sm btn-success", type: "button", onClick: () => estado("activar") }, "Activar"),
        h("button", { class: "btn btn-sm btn-outline-primary", type: "button", onClick: () => editar(d) }, "Editar datos"),
        h("button", { class: "btn btn-sm btn-outline-primary", type: "button", onClick: () => nuevaVersion(d) }, "Nueva versión"),
        d.archivo_nombre ? h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onClick: async () => { try { await descargar(`/documentos/${id}/archivo`, d.archivo_nombre); } catch (e) { aviso(e.message, "danger"); } } }, "Descargar original") : null];
      cuerpo.replaceChildren(
        h("div", { class: "d-flex flex-wrap justify-content-between gap-2 mb-2" }, h("h3", { class: "h5 mb-0" }, d.titulo), h("div", { class: "d-flex gap-1" }, insignia(...ESTADO[d.estado]), insignia(...VIGENCIA[d.vigencia]))),
        ...avisos.map((a) => h("div", { class: "alert alert-warning py-2" }, a)),
        h("div", { class: "row g-3" }, dato("Categoría", CATEGORIAS[d.categoria]), dato("Versión", d.version), dato("Fuente", d.fuente), dato("Archivo", d.archivo_nombre ?? "Texto pegado"),
          dato("Vigencia", `${d.vigente_desde ?? "—"} → ${d.vigente_hasta ?? "—"}`), dato("Aplica a", [d.sede_id ? nombreDe(sedes, d.sede_id) : null, d.especialidad_id ? nombreDe(especialidades, d.especialidad_id) : null].filter(Boolean).join(" · ") || "Todas"),
          dato("Fragmentos", `${d.n_fragmentos} (${d.n_indexados} indexados) · ${d.caracteres.toLocaleString("es-CO")} caracteres`), dato("Modelo de embeddings", d.embedding_model ?? "Sin indexar")),
        h("div", { class: "d-flex flex-wrap gap-2 mt-3" }, acciones),
        h("h4", { class: "h6 mt-4" }, "Fragmentos que verá el asistente"),
        h("div", { class: "vstack gap-2" }, d.fragmentos.map((f) => h("div", { class: "border rounded p-2" }, h("div", { class: "small text-body-secondary mb-1" }, `#${f.orden + 1} · ~${f.tokens ?? "?"} tokens`, " ", f.indexado ? insignia("indexado", "success") : null),
          h("div", { class: "small texto-fragmento" }, f.contenido)))));
    }
    async function estado(accion) { try { const r = await api(`/documentos/${id}/${accion}`, { metodo: "POST" }); aviso(accion === "activar" ? "Documento activado." : "Documento desactivado."); await pintar(r.advertencias); await cargar(); } catch (e) { aviso(e.message, "danger"); } }
    function editar(d) {
      formModal({ titulo: "Editar datos del documento", valores: d, campos: camposMetadatos(sedes, especialidades, false),
        onGuardar: async (v) => { await api(`/documentos/${id}`, { metodo: "PATCH", cuerpo: v }); aviso("Datos guardados."); await pintar(); await cargar(); } });
    }
    function nuevaVersion(d) {
      formModal({ titulo: `Nueva versión de «${d.titulo}»`, guardarTexto: "Crear borrador",
        campos: [{ name: "version", label: "Número de la nueva versión", required: true, max: 30 }, { name: "archivo", label: "Archivo", type: "file", accept: ".pdf,.docx,.txt,.md" }, { name: "texto", label: "O texto", type: "textarea" }],
        onGuardar: async (v) => { const r = await api(`/documentos/${id}/nueva-version`, { metodo: "POST", cuerpo: aFormData(v) }); aviso("Nueva versión creada como borrador. Actívala cuando esté lista y desactiva la anterior."); for (const a of r.advertencias) aviso(a, "warning"); await cargar(); } });
    }
    try { await pintar(); } catch (e) { cuerpo.replaceChildren(h("div", { class: "alert alert-danger" }, e.message)); }
  }

  await cargar();
  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Documentos"), h("p", { class: "text-body-secondary mb-0" }, "Reglamentos, protocolos y manuales que el asistente puede consultar. Solo los documentos activos e indexados se usan.")),
      h("button", { class: "btn btn-primary", type: "button", onClick: subir }, "+ Subir documento")),
    h("div", { class: "card" }, h("div", { class: "card-header" }, h("div", { class: "row g-2" },
      h("div", { class: "col-md-4" }, buscar), h("div", { class: "col-md-4" }, sel("estado", "Todos los estados", Object.entries(ESTADO).map(([k, [t]]) => [k, t]))),
      h("div", { class: "col-md-4" }, sel("categoria", "Todas las categorías", Object.entries(CATEGORIAS))))), zonaTabla));
}

export const preguntasFrecuentes = (ctx) => vistaCrud({
  titulo: "Preguntas frecuentes", descripcion: "Respuestas oficiales que el asistente usa y cita. Cada cambio actualiza su documento de conocimiento.", ruta: "/faqs", campoActivo: "activa",
  nuevoTexto: "Nueva pregunta", puedeGestionar: ctx.usuario.permisos.includes("documentos:gestionar"), nombreFila: (f) => f.pregunta,
  cargarExtras: async () => { const [sedes, especialidades] = await Promise.all([api("/sedes"), api("/especialidades")]); return { sedes, especialidades }; },
  textoFila: (f) => `${f.pregunta} ${f.respuesta} ${f.categoria ?? ""}`,
  columnas: [{ titulo: "Pregunta", render: (f) => h("div", {}, h("strong", {}, f.pregunta), h("div", { class: "small text-body-secondary" }, f.respuesta.length > 140 ? `${f.respuesta.slice(0, 140)}…` : f.respuesta)) },
             { titulo: "Categoría", render: (f) => f.categoria ?? "—" }, { titulo: "Aplica a", render: (f, c) => [f.sede_id ? nombreDe(c.sedes, f.sede_id) : null, f.especialidad_id ? nombreDe(c.especialidades, f.especialidad_id) : null].filter(Boolean).join(" · ") || "Todas" }],
  campos: (c) => [{ name: "pregunta", label: "Pregunta", required: true, max: 400 }, { name: "respuesta", label: "Respuesta", type: "textarea", required: true, max: 4000, ayuda: "Escríbela como quieres que la reciba el paciente." },
                  { name: "categoria", label: "Categoría", max: 100 }, { name: "especialidad_id", label: "Solo para una especialidad (opcional)", type: "select", numero: true, options: opciones(c.especialidades) },
                  { name: "sede_id", label: "Solo para una sede (opcional)", type: "select", numero: true, options: opciones(c.sedes) },
                  { name: "orden", label: "Orden", type: "number", min: 0, max: 999 }, { name: "activa", label: "Pregunta activa", type: "checkbox", defecto: true }],
})(ctx);
