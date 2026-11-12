// Se ejecuta ANTES de pintar (script clásico en <head>): aplica el modo guardado para que no haya un destello claro al abrir de noche.
(function () {
  var modo = null;
  try { modo = localStorage.getItem("recep_tema"); } catch (e) { /* sin almacenamiento: se usa el del sistema */ }
  if (modo !== "claro" && modo !== "oscuro") modo = matchMedia("(prefers-color-scheme: dark)").matches ? "oscuro" : "claro";
  document.documentElement.setAttribute("data-bs-theme", modo === "oscuro" ? "dark" : "light");
})();
