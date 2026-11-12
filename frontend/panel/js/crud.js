// Vista genérica de administración: tabla + búsqueda + crear + editar + activar/desactivar.
import { api } from "./api.js";
import { aviso, estadoInsignia, formModal, h, tabla } from "./ui.js";

/**
 * cfg: { titulo, descripcion, ruta, campoActivo ('activa'|'activo'), columnas, campos(ctx) -> campos, cargarExtras?(),
 *        textoFila?(fila, ctx), nuevoTexto, puedeGestionar }
 * Cada campo: { name, label, type, required, options, soloCrear, ... }
 */
export function vistaCrud(cfg) {
  return async function render(ctx) {
    const extras = cfg.cargarExtras ? await cfg.cargarExtras() : {};
    const contexto = { ...ctx, ...extras };
    let filas = await api(cfg.ruta);
    let texto = "";
    let verInactivos = false;

    const zona = h("div", { class: "card-body p-0" });
    const buscador = h("input", { class: "form-control", type: "search", placeholder: "Buscar…", "aria-label": "Buscar", onInput: (e) => { texto = e.target.value.toLowerCase(); pintar(); } });
    const conmutador = h("input", { class: "form-check-input", type: "checkbox", id: "ver-inactivos", onChange: (e) => { verInactivos = e.target.checked; pintar(); } });

    const columnas = [...cfg.columnas.map((c) => ({ titulo: c.titulo, render: (f) => c.render(f, contexto) })),
      { titulo: "Estado", render: (f) => estadoInsignia(f[cfg.campoActivo]) }];
    if (cfg.puedeGestionar) {
      columnas.push({ titulo: "", render: (f) => h("div", { class: "btn-group btn-group-sm" },
        h("button", { class: "btn btn-outline-primary", type: "button", onClick: () => editar(f) }, "Editar"),
        h("button", { class: "btn btn-outline-secondary", type: "button", onClick: () => conmutarActivo(f) }, f[cfg.campoActivo] ? "Desactivar" : "Activar")) });
    }

    function visibles() {
      return filas.filter((f) => (verInactivos || f[cfg.campoActivo]) &&
        (!texto || (cfg.textoFila ? cfg.textoFila(f, contexto) : JSON.stringify(Object.values(f))).toLowerCase().includes(texto)));
    }
    function pintar() { zona.replaceChildren(tabla(columnas, visibles(), "No hay registros para mostrar.")); }

    async function recargar() { filas = await api(cfg.ruta); pintar(); }

    function abrirFormulario(fila) {
      const esNuevo = !fila;
      const campos = cfg.campos(contexto).filter((c) => esNuevo || !c.soloCrear);
      const base = esNuevo ? {} : (cfg.normalizar ? cfg.normalizar(fila) : fila);
      formModal({
        titulo: esNuevo ? cfg.nuevoTexto : `Editar: ${cfg.nombreFila(fila)}`,
        campos, valores: base,
        onGuardar: async (valores) => {
          let respuesta;
          if (esNuevo) respuesta = await api(cfg.ruta, { metodo: "POST", cuerpo: valores });
          else {
            const cambios = {};
            for (const c of campos) if (JSON.stringify(valores[c.name]) !== JSON.stringify(base[c.name] ?? (c.type === "multiselect" ? [] : null))) cambios[c.name] = valores[c.name];
            if (!Object.keys(cambios).length) return;
            respuesta = await api(`${cfg.ruta}/${fila.id}`, { metodo: "PATCH", cuerpo: cambios });
          }
          aviso(esNuevo ? "Registro creado." : "Cambios guardados.");
          for (const w of respuesta?.advertencias ?? []) aviso(w, "warning");
          await recargar();
        },
      });
    }
    const editar = (f) => abrirFormulario(f);

    async function conmutarActivo(f) {
      const activar = !f[cfg.campoActivo];
      const nombre = cfg.nombreFila ? cfg.nombreFila(f) : "este registro";
      if (!activar && !confirm(`¿Desactivar ${nombre}? Dejará de ofrecerse, pero se conserva su historial.`)) return;
      try { await api(`${cfg.ruta}/${f.id}`, { metodo: "PATCH", cuerpo: { [cfg.campoActivo]: activar } }); aviso(activar ? "Activado." : "Desactivado."); await recargar(); }
      catch (e) { aviso(e.message, "danger"); }
    }

    pintar();
    return h("section", {},
      h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
        h("div", {}, h("h1", { class: "h3 mb-1" }, cfg.titulo), h("p", { class: "text-body-secondary mb-0" }, cfg.descripcion)),
        cfg.puedeGestionar ? h("button", { class: "btn btn-primary", type: "button", onClick: () => abrirFormulario(null) }, `+ ${cfg.nuevoTexto}`) : null),
      h("div", { class: "card" },
        h("div", { class: "card-header d-flex flex-wrap gap-3 align-items-center" },
          h("div", { class: "flex-grow-1" }, buscador),
          h("div", { class: "form-check mb-0" }, conmutador, h("label", { class: "form-check-label", for: "ver-inactivos" }, "Mostrar inactivos"))),
        zona));
  };
}
