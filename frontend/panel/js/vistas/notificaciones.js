// Avisos a pacientes por correo: estado de los canales, bandeja y reintentos. El texto de cada aviso no se muestra aquí (puede ser sensible).
import { api } from "../api.js";
import { aviso, h, insignia, tabla } from "../ui.js";
import { fechaHora } from "../tiempo.js";

const TIPOS = { CITA_AGENDADA: "Cita agendada", CITA_CANCELADA: "Cita cancelada", CITA_REPROGRAMADA: "Cita reprogramada", RECORDATORIO: "Recordatorio", CUPO_DISPONIBLE: "Cupo liberado (lista de espera)" };
const ESTADOS = { PENDIENTE: ["Pendiente", "warning"], ENVIADA: ["Enviada", "success"], FALLIDA: ["Falló", "danger"], OMITIDA: ["Omitida", "secondary"] };
const MOTIVOS = { sin_email: "El paciente no tiene correo", paciente_no_acepta: "El paciente no quiere avisos", canal_no_configurado: "El correo no está configurado", cita_cambio: "La cita cambió",
                  cita_pasada: "La cita ya pasó", espera_cambio: "Ya no está en espera", cupo_pasado: "El cupo ya pasó", smtp_no_disponible: "El servidor de correo no respondió",
                  smtp_autenticacion: "Usuario o clave del correo incorrectos", smtp_destino_rechazado: "El correo del paciente fue rechazado" };

export async function notificaciones(ctx) {
  const zona = ctx.config.zona;
  const resumen = h("div", { class: "card-body d-flex flex-wrap gap-2 align-items-center" });
  const zonaTabla = h("div", { class: "card-body p-0" });
  const filtro = h("select", { class: "form-select form-select-sm w-auto", "aria-label": "Filtrar por estado" },
    h("option", { value: "" }, "Todos los estados"), Object.entries(ESTADOS).map(([k, [t]]) => h("option", { value: k }, t)));

  async function cargar() {
    const [e, lista] = await Promise.all([api("/notificaciones/estado"), api(`/notificaciones${filtro.value ? `?estado=${filtro.value}` : ""}`)]);
    resumen.replaceChildren(
      insignia(e.canales.EMAIL ? "Correo: configurado" : "Correo: SIN configurar", e.canales.EMAIL ? "success" : "danger"),
      insignia("SMS / WhatsApp: no disponible", "secondary"),
      ...Object.entries(ESTADOS).map(([k, [t, c]]) => insignia(`${t}: ${e.conteo[k]}`, c)),
      h("span", { class: "small text-body-secondary" }, `Recordatorio ${e.recordatorio_horas_antes} h antes · se envían entre las ${e.horario[0]}:00 y las ${e.horario[1]}:00 · lista de espera: hasta ${e.espera_notificar_maximo} personas por cupo`),
      e.canales.EMAIL ? null : h("div", { class: "w-100 small text-danger" }, "Sin servidor de correo (SMTP_HOST y SMTP_REMITENTE en .env) los avisos quedan registrados como omitidos: nadie los recibe."));
    zonaTabla.replaceChildren(tabla([
      { titulo: "Programada", render: (n) => fechaHora(n.programada_para, zona) },
      { titulo: "Aviso", render: (n) => TIPOS[n.tipo] ?? n.tipo },
      { titulo: "Paciente", render: (n) => `#${n.paciente_id}${n.destino_enmascarado ? ` · ${n.destino_enmascarado}` : ""}` },
      { titulo: "Estado", render: (n) => insignia(...ESTADOS[n.estado]) },
      { titulo: "Detalle", render: (n) => [n.enviada_en ? `Enviada ${fechaHora(n.enviada_en, zona)}` : "", n.motivo ? (MOTIVOS[n.motivo] ?? n.motivo) : "", n.intentos ? `(${n.intentos} intento/s)` : ""].filter(Boolean).join(" · ") },
      { titulo: "", render: (n) => n.estado === "FALLIDA" ? h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onClick: async () => {
        try { await api(`/notificaciones/${n.id}/reintentar`, { metodo: "POST" }); aviso("Aviso puesto en cola de nuevo."); await cargar(); } catch (err) { aviso(err.message, "danger"); } } }, "Reintentar") : null },
    ], lista, "Todavía no hay avisos."));
  }

  async function procesar(ev) {
    const boton = ev.currentTarget; // currentTarget vale null después de un await
    boton.disabled = true;
    try {
      const r = await api("/notificaciones/procesar", { metodo: "POST" });
      const partes = Object.entries(r).filter(([, v]) => v).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`);
      aviso(partes.length ? `Listo. ${partes.join(", ")}.` : "No había nada que enviar.");
      await cargar();
    } catch (err) { aviso(err.message, "danger"); } finally { boton.disabled = false; }
  }

  filtro.addEventListener("change", cargar);
  await cargar();
  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Notificaciones"), h("p", { class: "text-body-secondary mb-0" }, "Avisos por correo sobre las citas de cada paciente. El servidor los revisa y envía solo cada minuto; «Procesar ahora» lo adelanta.")),
      h("div", { class: "d-flex gap-2 align-items-center" }, filtro, h("button", { class: "btn btn-primary", type: "button", onClick: procesar }, "Procesar ahora"))),
    h("div", { class: "card mb-3" }, resumen),
    h("div", { class: "card" }, zonaTabla));
}
