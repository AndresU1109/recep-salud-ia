// Chat de prueba con el asistente. Es para el personal: las conversaciones quedan marcadas como de prueba y no cuentan en reportes.
import { api } from "../api.js";
import { aviso, h, insignia } from "../ui.js";
import { ClienteVoz } from "../voz/cliente.js";

const ETIQUETAS = { emergencia: ["Emergencia", "danger"], orientacion_medica: ["Sin orientación médica", "warning"], transferencia: ["Transferencia", "info"], solicitud: ["Solicitud", "info"],
                    agendar_cita: ["Agendar", "primary"], cancelar_cita: ["Cancelar", "primary"], reprogramar_cita: ["Reprogramar", "primary"], consulta_disponibilidad: ["Disponibilidad", "secondary"],
                    consulta_catalogo: ["Catálogo", "secondary"], consulta_politicas: ["Políticas", "secondary"], informacion: ["Documentos", "success"], identificacion: ["Identificación", "secondary"],
                    error_agente: ["Error", "danger"], no_entendido: ["No se entendió el audio", "warning"] };

const ESTADOS_VOZ = { conectando: ["Conectando…", "secondary"], escuchando: ["Escuchando: habla cuando quieras", "success"], tu: ["Te escucho…", "primary"], procesando: ["Pensando…", "warning"],
                      asistente: ["El asistente habla (puedes interrumpirlo hablando)", "info"], cerrada: ["Llamada terminada", "secondary"] };

