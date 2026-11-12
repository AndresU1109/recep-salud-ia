// Agenda médica: horarios semanales, bloqueos y agenda del día.
import { api } from "../api.js";
import { vistaCrud } from "../crud.js";
import { aIsoEnZona, fechaHora, hhmm, hoyEn, hora } from "../tiempo.js";
import { aviso, formModal, h, insignia, tabla } from "../ui.js";

const DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];
const opciones = (lista, texto = (x) => x.nombre) => lista.map((x) => ({ value: x.id, label: texto(x) }));
const nombreDe = (lista, id, campo = "nombre") => lista.find((x) => x.id === id)?.[campo] ?? "—";
const gestiona = (ctx) => ctx.usuario.permisos.includes("agenda:gestionar");

// ---------- horarios ----------
export const horarios = (ctx) => vistaCrud({
  titulo: "Horarios", descripcion: "Jornada semanal de cada profesional. De aquí salen los cupos disponibles.", ruta: "/horarios", campoActivo: "activo",
  nuevoTexto: "Nuevo horario", puedeGestionar: gestiona(ctx), nombreFila: (f) => `${DIAS[f.dia_semana]} ${hhmm(f.hora_inicio)}–${hhmm(f.hora_fin)}`,
  cargarExtras: async () => {
    const [profesionales, especialidades, sedes, consultorios] = await Promise.all([api("/profesionales"), api("/especialidades"), api("/sedes"), api("/consultorios")]);
    return { profesionales, especialidades, sedes, consultorios };
  },
  textoFila: (f, c) => `${nombreDe(c.profesionales, f.profesional_id, "nombre_completo")} ${DIAS[f.dia_semana]} ${nombreDe(c.sedes, f.sede_id)}`,
  columnas: [
    { titulo: "Profesional", render: (f, c) => h("div", {}, h("strong", {}, nombreDe(c.profesionales, f.profesional_id, "nombre_completo")), h("div", { class: "small text-body-secondary" }, nombreDe(c.especialidades, f.especialidad_id))) },
    { titulo: "Día y franja", render: (f) => `${DIAS[f.dia_semana]} ${hhmm(f.hora_inicio)}–${hhmm(f.hora_fin)}` },
    { titulo: "Cita", render: (f) => `${f.duracion_cita_min} min${f.cupos_maximos ? ` · máx. ${f.cupos_maximos}` : ""}` },
    { titulo: "Lugar", render: (f, c) => `${nombreDe(c.sedes, f.sede_id)}${f.consultorio_id ? ` · ${nombreDe(c.consultorios, f.consultorio_id)}` : ""}` },
    { titulo: "Vigencia", render: (f) => `${f.vigente_desde}${f.vigente_hasta ? ` → ${f.vigente_hasta}` : " →"}` },
  ],
  normalizar: (f) => ({ ...f, hora_inicio: hhmm(f.hora_inicio), hora_fin: hhmm(f.hora_fin) }),
  campos: (c) => [
    { name: "profesional_id", label: "Profesional", type: "select", numero: true, required: true, soloCrear: true, options: opciones(c.profesionales.filter((p) => p.activo), (p) => p.nombre_completo) },
    { name: "especialidad_id", label: "Especialidad", type: "select", numero: true, required: true, soloCrear: true, options: [],
      depende: { de: "profesional_id", opciones: (pid) => opciones(c.profesionales.find((p) => p.id === pid)?.especialidades ?? []) } },
    { name: "sede_id", label: "Sede", type: "select", numero: true, required: true, options: opciones(c.sedes.filter((s) => s.activa)) },
    { name: "consultorio_id", label: "Consultorio (opcional)", type: "select", numero: true, options: [],
      depende: { de: "sede_id", opciones: (sid) => opciones(c.consultorios.filter((k) => k.sede_id === sid && k.activo)) } },
    { name: "dia_semana", label: "Día de la semana", type: "select", numero: true, required: true, options: DIAS.map((d, i) => ({ value: i, label: d })) },
    { name: "hora_inicio", label: "Hora de inicio", type: "time", required: true }, { name: "hora_fin", label: "Hora de fin", type: "time", required: true },
    { name: "duracion_cita_min", label: "Duración de cada cita (minutos)", type: "number", required: true, min: 5, max: 240 },
    { name: "cupos_maximos", label: "Máximo de citas por jornada (opcional)", type: "number", min: 1, max: 500 },
    { name: "vigente_desde", label: "Vigente desde", type: "date" }, { name: "vigente_hasta", label: "Vigente hasta (opcional)", type: "date" },
    { name: "activo", label: "Horario activo", type: "checkbox", defecto: true },
  ],
})(ctx);

