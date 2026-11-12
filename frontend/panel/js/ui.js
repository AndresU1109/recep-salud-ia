// Utilidades de interfaz. Todo se construye con nodos DOM y textContent (nunca HTML armado con datos).
import { icono } from "./iconos.js";

export function h(etiqueta, props = {}, ...hijos) {
  const el = document.createElement(etiqueta);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of hijos.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return el;
}

const AVISOS = {
  success: ["ok", "Listo"], danger: ["peligro", "Algo salió mal"], warning: ["alerta", "Ojo con esto"], info: ["info", "Para tu información"], secondary: ["info", "Aviso"],
};

/** Aviso flotante: tarjeta con icono, título, barra de tiempo (se detiene al pasar el mouse) y botón para cerrar. */
export function aviso(texto, tipo = "success") {
  let caja = document.getElementById("avisos");
  if (!caja) { caja = h("div", { id: "avisos", class: "avisos", "aria-live": "polite", "aria-atomic": "false" }); document.body.append(caja); }
  const [nombreIcono, titulo] = AVISOS[tipo] ?? AVISOS.info;
  const ms = tipo === "danger" ? 8000 : tipo === "warning" ? 6000 : 4000;
  const barra = h("span", { class: "aviso-barra" });
  barra.style.setProperty("--duracion", `${ms}ms`);
  const cerrar = () => { if (!el.isConnected) return; el.classList.add("saliendo"); setTimeout(() => el.remove(), 260); };
  const el = h("div", { class: `aviso aviso-${tipo}`, role: tipo === "danger" || tipo === "warning" ? "alert" : "status" },
    h("span", { class: "aviso-icono" }, icono(nombreIcono, 20)),
    h("div", { class: "aviso-texto" }, h("strong", {}, titulo), h("span", {}, texto)),
    h("button", { class: "aviso-cerrar", type: "button", "aria-label": "Cerrar aviso", onClick: cerrar }, icono("x", 16)),
    barra);
  let reloj = setTimeout(cerrar, ms);
  el.addEventListener("mouseenter", () => { clearTimeout(reloj); el.classList.add("en-pausa"); });
  el.addEventListener("mouseleave", () => { el.classList.remove("en-pausa"); reloj = setTimeout(cerrar, 1800); });
  while (caja.children.length >= 4) caja.firstElementChild.remove(); // nunca más de 4 a la vez
  caja.append(el);
}

export function insignia(texto, tipo = "secondary") {
  return h("span", { class: `badge text-bg-${tipo}` }, texto);
}

export function estadoInsignia(activo) {
  return activo ? insignia("Activo", "success") : insignia("Inactivo", "secondary");
}

/** Botón con icono: boton("Guardar", "guardar", "primary", onClick). */
export function boton(texto, nombreIcono, variante = "primary", onClick = null, extra = {}) {
  return h("button", { class: `btn btn-${variante}`, type: "button", ...extra, onClick }, nombreIcono ? icono(nombreIcono, 18) : null, texto);
}

/** Estado vacío amable: dibuja un icono y explica qué hacer, en vez de un «No hay registros» seco. */
export function vacio(texto, nombreIcono = "bandeja", pista = "") {
  return h("div", { class: "vacio" }, h("span", { class: "vacio-icono" }, icono(nombreIcono, 34)), h("p", { class: "vacio-texto" }, texto), pista ? h("p", { class: "vacio-pista" }, pista) : null);
}

export function tabla(columnas, filas, textoVacio = "No hay registros.") {
  if (!filas.length) return vacio(textoVacio, "bandeja", "Cuando haya información, aparecerá aquí.");
  return h("div", { class: "table-responsive" },
    h("table", { class: "table table-hover align-middle mb-0" },
      h("thead", {}, h("tr", {}, columnas.map((c) => h("th", { scope: "col" }, c.titulo)))),
      h("tbody", {}, filas.map((f) => h("tr", {}, columnas.map((c) => h("td", {}, c.render(f))))))));
}

// ---------- formularios ----------
function nodoCampo(c, valor, idBase) {
  const id = `${idBase}-${c.name}`;
  const etiqueta = h("label", { class: "form-label", for: id }, c.label, c.required ? " *" : "");
  let control;
  if (c.type === "checkbox") {
    control = h("input", { class: "form-check-input", type: "checkbox", id, checked: valor ?? c.defecto ?? false });
    return { nodo: h("div", { class: "form-check mb-3" }, control, h("label", { class: "form-check-label", for: id }, c.label)), leer: () => control.checked };
  }
  if (c.type === "file") {
    control = h("input", { class: "form-control", type: "file", id, accept: c.accept, required: c.required });
    return { nodo: h("div", { class: "mb-3" }, etiqueta, control, c.ayuda ? h("div", { class: "form-text" }, c.ayuda) : null), leer: () => control.files[0] ?? null, control };
  }
  if (c.type === "multiselect") {
    const marcados = new Set(valor ?? []);
    const cajas = c.options.map((o, i) => {
      const cb = h("input", { class: "form-check-input", type: "checkbox", id: `${id}-${i}`, value: o.value, checked: marcados.has(o.value) });
      return { cb, nodo: h("div", { class: "form-check" }, cb, h("label", { class: "form-check-label", for: `${id}-${i}` }, o.label)) };
    });
    const vacio = !c.options.length ? h("p", { class: "text-body-secondary small" }, "No hay opciones disponibles todavía.") : null;
    return { nodo: h("fieldset", { class: "mb-3" }, h("legend", { class: "form-label fs-6" }, c.label, c.required ? " *" : ""), vacio, cajas.map((x) => x.nodo)),
             leer: () => cajas.filter((x) => x.cb.checked).map((x) => Number(x.cb.value)) };
  }
  if (c.type === "select") {
    control = h("select", { class: "form-select", id, required: c.required, disabled: c.disabled });
    const llenar = (opciones, elegido) => {
      control.replaceChildren(h("option", { value: "" }, c.required ? "Selecciona…" : "— Ninguno —"), ...opciones.map((o) => h("option", { value: o.value }, o.label)));
      control.value = elegido == null ? "" : String(elegido);
    };
    llenar(c.options, valor);
    const leerSelect = () => (control.value === "" ? null : c.numero ? Number(control.value) : control.value);
    return { nodo: h("div", { class: "mb-3" }, etiqueta, control, c.ayuda ? h("div", { class: "form-text" }, c.ayuda) : null), leer: leerSelect, control, llenar };
  } else if (c.type === "textarea") {
    control = h("textarea", { class: "form-control", id, rows: 3, required: c.required, maxlength: c.max }, valor ?? "");
  } else {
    control = h("input", { class: "form-control", id, type: c.type || "text", required: c.required, min: c.min, max: c.max,
                           maxlength: c.type === "number" ? null : c.max, autocomplete: c.autocomplete || "off", value: valor ?? "" });
  }
  const leer = () => {
    const v = control.value.trim();
    if (v === "") return null;
    return c.type === "number" || (c.type === "select" && c.numero) ? Number(v) : v;
  };
  return { nodo: h("div", { class: "mb-3" }, etiqueta, control, c.ayuda ? h("div", { class: "form-text" }, c.ayuda) : null), leer, control };
}

