// Pone un icono a los botones según lo que dicen, sin tocar cada pantalla: así toda la interfaz queda coherente.
import { icono } from "./iconos.js";

const REGLAS = [
  [/^(nuev[oa]s?|agregar|crear|añadir)\b/i, "mas"], [/^(guardar|registrar)/i, "guardar"], [/^(editar|modificar)/i, "lapiz"], [/^descargar/i, "descargar"],
  [/^(eliminar|borrar|desactivar)/i, "basura"], [/^(cancelar|quitar|cerrar|descartar)/i, "x"], [/^enviar/i, "enviar"], [/^(filtrar|buscar|probar búsqueda)/i, "buscar"],
  [/^(ver|consultar)\b/i, "ver"], [/^(reintentar|actualizar|recargar|reindexar|indexar)/i, "refrescar"], [/^(procesar|probar|ejecutar)/i, "jugar"], [/^colgar/i, "colgar"],
  [/^(iniciar llamada|hablar)/i, "microfono"], [/^interrumpir/i, "parar"], [/^anterior/i, "atras"], [/^siguiente/i, "flecha"], [/^(confirmar|activar|aprobar|asignar|verificar)/i, "ok"],
  [/^reprogramar/i, "agenda"], [/^salir/i, "salir"], [/^(entrar|iniciar sesión)/i, "flecha"], [/^cambiar contraseña/i, "candado"],
];

export function decorar(nodo) {
  for (const b of nodo.querySelectorAll?.("button.btn, a.btn") ?? []) {
    if (b.dataset.ic || b.classList.contains("btn-close") || b.querySelector("svg")) continue;
    b.dataset.ic = "1";
    const texto = b.textContent.trim();
    const regla = REGLAS.find(([r]) => r.test(texto));
    if (regla && texto) b.prepend(icono(regla[1], 18));
  }
}

/** Observa la página: cualquier botón que aparezca (vistas, ventanas, formularios) se decora. */
export function iniciarDecorado() {
  decorar(document.body);
  new MutationObserver((cambios) => {
    for (const c of cambios) for (const n of c.addedNodes) if (n.nodeType === 1) { if (n.matches?.("button.btn, a.btn")) decorar(n.parentNode); else decorar(n); }
  }).observe(document.body, { childList: true, subtree: true });
}