// ---------- bloqueos ----------
export async function bloqueos(ctx) {
  const zona = ctx.config.zona;
  const [profesionales, sedes] = await Promise.all([api("/profesionales"), api("/sedes")]);
  let lista = await api("/excepciones-agenda");
  const zonaTabla = h("div", { class: "card-body p-0" });
  const alcance = (e) => e.profesional_id ? nombreDe(profesionales, e.profesional_id, "nombre_completo") : e.sede_id ? `Toda la sede ${nombreDe(sedes, e.sede_id)}` : "Toda la institución";

  async function recargar() { lista = await api("/excepciones-agenda"); pintar(); }
  function pintar() {
    const cols = [{ titulo: "Aplica a", render: (e) => h("strong", {}, alcance(e)) }, { titulo: "Desde", render: (e) => fechaHora(e.inicio, zona) },
                  { titulo: "Hasta", render: (e) => fechaHora(e.fin, zona) }, { titulo: "Motivo", render: (e) => e.motivo }];
    if (gestiona(ctx)) cols.push({ titulo: "", render: (e) => h("button", { class: "btn btn-sm btn-outline-danger", type: "button", onClick: async () => {
      if (!confirm("¿Quitar este bloqueo? Los cupos volverán a estar disponibles.")) return;
      try { await api(`/excepciones-agenda/${e.id}`, { metodo: "DELETE" }); aviso("Bloqueo eliminado."); await recargar(); } catch (err) { aviso(err.message, "danger"); }
    } }, "Quitar") });
    zonaTabla.replaceChildren(tabla(cols, lista, "No hay bloqueos vigentes ni futuros."));
  }
  function nuevo() {
    formModal({
      titulo: "Nuevo bloqueo de agenda", guardarTexto: "Bloquear",
      campos: [{ name: "profesional_id", label: "Profesional (vacío = todos)", type: "select", numero: true, options: opciones(profesionales, (p) => p.nombre_completo) },
               { name: "sede_id", label: "Sede (vacío = todas)", type: "select", numero: true, options: opciones(sedes) },
               { name: "inicio", label: `Desde (hora de ${zona})`, type: "datetime-local", required: true }, { name: "fin", label: `Hasta (hora de ${zona})`, type: "datetime-local", required: true },
               { name: "motivo", label: "Motivo", required: true, max: 250, ayuda: "Vacaciones, festivo, capacitación, mantenimiento…" }],
      onGuardar: async (v) => {
        const r = await api("/excepciones-agenda", { metodo: "POST", cuerpo: { ...v, inicio: aIsoEnZona(v.inicio, zona), fin: aIsoEnZona(v.fin, zona) } });
        aviso("Bloqueo creado.");
        if (r.citas_afectadas) aviso(`Atención: hay ${r.citas_afectadas} cita(s) activa(s) dentro del bloqueo. No se cancelan solas; reprográmalas o cancélalas.`, "warning");
        await recargar();
      },
    });
  }
  pintar();
  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Bloqueos de agenda"), h("p", { class: "text-body-secondary mb-0" }, "Vacaciones, festivos y cierres. Los cupos bloqueados no se ofrecen a los pacientes.")),
      gestiona(ctx) ? h("button", { class: "btn btn-primary", type: "button", onClick: nuevo }, "+ Nuevo bloqueo") : null),
    h("div", { class: "card" }, zonaTabla));
}

