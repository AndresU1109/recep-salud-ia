// Inicio: bienvenida, accesos rápidos, cifras y una lista de «primeros pasos» que se va marcando sola.
import { api } from "../api.js";
import { fechaLarga, saludo } from "../ayuda.js";
import { icono } from "../iconos.js";
import { h } from "../ui.js";

const CIFRAS = {
  citas_hoy: ["Citas de hoy", "agenda", "pino"], citas_proximas: ["Citas próximas", "citas", "cielo"], pacientes: ["Pacientes activos", "pacientes", "musgo"],
  profesionales_activos: ["Profesionales", "profesionales", "coral"], especialidades_activas: ["Especialidades", "especialidades", "sol"],
  sedes_activas: ["Sedes", "sedes", "cielo"], documentos_activos: ["Documentos activos", "documentos", "coral"], usuarios_activos: ["Usuarios", "usuarios", "pino"],
};

const ACCESOS = [
  ["agente:atender", "voz", "Atender por voz", "El paciente habla y el asistente agenda su cita en voz alta."],
  ["citas:ver", "citas", "Agendar o revisar citas", "Busca un paciente y reserva un horario libre."],
  ["agenda:ver", "agenda", "Ver la agenda de hoy", "Quién atiende y qué cupos quedan."],
  ["pacientes:ver", "pacientes", "Registrar un paciente", "Con su autorización de datos."],
  ["agente:probar", "asistente", "Hablar con el asistente", "Pruébalo escribiendo o con tu voz."],
  ["documentos:gestionar", "documentos", "Enseñarle algo nuevo", "Sube un documento para que lo use al responder."],
  ["reportes:ver", "reportes", "Ver cómo va todo", "Reportes en Excel o PDF."],
];

const PASOS = [
  ["sedes_activas", "sedes", "Crea tu primera sede", "Es el lugar físico donde se atiende."],
  ["especialidades_activas", "especialidades", "Agrega las especialidades", "Lo que ofrece la institución."],
  ["profesionales_activos", "profesionales", "Registra a los profesionales", "Quienes atienden, con sus especialidades."],
  ["documentos_activos", "documentos", "Sube un documento al asistente", "Así podrá responder con información oficial."],
];

const CONSEJOS = [
  "Si un reporte dice «N/D», significa que todavía no hay datos suficientes para calcularlo; no es un error.",
  "El asistente nunca da orientación médica: ante una posible emergencia indica llamar al 123.",
  "Un aviso de lista de espera no reserva el cupo: quien responda primero lo ocupa.",
  "Con el botón del sol y la luna cambias entre modo día y modo noche.",
  "Cada acción importante queda en la auditoría, y nadie puede editarla ni borrarla.",
  "En cada pantalla, el botón de ayuda (arriba) te explica para qué sirve y cómo se usa.",
];

const sinMovimiento = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function contar(el, hasta) {
  if (sinMovimiento() || hasta === 0) { el.textContent = String(hasta); return; }
  const t0 = performance.now(), dur = 700;
  const paso = (t) => {
    const k = Math.min(1, (t - t0) / dur);
    el.textContent = String(Math.round(hasta * (1 - Math.pow(1 - k, 3))));
    if (k < 1) requestAnimationFrame(paso);
  };
  requestAnimationFrame(paso);
}

export async function inicio(ctx) {
  const { institucion, cifras } = await api("/panel/resumen");
  const nombre = ctx.usuario.nombre.split(" ")[0];
  const permisos = ctx.usuario.permisos;

  const tarjetas = Object.entries(CIFRAS).filter(([k]) => k in cifras).map(([k, [titulo, ic, color]]) => {
    const numero = h("span", { class: "dato-numero" }, "0");
    contar(numero, cifras[k]);
    return h("div", { class: "col-6 col-lg-3" }, h("div", { class: `tarjeta-dato color-${color}` }, h("span", { class: "dato-icono" }, icono(ic, 22)), numero, h("span", { class: "dato-titulo" }, titulo)));
  });

  const accesos = ACCESOS.filter(([p]) => permisos.includes(p)).map(([, ruta, titulo, texto]) =>
    h("a", { class: "acceso-rapido", href: `#/${ruta}` }, h("span", { class: "acceso-rapido-icono" }, icono(ruta, 24)), h("span", { class: "acceso-rapido-texto" }, h("strong", {}, titulo), h("small", {}, texto)), h("span", { class: "acceso-rapido-flecha" }, icono("flecha", 18))));

  const pasos = PASOS.filter(([k]) => k in cifras).map(([k, ruta, titulo, texto]) => {
    const hecho = cifras[k] > 0;
    return h("a", { class: `paso ${hecho ? "paso-hecho" : ""}`, href: `#/${ruta}` },
      h("span", { class: "paso-marca" }, hecho ? icono("ok", 16) : null), h("span", { class: "paso-texto" }, h("strong", {}, titulo), h("small", {}, hecho ? "Listo" : texto)));
  });
  const hechos = pasos.filter((p) => p.classList.contains("paso-hecho")).length;
  const consejo = CONSEJOS[new Date().getDate() % CONSEJOS.length];

  return h("section", { class: "inicio" },
    h("div", { class: "bienvenida" },
      h("div", { class: "bienvenida-texto" },
        h("span", { class: "bienvenida-fecha" }, fechaLarga(new Date(), ctx.config.zona)),
        h("h1", { class: "bienvenida-titulo" }, `${saludo()}, `, h("em", {}, nombre), "."),
        h("p", { class: "bienvenida-sub" }, `Qué bueno tenerte en ${institucion}. Aquí tienes lo esencial para empezar el día.`)),
      h("div", { class: "bienvenida-dibujo", "aria-hidden": "true" }, h("span", { class: "burbuja burbuja-1" }, icono("citas", 26)), h("span", { class: "burbuja burbuja-2" }, icono("corazon", 22)), h("span", { class: "burbuja burbuja-3" }, icono("asistente", 24)))),
    accesos.length ? h("div", {}, h("h2", { class: "seccion" }, icono("rayo", 20), "¿Qué quieres hacer?"), h("div", { class: "accesos" }, accesos)) : null,
    tarjetas.length ? h("div", {}, h("h2", { class: "seccion" }, icono("reportes", 20), "Así va todo"), h("div", { class: "row g-3" }, tarjetas)) : null,
    h("div", { class: "row g-3 mt-1" },
      pasos.length ? h("div", { class: "col-12 col-lg-7" }, h("div", { class: "card h-100" }, h("div", { class: "card-body" },
        h("h2", { class: "seccion m-0" }, icono("brujula", 20), "Primeros pasos", h("span", { class: "seccion-cuenta" }, `${hechos} de ${pasos.length}`)),
        h("p", { class: "text-body-secondary small" }, hechos === pasos.length ? "¡Todo listo! Ya puedes recibir pacientes." : "Sigue estos pasos en orden y el sistema quedará listo para agendar."),
        h("div", { class: "pasos" }, pasos)))) : null,
      h("div", { class: pasos.length ? "col-12 col-lg-5" : "col-12" }, h("div", { class: "consejo" }, h("span", { class: "consejo-icono" }, icono("foco", 22)), h("div", {}, h("strong", {}, "¿Sabías que?"), h("p", {}, consejo))))));
}
