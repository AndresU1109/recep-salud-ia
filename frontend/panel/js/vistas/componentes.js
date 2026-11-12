// Componentes reutilizables: selector de paciente y selector de cupos.
import { api } from "../api.js";
import { hoyEn, hora } from "../tiempo.js";
import { aviso, h } from "../ui.js";

const sumarDias = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

/** Busca pacientes por nombre o documento y deja elegir uno. Requiere permiso pacientes:ver. */
export function selectorPaciente() {
  let elegido = null;
  const q = h("input", { class: "form-control", type: "search", placeholder: "Nombre o documento del paciente", "aria-label": "Buscar paciente", maxlength: 100 });
  const resultados = h("div", { class: "list-group mt-2" });
  const marcado = h("div", { class: "form-text" }, "Ningún paciente seleccionado.");
  async function buscar(e) {
    e?.preventDefault();
    if (q.value.trim().length < 2) { aviso("Escribe al menos 2 caracteres.", "warning"); return; }
    try {
      const lista = await api(`/pacientes?q=${encodeURIComponent(q.value.trim())}&limite=8`);
      resultados.replaceChildren(...(lista.length ? lista.map((p) => h("button", { type: "button", class: "list-group-item list-group-item-action", onClick: () => {
        elegido = p; marcado.textContent = `Seleccionado: ${p.nombres} ${p.apellidos} (${p.tipo_documento} ${p.documento})`; resultados.replaceChildren();
      } }, `${p.nombres} ${p.apellidos} · ${p.tipo_documento} ${p.documento}`)) : [h("div", { class: "text-body-secondary small" }, "No se encontraron pacientes.")]));
    } catch (err) { aviso(err.message, "danger"); }
  }
  q.addEventListener("keydown", (e) => { if (e.key === "Enter") buscar(e); });
  const nodo = h("div", { class: "mb-3" }, h("label", { class: "form-label" }, "Paciente *"),
    h("div", { class: "input-group" }, q, h("button", { class: "btn btn-outline-primary", type: "button", onClick: buscar }, "Buscar")), resultados, marcado);
  return { nodo, valor: () => elegido };
}

/** Lista de cupos libres de un rango de fechas para elegir uno. filtros: {especialidad_id, servicio_id?, profesional_id?, sede_id?} */
export function selectorCupos(ctx, filtros) {
  const zona = ctx.config.zona;
  const hoy = hoyEn(zona);
  let elegido = null;
  const desde = h("input", { class: "form-control", type: "date", value: sumarDias(hoy, 1), "aria-label": "Desde" });
  const hasta = h("input", { class: "form-control", type: "date", value: sumarDias(hoy, 8), "aria-label": "Hasta" });
  const zonaSlots = h("div", { class: "mt-3" });
  const marcado = h("div", { class: "form-text" }, "Ningún cupo seleccionado.");

  async function buscar() {
    elegido = null; marcado.textContent = "Ningún cupo seleccionado.";
    const q = new URLSearchParams({ desde: desde.value, hasta: hasta.value });
    for (const [k, v] of Object.entries(filtros)) if (v) q.set(k, v);
    zonaSlots.replaceChildren(h("div", { class: "spinner-border spinner-border-sm", role: "status" }));
    try {
      const slots = await api(`/agenda/disponibilidad?${q}`);
      if (!slots.length) { zonaSlots.replaceChildren(h("div", { class: "alert alert-secondary mb-0" }, "No hay cupos libres en ese rango. Prueba con otras fechas o agrega al paciente a la lista de espera.")); return; }
      const dias = new Map();
      for (const s of slots) {
        const clave = new Date(s.inicio).toLocaleDateString("es-CO", { timeZone: zona, weekday: "long", day: "numeric", month: "long" });
        (dias.get(clave) ?? dias.set(clave, []).get(clave)).push(s);
      }
      const botones = [];
      zonaSlots.replaceChildren(...[...dias].map(([dia, lista]) => h("div", { class: "mb-3" }, h("div", { class: "fw-semibold text-capitalize mb-1" }, dia),
        h("div", { class: "d-flex flex-wrap gap-2" }, lista.map((s) => {
          const b = h("button", { type: "button", class: "btn btn-sm btn-outline-success", title: `${s.profesional} · ${s.sede}`, onClick: () => {
            botones.forEach((x) => x.classList.remove("active")); b.classList.add("active"); elegido = s;
            marcado.textContent = `Elegido: ${dia}, ${hora(s.inicio, zona)} con ${s.profesional} (${s.sede}${s.consultorio ? ` · ${s.consultorio}` : ""})`;
          } }, `${hora(s.inicio, zona)} · ${s.profesional.split(" ").slice(0, 2).join(" ")}`);
          botones.push(b); return b;
        })))));
    } catch (err) { zonaSlots.replaceChildren(h("div", { class: "alert alert-danger mb-0" }, err.message)); }
  }
  const nodo = h("div", { class: "mb-3" },
    h("div", { class: "row g-2 align-items-end" }, h("div", { class: "col" }, h("label", { class: "form-label" }, "Desde"), desde), h("div", { class: "col" }, h("label", { class: "form-label" }, "Hasta"), hasta),
      h("div", { class: "col-auto" }, h("button", { class: "btn btn-outline-primary", type: "button", onClick: buscar }, "Ver cupos"))),
    zonaSlots, marcado);
  return { nodo, valor: () => elegido, buscar };
}