// ---------- agenda del día ----------
const CLASE = { LIBRE: "slot-libre", OCUPADO: "slot-ocupado", BLOQUEADO: "slot-bloqueado", SIN_CUPO: "slot-sincupo", PASADO: "slot-pasado" };
const TEXTO = { LIBRE: "Libre", OCUPADO: "Ocupado", BLOQUEADO: "Bloqueado", SIN_CUPO: "Sin cupo (máximo de la jornada)", PASADO: "Pasado" };

export async function agendaDia(ctx) {
  const zona = ctx.config.zona;
  const [profesionales, sedes] = await Promise.all([api("/profesionales"), api("/sedes")]);
  const fecha = h("input", { class: "form-control", type: "date", id: "fecha", value: hoyEn(zona), "aria-label": "Fecha" });
  const prof = h("select", { class: "form-select", "aria-label": "Profesional" }, h("option", { value: "" }, "Todos los profesionales"), profesionales.filter((p) => p.activo).map((p) => h("option", { value: p.id }, p.nombre_completo)));
  const sede = h("select", { class: "form-select", "aria-label": "Sede" }, h("option", { value: "" }, "Todas las sedes"), sedes.map((s) => h("option", { value: s.id }, s.nombre)));
  const zonaSlots = h("div", {});

  async function cargar() {
    if (!fecha.value) return;
    const q = new URLSearchParams({ fecha: fecha.value });
    if (prof.value) q.set("profesional_id", prof.value);
    if (sede.value) q.set("sede_id", sede.value);
    try {
      const slots = await api(`/agenda/dia?${q}`);
      const grupos = new Map();
      for (const s of slots) { const k = `${s.profesional_id}|${s.sede_id}|${s.consultorio_id}`; (grupos.get(k) ?? grupos.set(k, []).get(k)).push(s); }
      if (!grupos.size) { zonaSlots.replaceChildren(h("div", { class: "alert alert-secondary" }, "Nadie tiene horario configurado para este día.")); return; }
      zonaSlots.replaceChildren(...[...grupos.values()].map((g) => {
        const libres = g.filter((s) => s.estado === "LIBRE").length;
        return h("div", { class: "card mb-3" },
          h("div", { class: "card-header d-flex flex-wrap justify-content-between gap-2" },
            h("div", {}, h("strong", {}, g[0].profesional), " · ", g[0].sede, g[0].consultorio ? ` · ${g[0].consultorio}` : ""), insignia(`${libres} libres de ${g.length}`, libres ? "success" : "secondary")),
          h("div", { class: "card-body d-flex flex-wrap gap-2" }, g.map((s) =>
            h("span", { class: `slot ${CLASE[s.estado]}`, title: `${hora(s.inicio, zona)}–${hora(s.fin, zona)} · ${TEXTO[s.estado]}${s.detalle ? ` (${s.detalle})` : ""}` }, hora(s.inicio, zona)))));
      }));
    } catch (e) { zonaSlots.replaceChildren(h("div", { class: "alert alert-danger" }, e.message)); }
  }
  [fecha, prof, sede].forEach((el) => el.addEventListener("change", cargar));
  await cargar();
  const leyenda = Object.keys(CLASE).map((k) => h("span", { class: "d-inline-flex align-items-center gap-1 me-3 small" }, h("span", { class: `slot slot-mini ${CLASE[k]}` }), TEXTO[k].split(" (")[0]));
  return h("section", {},
    h("h1", { class: "h3 mb-1" }, "Agenda del día"), h("p", { class: "text-body-secondary" }, "Pasa el cursor sobre un cupo para ver su detalle. Horas de " + zona + "."),
    h("div", { class: "row g-2 mb-3" }, h("div", { class: "col-12 col-md-3" }, fecha), h("div", { class: "col-12 col-md-5" }, prof), h("div", { class: "col-12 col-md-4" }, sede)),
    h("div", { class: "mb-3" }, leyenda), zonaSlots);
}
