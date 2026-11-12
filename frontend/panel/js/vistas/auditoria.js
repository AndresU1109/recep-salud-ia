// Bitácora de auditoría (solo administradores). Es de solo lectura: el registro no se puede editar ni borrar.
import { api, descargar } from "../api.js";
import { aviso, h, insignia, tabla } from "../ui.js";
import { fechaHora } from "../tiempo.js";

const POR_PAGINA = 25;

export async function auditoria(ctx) {
  const zona = ctx.config.zona;
  let pagina = 1;
  const acciones = await api("/auditoria/acciones");
  const accion = h("input", { class: "form-control", id: "accion", list: "lista-acciones", maxlength: 80, placeholder: "p. ej. cita_", "aria-label": "Acción" });
  const lista = h("datalist", { id: "lista-acciones" }, acciones.map((a) => h("option", { value: a })));
  const actor = h("select", { class: "form-select", id: "actor", "aria-label": "Actor" }, [["", "Cualquier actor"], ["usuario", "Personal"], ["agente", "Asistente"], ["sistema", "Sistema"]].map(([v, t]) => h("option", { value: v }, t)));
  const exito = h("select", { class: "form-select", id: "exito", "aria-label": "Resultado" }, [["", "Todos los resultados"], ["true", "Correctos"], ["false", "Fallidos o denegados"]].map(([v, t]) => h("option", { value: v }, t)));
  const desde = h("input", { class: "form-control", type: "date", id: "desde", "aria-label": "Desde" });
  const hasta = h("input", { class: "form-control", type: "date", id: "hasta", "aria-label": "Hasta" });
  const zonaTabla = h("div", { class: "card-body p-0" });
  const pie = h("div", { class: "d-flex justify-content-between align-items-center p-2" });

  const filtros = () => new URLSearchParams(Object.entries({ accion: accion.value.trim(), actor: actor.value, exito: exito.value, desde: desde.value, hasta: hasta.value }).filter(([, v]) => v));

  async function cargar() {
    try {
      const q = filtros();
      q.set("limite", POR_PAGINA); q.set("pagina", pagina);
      const r = await api(`/auditoria?${q}`);
      zonaTabla.replaceChildren(tabla([
        { titulo: "Fecha", render: (x) => fechaHora(x.ocurrio_en, zona) },
        { titulo: "Quién", render: (x) => x.usuario ?? x.actor },
        { titulo: "Acción", render: (x) => h("code", {}, x.accion) },
        { titulo: "Sobre", render: (x) => x.entidad ? `${x.entidad} ${x.entidad_id ?? ""}` : "" },
        { titulo: "Resultado", render: (x) => x.exito ? insignia("Correcto", "success") : insignia("Fallido / denegado", "danger") },
        { titulo: "Detalle", render: (x) => x.detalle ? h("small", { class: "text-body-secondary" }, JSON.stringify(x.detalle)) : "" },
      ], r.registros, "No hay registros con esos filtros."));
      const paginas = Math.max(1, Math.ceil(r.total / POR_PAGINA));
      pie.replaceChildren(h("span", { class: "small text-body-secondary" }, `${r.total} registro(s) · página ${pagina} de ${paginas}`),
        h("div", { class: "btn-group" },
          h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", disabled: pagina <= 1, onClick: () => { pagina--; cargar(); } }, "Anterior"),
          h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", disabled: pagina >= paginas, onClick: () => { pagina++; cargar(); } }, "Siguiente")));
    } catch (e) { aviso(e.message, "danger"); }
  }

  async function exportar() {
    try { await descargar(`/auditoria/exportar?${filtros()}`, "auditoria.xlsx"); }
    catch (e) { aviso(e.message, "danger"); }
  }

  const aplicar = (ev) => { ev.preventDefault(); pagina = 1; cargar(); };
  await cargar();
  return h("section", {},
    h("h1", { class: "h3 mb-1" }, "Auditoría"),
    h("p", { class: "text-body-secondary" }, "Quién hizo qué y cuándo. El detalle nunca incluye datos personales. El registro es de solo escritura: nadie puede editarlo ni borrarlo, ni siquiera con acceso a la base de datos desde la aplicación."),
    h("form", { class: "row g-2 align-items-end mb-3", onSubmit: aplicar },
      h("div", { class: "col-12 col-md-3" }, h("label", { class: "form-label", for: "accion" }, "Acción contiene"), accion, lista),
      h("div", { class: "col-6 col-md-2" }, h("label", { class: "form-label", for: "actor" }, "Actor"), actor),
      h("div", { class: "col-6 col-md-2" }, h("label", { class: "form-label", for: "exito" }, "Resultado"), exito),
      h("div", { class: "col-6 col-md-2" }, h("label", { class: "form-label", for: "desde" }, "Desde"), desde),
      h("div", { class: "col-6 col-md-2" }, h("label", { class: "form-label", for: "hasta" }, "Hasta"), hasta),
      h("div", { class: "col-12 d-flex gap-2" }, h("button", { class: "btn btn-primary", type: "submit" }, "Filtrar"),
        h("button", { class: "btn btn-outline-success", type: "button", onClick: exportar }, "Descargar Excel"))),
    h("div", { class: "card" }, zonaTabla, pie));
}