export async function asistente(ctx) {
  let conversacion = null;
  const lista = h("div", { class: "chat-lista", role: "log", "aria-live": "polite" });
  const entrada = h("input", { class: "form-control", id: "texto", maxlength: 1000, placeholder: "Escribe como lo haría un paciente…", autocomplete: "off", "aria-label": "Mensaje" });
  const enviar = h("button", { class: "btn btn-primary", type: "submit" }, "Enviar");
  const estado = h("div", { class: "small text-body-secondary" });

  function burbuja(rol, texto, extras = null) {
    const el = h("div", { class: `chat-fila ${rol === "usuario" ? "chat-usuario" : "chat-asistente"}` },
      h("div", { class: "chat-burbuja" }, texto), extras ? h("div", { class: "chat-detalles" }, extras) : null);
    lista.append(el);
    lista.scrollTop = lista.scrollHeight;
    return el;
  }

  function detalles(r) {
    const partes = [];
    const [t, c] = ETIQUETAS[r.intencion] ?? [r.intencion, "light"];
    partes.push(insignia(t, c));
    for (const hn of r.herramientas) partes.push(insignia(hn, "dark"));
    if (r.requiere_confirmacion) partes.push(insignia("espera confirmación", "warning"));
    if (r.transferida) partes.push(insignia("transferida a una persona", "info"));
    for (const f of r.fuentes) partes.push(insignia(`Fuente: ${f.titulo} v${f.version}${f.desactualizado ? " (vencido)" : ""}`, f.desactualizado ? "danger" : "success"));
    return partes;
  }

  async function iniciar() {
    lista.replaceChildren(); estado.textContent = "Iniciando…";
    try {
      const r = await api("/agente/conversaciones", { metodo: "POST", cuerpo: { canal: "TEXTO" } });
      conversacion = r.conversacion_id;
      burbuja("asistente", r.texto, detalles(r));
      if (r.terminada) { estado.textContent = "La conversación terminó porque el paciente no necesita más ayuda. Inicia una nueva para seguir probando."; return; }
      estado.textContent = "Conversación de prueba activa.";
      entrada.disabled = enviar.disabled = false; entrada.focus();
    } catch (e) { estado.textContent = ""; aviso(e.message, "danger"); }
  }

  async function mandar(ev) {
    ev.preventDefault();
    const texto = entrada.value.trim();
    if (!texto || !conversacion) return;
    entrada.value = ""; entrada.disabled = enviar.disabled = true;
    burbuja("usuario", texto);
    const pensando = burbuja("asistente", "…");
    try {
      const r = await api(`/agente/conversaciones/${conversacion}/mensajes`, { metodo: "POST", cuerpo: { texto } });
      pensando.remove();
      burbuja("asistente", r.texto, detalles(r));
      if (r.transferida || r.emergencia) estado.textContent = r.emergencia ? "El asistente detectó una posible emergencia." : "La conversación fue transferida a una persona.";
    } catch (e) {
      pensando.remove();
      burbuja("asistente", e.message);
      if (["sesion_expirada", "conversacion_cerrada"].includes(e.codigo)) { estado.textContent = "La sesión terminó. Inicia una conversación nueva."; return; }
    }
    entrada.disabled = enviar.disabled = false; entrada.focus();
  }

  async function nueva() {
    if (conversacion) { try { await api(`/agente/conversaciones/${conversacion}/finalizar`, { metodo: "POST" }); } catch { /* ya terminó */ } }
    await iniciar();
  }

  // ---------------- llamada de voz ----------------
  let llamada = null;
  const botonLlamar = h("button", { class: "btn btn-success", type: "button" }, "Iniciar llamada de voz");
  const botonCortar = h("button", { class: "btn btn-outline-secondary", type: "button", disabled: true }, "Interrumpir al asistente");
  const estadoVoz = h("span", { class: "badge text-bg-secondary", role: "status" }, "Sin llamada");
  const nivel = h("progress", { class: "voz-nivel", max: 1, value: 0, "aria-label": "Volumen del micrófono" });

  function pintarEstadoVoz(clave) {
    const [texto, color] = ESTADOS_VOZ[clave];
    estadoVoz.className = `badge text-bg-${color}`;
    estadoVoz.textContent = texto;
  }

  function alEventoDeVoz(e) {
    if (e.tipo === "transcripcion" && e.texto) burbuja("usuario", e.baja_confianza ? `${e.texto} (poco claro)` : e.texto);
    else if (e.tipo === "respuesta") burbuja("asistente", e.texto, detalles(e));
    else if (e.tipo === "error" && e.codigo !== "ocupado") aviso(e.mensaje, e.codigo === "tts_no_disponible" ? "warning" : "danger");
    else if (e.tipo === "cerrada") terminarLlamada();
  }

  async function empezarLlamada() {
    botonLlamar.disabled = true;
    if (conversacion) { try { await api(`/agente/conversaciones/${conversacion}/finalizar`, { metodo: "POST" }); } catch { /* ya terminó */ } conversacion = null; }
    lista.replaceChildren();
    entrada.disabled = enviar.disabled = true;
    estado.textContent = "Llamada de voz de prueba: la conversación queda marcada como de prueba.";
    pintarEstadoVoz("conectando");
    llamada = new ClienteVoz({ onEstado: pintarEstadoVoz, onNivel: (v) => { nivel.value = v; }, onEvento: alEventoDeVoz });
    try {
      await llamada.iniciar();
      botonLlamar.textContent = "Colgar";
      botonLlamar.className = "btn btn-danger";
      botonCortar.disabled = false;
    } catch (e) {
      aviso(e.name === "NotAllowedError" ? "No se pudo usar el micrófono: el navegador no dio permiso." : e.message, "danger");
      llamada.colgar();
      terminarLlamada();
    }
    botonLlamar.disabled = false;
  }

  function terminarLlamada() {
    if (!llamada) return;
    llamada.colgar();
    llamada = null;
    nivel.value = 0;
    botonCortar.disabled = true;
    botonLlamar.textContent = "Iniciar llamada de voz";
    botonLlamar.className = "btn btn-success";
    estadoVoz.className = "badge text-bg-secondary";
    estadoVoz.textContent = "Sin llamada";
    iniciar();
  }

  botonLlamar.addEventListener("click", () => (llamada ? terminarLlamada() : empezarLlamada()));
  botonCortar.addEventListener("click", () => llamada?.interrumpir());
  const soportaVoz = !!(navigator.mediaDevices?.getUserMedia && window.AudioWorklet && window.isSecureContext);
  if (!soportaVoz) { botonLlamar.disabled = true; estadoVoz.textContent = "El micrófono requiere HTTPS (o localhost) y un navegador reciente"; }
  const panelVoz = h("div", { class: "card mb-3" }, h("div", { class: "card-body d-flex flex-wrap align-items-center gap-3" },
    botonLlamar, botonCortar, estadoVoz, nivel));

  entrada.disabled = enviar.disabled = true;
  await iniciar();
  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Probar asistente"), h("p", { class: "text-body-secondary mb-0" }, "Conversa con el asistente como lo haría un paciente. Debajo de cada respuesta ves qué hizo (herramientas y fuentes).")),
      h("button", { class: "btn btn-outline-secondary", type: "button", onClick: nueva }, "Nueva conversación")),
    panelVoz,
    h("div", { class: "card" }, lista, h("form", { class: "card-footer d-flex gap-2", onSubmit: mandar }, h("div", { class: "flex-grow-1" }, entrada), enviar)),
    h("div", { class: "mt-2" }, estado),
    h("p", { class: "text-body-secondary small mt-3" }, "Usa solo datos ficticios. Los pacientes reales usarán la interfaz de pacientes; esta pantalla es para revisar el comportamiento."));
}
