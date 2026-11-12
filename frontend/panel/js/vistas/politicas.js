// Políticas institucionales. El sistema solo aplica lo que se configure aquí; sin política no hay límites ni sanciones.
import { api } from "../api.js";
import { aviso, formModal, h, insignia, tabla } from "../ui.js";

const TIPOS = {
  CANCELACION: { nombre: "Cancelación", ayuda: "Anticipación mínima para cancelar una cita.", parametros: [{ clave: "horas_minimas_cancelar", label: "Horas mínimas de anticipación para cancelar" }] },
  REPROGRAMACION: { nombre: "Reprogramación", ayuda: "Límites para mover una cita.", parametros: [
    { clave: "horas_minimas_reprogramar", label: "Horas mínimas de anticipación para reprogramar" }, { clave: "maximo_reprogramaciones", label: "Máximo de reprogramaciones por cita" }] },
  INASISTENCIA: { nombre: "Inasistencia", ayuda: "Qué se informa al paciente y qué consecuencia (si la hay) autorizó la institución.", parametros: [
    { clave: "inasistencias_para_aviso", label: "Inasistencias verificadas a partir de las cuales se avisa al paciente" }], consecuencia: true },
  OTRA: { nombre: "Otra (informativa)", ayuda: "Texto informativo para los pacientes.", parametros: [] },
};

export async function politicas(ctx) {
  const gestiona = ctx.usuario.permisos.includes("politicas:gestionar");
  const [sedes, especialidades] = await Promise.all([api("/sedes"), api("/especialidades")]);
  const nombre = (l, id) => l.find((x) => x.id === id)?.nombre;
  const zonaTabla = h("div", { class: "card-body p-0" });

  async function cargar() {
    const lista = await api("/politicas");
    zonaTabla.replaceChildren(tabla([
      { titulo: "Política", render: (p) => h("div", {}, h("strong", {}, p.titulo), h("div", { class: "small text-body-secondary" }, TIPOS[p.tipo].nombre)) },
      { titulo: "Reglas", render: (p) => Object.entries(p.parametros).map(([k, v]) => h("div", { class: "small" }, `${TIPOS[p.tipo].parametros.find((x) => x.clave === k)?.label ?? k}: `, h("strong", {}, v))) },
      { titulo: "Aplica a", render: (p) => (p.especialidad_id || p.sede_id) ? [nombre(especialidades, p.especialidad_id), nombre(sedes, p.sede_id)].filter(Boolean).join(" · ") : "Toda la institución" },
      { titulo: "Vigencia", render: (p) => `${p.vigente_desde}${p.vigente_hasta ? ` → ${p.vigente_hasta}` : " →"}` },
      { titulo: "Estado", render: (p) => p.activa ? insignia("Activa", "success") : insignia("Inactiva", "secondary") },
      { titulo: "", render: (p) => gestiona ? h("button", { class: "btn btn-sm btn-outline-primary", type: "button", onClick: () => formulario(p.tipo, p) }, "Editar") : null },
    ], lista, "Aún no hay políticas. Sin políticas el sistema no aplica límites ni consecuencias, y el asistente dirá que no tiene esa información."));
  }

  function formulario(tipo, politica = null) {
    const t = TIPOS[tipo];
    const campos = [{ name: "titulo", label: "Título", required: true, max: 200 },
      ...t.parametros.map((p) => ({ name: `p_${p.clave}`, label: p.label, type: "number", min: 0, max: 100000 })),
      { name: "mensaje_paciente", label: "Mensaje para el paciente", type: "textarea", ayuda: "Lo que el asistente dirá tal cual cuando aplique esta política." }];
    if (t.consecuencia) campos.push({ name: "consecuencia_autorizada", label: "Consecuencia autorizada (opcional)", type: "textarea", ayuda: "Solo si la institución la autorizó. Nunca se aplica sola: una persona debe decidirlo caso por caso." });
    if (!politica) campos.push({ name: "especialidad_id", label: "Solo para una especialidad (opcional)", type: "select", numero: true, options: especialidades.map((e) => ({ value: e.id, label: e.nombre })) },
                               { name: "sede_id", label: "Solo para una sede (opcional)", type: "select", numero: true, options: sedes.map((s) => ({ value: s.id, label: s.nombre })) });
    campos.push({ name: "vigente_desde", label: "Vigente desde", type: "date" }, { name: "vigente_hasta", label: "Vigente hasta (opcional)", type: "date" },
                { name: "activa", label: "Política activa", type: "checkbox", defecto: true });
    const valores = politica ? { ...politica, ...Object.fromEntries(Object.entries(politica.parametros).map(([k, v]) => [`p_${k}`, v])) } : {};
    formModal({
      titulo: politica ? `Editar: ${politica.titulo}` : `Nueva política de ${t.nombre.toLowerCase()}`, valores, campos,
      onGuardar: async (v) => {
        const parametros = {};
        for (const p of t.parametros) if (v[`p_${p.clave}`] !== null) parametros[p.clave] = v[`p_${p.clave}`];
        const cuerpo = { titulo: v.titulo, parametros, mensaje_paciente: v.mensaje_paciente, vigente_desde: v.vigente_desde, vigente_hasta: v.vigente_hasta, activa: v.activa };
        if (t.consecuencia) cuerpo.consecuencia_autorizada = v.consecuencia_autorizada;
        if (politica) await api(`/politicas/${politica.id}`, { metodo: "PATCH", cuerpo });
        else await api("/politicas", { metodo: "POST", cuerpo: { ...cuerpo, tipo, especialidad_id: v.especialidad_id, sede_id: v.sede_id } });
        aviso("Política guardada."); await cargar();
      },
    });
  }

  await cargar();
  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Políticas institucionales"), h("p", { class: "text-body-secondary mb-0" }, "Cancelación, reprogramación e inasistencias. Si hay varias, gana la más específica (especialidad, luego sede, luego general).")),
      gestiona ? h("div", { class: "dropdown" }, h("button", { class: "btn btn-primary dropdown-toggle", type: "button", "data-bs-toggle": "dropdown", "aria-expanded": "false" }, "+ Nueva política"),
        h("ul", { class: "dropdown-menu dropdown-menu-end" }, Object.entries(TIPOS).map(([k, t]) => h("li", {}, h("button", { class: "dropdown-item", type: "button", onClick: () => formulario(k) }, t.nombre))))) : null),
    h("div", { class: "card" }, zonaTabla));
}
