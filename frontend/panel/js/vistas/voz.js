// Atención por voz: una conversación SOLO hablada. El paciente habla; el asistente responde en voz alta (pide datos, ofrece horarios,
// nombra especialistas y sedes). En pantalla solo hay subtítulos de apoyo y sugerencias de qué decir: no se escribe nada.
import { ClienteVoz } from "../voz/cliente.js";
import { EscuchaDeActivacion } from "../voz/espera.js";
import { api } from "../api.js";
import { aviso, h } from "../ui.js";
import { icono } from "../iconos.js";

const ESTADOS = {
  inactivo: ["Listo para atender", "Pulse el botón para empezar la conversación."],
  conectando: ["Conectando…", "Un momento, por favor."],
  escuchando: ["Lo escucho", "Hable cuando quiera; no necesita pulsar nada."],
  tu: ["Lo estoy escuchando…", "Siga hablando, cuando termine haré una pausa."],
  procesando: ["Entendiendo lo que dijo…", "Esto toma unos segundos."],
  asistente: ["Le estoy respondiendo", "Puede interrumpirme hablando."],
  cerrada: ["Conversación terminada", "Gracias por comunicarse."],
  espera: ["Esperando su llamado", "Diga «Hola asistente» para empezar; no necesita pulsar nada."],
};

const SUGERENCIAS = {
  inicio: ["Quiero una cita", "¿Qué instituciones atienden?", "Quiero cancelar mi cita"],
  encuesta: ["Cinco", "Excelente", "Más o menos", "No quiero responder"],
  institucion: ["Diga el nombre de la institución", "O la especialidad que necesita: «una cita de…»"],
  identidad: ["Mi cédula es… y nací el… de… de…"],
  horarios: ["La primera", "A las nueve de la mañana", "Prefiero otro día"],
  confirmar: ["Sí, confírmela", "No, gracias"],
  espera: ["Hola asistente"],
  listo: ["No, eso es todo", "Quiero otra cita"],
  otra: ["No, eso es todo", "Quiero hablar con una persona"],
};

function fase(r) {
  if (r.intencion === "encuesta") return "encuesta";
  if (r.requiere_confirmacion) return "confirmar";
  if (r.herramientas.includes("agendar_cita") || r.herramientas.includes("cancelar_cita") || r.herramientas.includes("reprogramar_cita")) return "listo";
  if (/documento|nacimiento/i.test(r.texto) && !r.herramientas.includes("buscar_paciente")) return "identidad";
  if (r.herramientas.includes("consultar_disponibilidad")) return "horarios";
  if (r.intencion === "seleccion_institucion" || (r.intencion === "saludo" && /instituci/i.test(r.texto))) return "institucion";
  return r.intencion === "saludo" ? "inicio" : "otra";
}

const recordar = (k, v) => { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); return v; } catch { return null; } };