/** Ventana informativa (sin formulario). Devuelve { cuerpo, cerrar } para ir llenándola. */
export function ventana(titulo, { grande = false } = {}) {
  const cuerpo = h("div", { class: "modal-body" });
  const raiz = h("div", { class: "modal fade", tabindex: "-1", "aria-hidden": "true" },
    h("div", { class: `modal-dialog modal-dialog-scrollable ${grande ? "modal-lg" : ""}` }, h("div", { class: "modal-content" },
      h("div", { class: "modal-header" }, h("h2", { class: "modal-title fs-5" }, titulo), h("button", { type: "button", class: "btn-close", "data-bs-dismiss": "modal", "aria-label": "Cerrar" })),
      cuerpo)));
  document.body.append(raiz);
  const modal = new bootstrap.Modal(raiz);
  raiz.addEventListener("hidden.bs.modal", () => { modal.dispose(); raiz.remove(); });
  modal.show();
  return { cuerpo, cerrar: () => modal.hide() };
}

/** Abre un formulario modal. onGuardar(valores) puede lanzar ApiError: se muestra dentro y el modal sigue abierto. */
export function formModal({ titulo, campos, valores = {}, guardarTexto = "Guardar", onGuardar }) {
  const idBase = `f${Math.random().toString(36).slice(2, 8)}`;
  const nodos = campos.map((c) => ({ c, ...nodoCampo(c, valores[c.name], idBase) }));
  // Selects dependientes: campo.depende = { de: "otro_campo", opciones: (valorDelOtro) => [{value,label}] }
  for (const n of nodos.filter((x) => x.c.depende)) {
    const origen = nodos.find((x) => x.c.name === n.c.depende.de);
    const refrescar = (conservar) => n.llenar(n.c.depende.opciones(origen.leer()), conservar);
    refrescar(valores[n.c.name]);
    origen.control.addEventListener("change", () => refrescar(null));
  }
  const alerta = h("div", { class: "alert alert-danger d-none", role: "alert" });
  const mostrar = (texto) => { alerta.textContent = texto; alerta.classList.remove("d-none"); alerta.scrollIntoView({ block: "nearest" }); };
  const boton = h("button", { type: "submit", class: "btn btn-primary" }, guardarTexto);
  const form = h("form", {},
    h("div", { class: "modal-header" }, h("h2", { class: "modal-title fs-5" }, titulo), h("button", { type: "button", class: "btn-close", "data-bs-dismiss": "modal", "aria-label": "Cerrar" })),
    h("div", { class: "modal-body" }, alerta, nodos.map((n) => n.nodo)),
    h("div", { class: "modal-footer" }, h("button", { type: "button", class: "btn btn-outline-secondary", "data-bs-dismiss": "modal" }, "Cancelar"), boton));
  const raiz = h("div", { class: "modal fade", tabindex: "-1", "aria-hidden": "true" }, h("div", { class: "modal-dialog modal-dialog-scrollable" }, h("div", { class: "modal-content" }, form)));
  document.body.append(raiz);
  const modal = new bootstrap.Modal(raiz);
  raiz.addEventListener("hidden.bs.modal", () => { modal.dispose(); raiz.remove(); });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    alerta.classList.add("d-none");
    const out = {};
    for (const n of nodos) out[n.c.name] = n.leer();
    const faltante = nodos.find((n) => n.c.type === "multiselect" && n.c.required && !out[n.c.name].length);
    if (faltante) { mostrar(`Selecciona al menos una opción en «${faltante.c.label}».`); return; }
    const sinMarcar = nodos.find((n) => n.c.type === "checkbox" && n.c.obligatorio && !out[n.c.name]);
    if (sinMarcar) { mostrar(sinMarcar.c.obligatorio); return; }
    boton.disabled = true;
    try { await onGuardar(out); modal.hide(); }
    catch (err) { mostrar(err.message || "No se pudo guardar."); }
    finally { boton.disabled = false; }
  });
  modal.show();
  raiz.addEventListener("shown.bs.modal", () => form.querySelector("input,select,textarea")?.focus());
}
