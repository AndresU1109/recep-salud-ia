// Modo día / noche. Sin elección guardada, sigue al sistema; al elegir con el botón, se recuerda en este navegador.
import { h } from "./ui.js";
import { icono } from "./iconos.js";

const CLAVE = "recep_tema";
const sistema = matchMedia("(prefers-color-scheme: dark)");

const guardado = () => { try { const v = localStorage.getItem(CLAVE); return v === "claro" || v === "oscuro" ? v : null; } catch { return null; } };
export const modoActual = () => (document.documentElement.getAttribute("data-bs-theme") === "dark" ? "oscuro" : "claro");

function aplicar(modo, animar) {
  const raiz = document.documentElement;
  if (animar) { raiz.classList.add("cambiando-tema"); setTimeout(() => raiz.classList.remove("cambiando-tema"), 450); }
  raiz.setAttribute("data-bs-theme", modo === "oscuro" ? "dark" : "light");
  document.querySelectorAll(".boton-tema").forEach(actualizarBoton);
}

function actualizarBoton(b) {
  const noche = modoActual() === "oscuro";
  b.setAttribute("aria-pressed", String(noche));
  b.setAttribute("aria-label", noche ? "Cambiar a modo día" : "Cambiar a modo noche");
  b.setAttribute("title", noche ? "Modo día" : "Modo noche");
}

export function alternarTema() {
  const nuevo = modoActual() === "oscuro" ? "claro" : "oscuro";
  try { localStorage.setItem(CLAVE, nuevo); } catch { /* la elección dura mientras la página siga abierta */ }
  aplicar(nuevo, true);
}

sistema.addEventListener("change", () => { if (!guardado()) aplicar(sistema.matches ? "oscuro" : "claro", true); });

/** Interruptor con sol y luna que giran y se desvanecen uno en otro (CSS). */
export function botonTema() {
  const b = h("button", { class: "boton-tema", type: "button", onClick: alternarTema }, h("span", { class: "tema-sol" }, icono("sol", 20)), h("span", { class: "tema-luna" }, icono("luna", 20)));
  actualizarBoton(b);
  return b;
}