export async function voz(ctx) {
  const puedeAtender = ctx.usuario.permisos.includes("agente:atender");
  let llamada = null;
  let espera = null; // la escucha de la frase de activación
  let esperando = false; // el usuario activó el modo de espera: tras cada llamada se vuelve a esperar
  let frasesActivacion = ["hola asistente"];
  let animacion = 0;
  let estado = "inactivo";

  const etiqueta = h("div", { class: "voz-estado-titulo", role: "status", "aria-live": "polite" });
  const detalle = h("div", { class: "voz-estado-sub" });
  const orbe = h("div", { class: "orbe orbe-inactivo" }, h("span", { class: "orbe-anillo" }), h("span", { class: "orbe-anillo orbe-anillo-2" }), h("span", { class: "orbe-centro" }, icono("microfono", 44)));
  const asistenteDice = h("p", { class: "sub-asistente", "aria-live": "polite" }, "Hola. Cuando esté listo, pulse «Iniciar conversación» y hable con normalidad.");
  const usuarioDijo = h("p", { class: "sub-usuario" });
  const chips = h("div", { class: "voz-chips" });
  const historial = h("div", { class: "voz-historial", role: "log" });
  const botonPrincipal = h("button", { class: "btn btn-primary btn-lg voz-boton-principal", type: "button" }, "Iniciar conversación");
  const botonSilencio = h("button", { class: "btn btn-outline-secondary", type: "button", disabled: true, "aria-pressed": "false" }, "Silenciar micrófono");
  const botonEspera = h("button", { class: "btn btn-outline-secondary", type: "button", "aria-pressed": "false" }, "Activar escucha por voz");
  const botonPantalla = h("button", { class: "btn btn-outline-secondary", type: "button" }, "Pantalla completa");
  const modoReal = h("input", { class: "form-check-input", type: "checkbox", id: "voz-real", checked: recordar("recep_voz_real") === "1" });
  const pantalla = h("section", { class: "voz-pantalla" });
  const nivel = h("div", { class: "voz-nivel", role: "img", "aria-label": "Nivel del micrófono" }, h("span", {}));

  // La institución y la especialidad no se eligen en pantalla: el asistente las pregunta y el paciente las dice en la conversación.
  const quienHabla = h("div", { class: "voz-quien", "aria-live": "polite" });

  function pintarEstado(clave) {
    estado = clave;
    const [t, s] = ESTADOS[clave];
    etiqueta.textContent = t; detalle.textContent = s;
    orbe.className = `orbe orbe-${clave}`;
    const libre = clave === "inactivo" || clave === "cerrada" || clave === "espera";
    botonPrincipal.replaceChildren(icono(libre ? "microfono" : "colgar", 20), libre ? "Iniciar conversación" : "Terminar conversación");
    botonPrincipal.classList.toggle("btn-danger", !libre);
    botonPrincipal.classList.toggle("btn-primary", libre);
    botonSilencio.disabled = libre || clave === "conectando";
    if (clave === "espera") detalle.textContent = `Diga «${frasesActivacion[0]}» para empezar; no necesita pulsar nada.`;
  }

  function sugerir(clave) {
    chips.replaceChildren(h("span", { class: "voz-chips-titulo" }, "Puede decir:"), ...SUGERENCIAS[clave].map((t) => h("span", { class: "voz-chip" }, `«${t}»`)));
  }

  function burbuja(rol, texto) {
    historial.append(h("div", { class: `chat-fila ${rol === "usuario" ? "chat-usuario" : "chat-asistente"}` }, h("div", { class: "chat-burbuja" }, texto)));
    historial.scrollTop = historial.scrollHeight;
  }

  function bucleOrbe() {
    // El orbe «respira» con la voz: la del asistente mientras habla, la del paciente mientras habla él.
    const v = estado === "asistente" ? llamada?.nivelSalida() ?? 0 : estado === "tu" ? nivelMic : 0;
    orbe.style.setProperty("--nivel", v.toFixed(3));
    nivel.firstChild.style.setProperty("width", `${Math.min(100, Math.round(nivelMic * 100))}%`); // se ve si el navegador está oyendo el micrófono
    animacion = requestAnimationFrame(bucleOrbe);
  }
  let nivelMic = 0;

  function alEvento(e) {
    if (e.tipo === "transcripcion" && e.texto) {
      usuarioDijo.textContent = e.baja_confianza ? `Entendí: «${e.texto}» (no estoy segura)` : `Usted dijo: «${e.texto}»`;
      burbuja("usuario", e.texto);
    } else if ((e.tipo === "listo" || e.tipo === "institucion") && e.institucion) {
      quienHabla.textContent = `Hablando con: ${e.institucion.nombre}`;
    } else if (e.tipo === "respuesta") {
      asistenteDice.textContent = e.texto;
      burbuja("asistente", e.texto);
      sugerir(e.intencion === "no_entendido" ? "otra" : fase(e));
      if (e.emergencia) aviso("El asistente detectó una posible emergencia y lo indicó en voz alta.", "warning");
      if (e.transferida) aviso("La conversación pasó a una persona del equipo.", "info");
      if (e.terminar) chips.replaceChildren();
    } else if (e.tipo === "aviso_local") {
      aviso(e.mensaje, "warning");
    } else if (e.tipo === "error" && !["ocupado"].includes(e.codigo)) {
      aviso(e.mensaje, e.codigo === "tts_no_disponible" ? "warning" : "danger");
    } else if (e.tipo === "cerrada") terminar(false);
  }

  async function empezarEspera() {
    espera = new EscuchaDeActivacion({ onNivel: (v) => { nivelMic = v; }, onAviso: (m) => { aviso(m, "warning"); apagarEspera(); },
      onActivar: () => { espera = null; aviso("Activada por voz: empieza la conversación."); empezar(); } });
    try {
      await espera.iniciar();
      pintarEstado("espera");
      cancelAnimationFrame(animacion); bucleOrbe();
      sugerir("espera");
    } catch (e) {
      aviso(e.name === "NotAllowedError" ? "No se pudo usar el micrófono: el navegador no dio permiso." : e.message, "danger");
      espera = null; apagarEspera();
    }
  }

  function apagarEspera() {
    esperando = false;
    espera?.detener(); espera = null;
    botonEspera.setAttribute("aria-pressed", "false"); botonEspera.replaceChildren(icono("microfono", 18), "Activar escucha por voz");
    if (estado === "espera") { cancelAnimationFrame(animacion); pintarEstado("inactivo"); sugerir("inicio"); }
  }

  async function empezar() {
    espera?.detener(); espera = null; // el micrófono pasa a la llamada
    pintarEstado("conectando");
    historial.replaceChildren(); usuarioDijo.textContent = ""; quienHabla.textContent = "";
    asistenteDice.textContent = "";
    recordar("recep_voz_real", modoReal.checked ? "1" : "0");
    llamada = new ClienteVoz({ onEstado: pintarEstado, onNivel: (v) => { nivelMic = v; }, onEvento: alEvento }, { real: puedeAtender && modoReal.checked });
    try {
      await llamada.iniciar();
      botonSilencio.disabled = false;
      sugerir("inicio");
      cancelAnimationFrame(animacion); bucleOrbe();
    } catch (e) {
      aviso(e.name === "NotAllowedError" ? "No se pudo usar el micrófono: el navegador no dio permiso." : e.message, "danger");
      terminar(true);
    }
  }

  function terminar(forzado) {
    cancelAnimationFrame(animacion);
    if (llamada) { llamada.colgar(); llamada = null; }
    if (document.fullscreenElement && forzado === true) document.exitFullscreen?.();
    pintarEstado("cerrada");
    botonSilencio.setAttribute("aria-pressed", "false"); botonSilencio.replaceChildren(icono("microfono", 18), "Silenciar micrófono");
    chips.replaceChildren();
    if (!asistenteDice.textContent) asistenteDice.textContent = "La conversación no pudo iniciarse.";
    if (esperando) setTimeout(() => { if (esperando && !llamada && !espera) empezarEspera(); }, 1200); // modo de espera: tras la llamada se vuelve a esperar el llamado
  }

  botonPrincipal.addEventListener("click", () => (estado === "inactivo" || estado === "cerrada" || estado === "espera" ? empezar() : terminar(true)));
  botonEspera.addEventListener("click", async () => {
    if (esperando) { apagarEspera(); return; }
    if (llamada) return aviso("Termine la conversación para activar la escucha por voz.", "info");
    esperando = true;
    botonEspera.setAttribute("aria-pressed", "true"); botonEspera.replaceChildren(icono("colgar", 18), "Desactivar escucha por voz");
    try { frasesActivacion = (await api("/voz/activacion")).frases; } catch { /* se usa la frase por defecto */ }
    empezarEspera();
  });
  botonSilencio.addEventListener("click", () => {
    const silenciar = botonSilencio.getAttribute("aria-pressed") !== "true";
    llamada?.silenciar(silenciar);
    botonSilencio.setAttribute("aria-pressed", String(silenciar));
    botonSilencio.replaceChildren(icono(silenciar ? "ver" : "microfono", 18), silenciar ? "Activar micrófono" : "Silenciar micrófono");
  });
  botonPantalla.addEventListener("click", () => { if (document.fullscreenElement) document.exitFullscreen(); else pantalla.requestFullscreen?.().catch(() => aviso("El navegador no permitió la pantalla completa.", "warning")); });
  const salir = () => { esperando = false; espera?.detener(); espera = null; cancelAnimationFrame(animacion); llamada?.colgar(); llamada = null; };
  window.addEventListener("hashchange", salir, { once: true }); // al cambiar de sección se cuelga: nunca queda el micrófono abierto
  window.addEventListener("pagehide", salir, { once: true });

  pintarEstado("inactivo");
  const soporta = !!(navigator.mediaDevices?.getUserMedia && window.AudioWorklet && window.isSecureContext);
  if (!soporta) { botonPrincipal.disabled = true; detalle.textContent = "El micrófono requiere HTTPS (o localhost) y un navegador reciente."; }
  sugerir("inicio");

  pantalla.append(
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Atención por voz"),
        h("p", { class: "text-body-secondary mb-0" }, "Conversación completa hablada: el asistente pregunta la institución, pide los datos, ofrece horarios y agenda la cita, todo en voz alta. Se cierra sola cuando el paciente dice que no necesita más ayuda.")),
      botonPantalla),
    h("div", { class: "voz-escenario" },
      quienHabla, orbe, h("div", { class: "voz-estado" }, etiqueta, detalle),
      h("div", { class: "voz-subtitulos" }, h("span", { class: "voz-rotulo" }, "El asistente dice"), asistenteDice, usuarioDijo),
      chips,
      h("div", { class: "voz-controles" }, botonPrincipal, botonSilencio, botonEspera),
      h("div", { class: "voz-nivel-caja" }, h("span", { class: "voz-nivel-rotulo" }, "Micrófono"), nivel),
      puedeAtender ? h("div", { class: "form-check form-switch voz-modo" }, modoReal, h("label", { class: "form-check-label", for: "voz-real" },
        "Es una conversación real con un paciente (cuenta en los reportes). Apagado = modo prueba.")) : null),
    h("details", { class: "voz-detalles" }, h("summary", {}, "Ver la conversación escrita"), historial));
  return pantalla;
}
