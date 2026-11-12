// Citas, inasistencias y lista de espera.
import { api } from "../api.js";
import { fechaHora, hoyEn } from "../tiempo.js";
import { aviso, h, insignia, tabla, ventana } from "../ui.js";
import { selectorCupos, selectorPaciente } from "./componentes.js";

const COLOR = { PROGRAMADA: "primary", CONFIRMADA: "success", CANCELADA: "secondary", ATENDIDA: "info", INASISTENCIA: "danger" };
const sumarDias = (iso, n) => { const d = new Date(`${iso}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

function botonModal(texto, clase, accion) {
  const b = h("button", { type: "button", class: `btn ${clase}` }, texto);
  b.addEventListener("click", async () => { b.disabled = true; try { await accion(); } finally { b.disabled = false; } });
  return b;
}

// ---------- citas ----------
export async function citas(ctx) {
  const zona = ctx.config.zona;
  const puede = (p) => ctx.usuario.permisos.includes(p);
  const [profesionales, especialidades] = await Promise.all([api("/profesionales"), api("/especialidades")]);
  const hoy = hoyEn(zona);
  const desde = h("input", { class: "form-control", type: "date", value: hoy, "aria-label": "Desde" });
  const hasta = h("input", { class: "form-control", type: "date", value: sumarDias(hoy, 7), "aria-label": "Hasta" });
  const prof = h("select", { class: "form-select", "aria-label": "Profesional" }, h("option", { value: "" }, "Todos los profesionales"), profesionales.map((p) => h("option", { value: p.id }, p.nombre_completo)));
  const estado = h("select", { class: "form-select", "aria-label": "Estado" }, h("option", { value: "" }, "Todos los estados"), Object.keys(COLOR).map((e) => h("option", { value: e }, e)));
  const codigo = h("input", { class: "form-control", type: "search", placeholder: "Código de reserva", "aria-label": "Código", maxlength: 12 });
  const zonaTabla = h("div", { class: "card-body p-0" });

  async function cargar() {
    try {
      let lista;
      if (codigo.value.trim()) lista = [await api(`/citas/codigo/${encodeURIComponent(codigo.value.trim())}`)];
      else {
        const q = new URLSearchParams();
        if (desde.value) q.set("desde", desde.value); if (hasta.value) q.set("hasta", hasta.value);
        if (prof.value) q.set("profesional_id", prof.value); if (estado.value) q.set("estado", estado.value);
        lista = await api(`/citas?${q}`);
      }
      zonaTabla.replaceChildren(tabla([
        { titulo: "Fecha", render: (c) => fechaHora(c.inicio, zona) },
        { titulo: "Paciente", render: (c) => c.paciente ?? h("span", { class: "text-body-secondary" }, "(oculto por permisos)") },
        { titulo: "Atiende", render: (c) => h("div", {}, c.profesional, h("div", { class: "small text-body-secondary" }, `${c.especialidad} · ${c.sede}`)) },
        { titulo: "Estado", render: (c) => insignia(c.estado, COLOR[c.estado]) }, { titulo: "Código", render: (c) => h("code", {}, c.codigo_reserva) },
        { titulo: "", render: (c) => h("button", { class: "btn btn-sm btn-outline-primary", type: "button", onClick: () => detalle(c.id) }, "Abrir") },
      ], lista, "No hay citas con esos filtros."));
    } catch (e) { zonaTabla.replaceChildren(h("div", { class: "alert alert-danger m-3" }, e.message)); }
  }

  // ----- detalle y acciones -----
  async function detalle(id) {
    const { cuerpo } = ventana("Cita", { grande: true });
    async function pintar() {
      const [c, hist] = await Promise.all([api(`/citas/${id}`), api(`/citas/${id}/historial`)]);
      const activa = c.estado === "PROGRAMADA" || c.estado === "CONFIRMADA";
      const yaPaso = new Date(c.inicio) <= new Date();
      const acciones = [];
      if (puede("citas:gestionar") && activa && !yaPaso) {
        if (c.estado === "PROGRAMADA") acciones.push(botonModal("Confirmar", "btn-success btn-sm", async () => { await ejecutar(`/citas/${id}/confirmar`, {}); }));
        acciones.push(botonModal("Reprogramar", "btn-outline-primary btn-sm", () => reprogramar(c)));
        acciones.push(botonModal("Cancelar cita", "btn-outline-danger btn-sm", () => cancelar(c)));
      }
      if (puede("citas:gestionar") && activa && yaPaso) {
        acciones.push(botonModal("Marcar atendida", "btn-info btn-sm", async () => { await ejecutar(`/citas/${id}/atendida`, {}); }));
        acciones.push(botonModal("Registrar inasistencia", "btn-outline-danger btn-sm", async () => { await ejecutar(`/citas/${id}/inasistencia`, { observacion: prompt("Observación (opcional):") || null }); }));
      }
      const dato = (k, v) => h("div", { class: "col-sm-6" }, h("div", { class: "small text-body-secondary" }, k), h("div", {}, v ?? "—"));
      cuerpo.replaceChildren(
        h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" }, h("h3", { class: "h5 mb-0" }, fechaHora(c.inicio, zona)), insignia(c.estado, COLOR[c.estado])),
        h("div", { class: "row g-3" }, dato("Código de reserva", h("code", { class: "fs-6" }, c.codigo_reserva)), dato("Paciente", c.paciente), dato("Profesional", c.profesional),
          dato("Especialidad", c.especialidad), dato("Servicio", c.servicio), dato("Lugar", `${c.sede}${c.consultorio ? ` · ${c.consultorio}` : ""}`), dato("Canal", c.canal),
          dato("Reprogramaciones", c.reprogramaciones), c.motivo_cancelacion ? dato("Motivo de cancelación", c.motivo_cancelacion) : null),
        h("div", { class: "d-flex flex-wrap gap-2 mt-3" }, acciones),
        h("h4", { class: "h6 mt-4" }, "Historial de cambios"),
        tabla([{ titulo: "Cuándo", render: (x) => fechaHora(x.ocurrio_en, zona) }, { titulo: "Acción", render: (x) => x.accion }, { titulo: "Quién", render: (x) => x.actor_tipo.toLowerCase() },
               { titulo: "Detalle", render: (x) => x.inicio_anterior ? `Antes: ${fechaHora(x.inicio_anterior, zona)}` : x.detalle?.motivo ?? (x.detalle?.forzado ? "Política omitida por autorización" : "") }], hist));
    }
    async function ejecutar(ruta, cuerpoPost) {
      try { await api(ruta, { metodo: "POST", cuerpo: cuerpoPost }); aviso("Listo."); await pintar(); await cargar(); } catch (e) { aviso(e.message, "danger"); }
    }
    async function cancelar(c) {
      const motivo = prompt("Motivo de la cancelación (opcional):");
      if (motivo === null) return;
      try {
        const r = await api(`/citas/${id}/cancelar`, { metodo: "POST", cuerpo: { motivo: motivo || null } });
        aviso("Cita cancelada."); if (r.lista_espera_candidatos) aviso(`Hay ${r.lista_espera_candidatos} persona(s) en lista de espera para un cupo como este. Revisa «Lista de espera».`, "warning");
      } catch (e) {
        if (e.codigo === "politica_incumplida" && puede("inasistencias:verificar") && confirm(`${e.message}\n\n¿Cancelar de todos modos, con tu autorización? Quedará registrado.`)) {
          try { await api(`/citas/${id}/cancelar`, { metodo: "POST", cuerpo: { motivo: motivo || null, forzar: true } }); aviso("Cita cancelada (política omitida)."); } catch (e2) { aviso(e2.message, "danger"); }
        } else aviso(e.message, "danger");
      }
      await pintar(); await cargar();
    }
    function reprogramar(c) {
      const v = ventana("Reprogramar cita", { grande: true });
      const sel = selectorCupos(ctx, { especialidad_id: c.especialidad_id, profesional_id: "" });
      v.cuerpo.append(h("p", { class: "text-body-secondary" }, "Elige el nuevo cupo. La cita conserva su código y deberá confirmarse otra vez."), sel.nodo,
        botonModal("Reprogramar", "btn-primary", async () => {
          const s = sel.valor(); if (!s) { aviso("Elige un cupo.", "warning"); return; }
          try {
            await api(`/citas/${id}/reprogramar`, { metodo: "POST", cuerpo: { inicio: s.inicio, profesional_id: s.profesional_id } });
            aviso("Cita reprogramada."); v.cerrar(); await pintar(); await cargar();
          } catch (e) {
            if (e.codigo === "politica_incumplida" && puede("inasistencias:verificar") && confirm(`${e.message}\n\n¿Reprogramar de todos modos, con tu autorización?`)) {
              try { await api(`/citas/${id}/reprogramar`, { metodo: "POST", cuerpo: { inicio: s.inicio, profesional_id: s.profesional_id, forzar: true } }); v.cerrar(); await pintar(); await cargar(); } catch (e2) { aviso(e2.message, "danger"); }
            } else aviso(e.message, "danger");
          }
        }));
      sel.buscar();
    }
    try { await pintar(); } catch (e) { cuerpo.replaceChildren(h("div", { class: "alert alert-danger" }, e.message)); }
  }

  // ----- nueva cita -----
  function nueva() {
    const v = ventana("Nueva cita", { grande: true });
    const pac = selectorPaciente();
    const esp = h("select", { class: "form-select", id: "esp" }, h("option", { value: "" }, "Selecciona…"), especialidades.filter((e) => e.activa).map((e) => h("option", { value: e.id }, e.nombre)));
    const serv = h("select", { class: "form-select", id: "serv" }, h("option", { value: "" }, "Duración estándar del horario"));
    const zonaCupos = h("div", {});
    let selector = null;
    esp.addEventListener("change", async () => {
      serv.replaceChildren(h("option", { value: "" }, "Duración estándar del horario"));
      zonaCupos.replaceChildren(); selector = null;
      if (!esp.value) return;
      for (const s of (await api(`/servicios?especialidad_id=${esp.value}`)).filter((x) => x.activo)) serv.append(h("option", { value: s.id }, `${s.nombre} (${s.duracion_minutos} min)`));
      armar();
    });
    serv.addEventListener("change", armar);
    function armar() { selector = selectorCupos(ctx, { especialidad_id: esp.value, servicio_id: serv.value }); zonaCupos.replaceChildren(selector.nodo); selector.buscar(); }
    v.cuerpo.append(pac.nodo,
      h("div", { class: "row g-2 mb-3" }, h("div", { class: "col-md-6" }, h("label", { class: "form-label", for: "esp" }, "Especialidad *"), esp), h("div", { class: "col-md-6" }, h("label", { class: "form-label", for: "serv" }, "Servicio"), serv)),
      zonaCupos, botonModal("Agendar cita", "btn-primary", async () => {
        const p = pac.valor(), s = selector?.valor();
        if (!p) { aviso("Elige un paciente.", "warning"); return; } if (!s) { aviso("Elige un cupo.", "warning"); return; }
        try {
          const c = await api("/citas", { metodo: "POST", cuerpo: { paciente_id: p.id, profesional_id: s.profesional_id, especialidad_id: s.especialidad_id, servicio_id: serv.value ? Number(serv.value) : null, inicio: s.inicio, canal: "PRESENCIAL" } });
          aviso(`Cita agendada. Código de reserva: ${c.codigo_reserva}`); v.cerrar(); await cargar();
        } catch (e) { aviso(e.message, "danger"); if (e.codigo === "cupo_no_disponible") selector?.buscar(); }
      }));
  }

  [desde, hasta, prof, estado].forEach((el) => el.addEventListener("change", cargar));
  codigo.addEventListener("keydown", (e) => { if (e.key === "Enter") cargar(); });
  await cargar();
  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Citas"), h("p", { class: "text-body-secondary mb-0" }, "Agenda, confirma, reprograma y cancela. Cada cambio queda en el historial.")),
      puede("citas:gestionar") && puede("pacientes:ver") ? h("button", { class: "btn btn-primary", type: "button", onClick: nueva }, "+ Nueva cita") : null),
    h("div", { class: "card" }, h("div", { class: "card-header" }, h("div", { class: "row g-2" },
      h("div", { class: "col-6 col-md-2" }, desde), h("div", { class: "col-6 col-md-2" }, hasta), h("div", { class: "col-md-3" }, prof), h("div", { class: "col-md-2" }, estado), h("div", { class: "col-md-3" }, codigo))), zonaTabla));
}

// ---------- inasistencias ----------
export async function inasistencias(ctx) {
  const zona = ctx.config.zona;
  const pol = await api("/politicas/aplicable?tipo=INASISTENCIA");
  let pendientes = true;
  const zonaTabla = h("div", { class: "card-body p-0" });
  const filtro = h("select", { class: "form-select w-auto", "aria-label": "Mostrar" }, h("option", { value: "1" }, "Por verificar"), h("option", { value: "0" }, "Ya revisadas"));

  async function cargar() {
    const lista = await api(`/inasistencias?pendientes=${pendientes}`);
    zonaTabla.replaceChildren(tabla([
      { titulo: "Fecha de la cita", render: (i) => fechaHora(i.fecha, zona) }, { titulo: "Paciente", render: (i) => h("strong", {}, i.paciente) }, { titulo: "Observación", render: (i) => i.observacion ?? "—" },
      { titulo: "Resultado", render: (i) => i.pendiente ? insignia("Pendiente", "warning") : i.verificada ? h("div", {}, insignia("Confirmada", "danger"), i.consecuencia_aplicada ? h("div", { class: "small text-body-secondary" }, i.consecuencia_aplicada) : null) : insignia("Descartada", "secondary") },
      { titulo: "", render: (i) => !i.pendiente ? null : h("div", { class: "btn-group btn-group-sm" },
          h("button", { class: "btn btn-outline-danger", type: "button", onClick: () => revisar(i, true) }, "Confirmar"), h("button", { class: "btn btn-outline-secondary", type: "button", onClick: () => revisar(i, false) }, "Sí asistió")) },
    ], lista, pendientes ? "No hay inasistencias por verificar." : "Aún no hay inasistencias revisadas."));
  }
  async function revisar(i, confirmada) {
    let aplicar = false;
    if (confirmada) {
      if (!confirm(`¿Confirmar que ${i.paciente} no asistió a su cita? Contará como inasistencia verificada.`)) return;
      if (pol?.consecuencia_autorizada) aplicar = confirm(`La política vigente autoriza esta consecuencia:\n\n«${pol.consecuencia_autorizada}»\n\n¿Aplicarla a este caso?`);
    } else if (!confirm("¿Descartar? La cita pasará a «atendida» y no contará como inasistencia.")) return;
    try { await api(`/inasistencias/${i.id}/verificar`, { metodo: "POST", cuerpo: { confirmada, aplicar_consecuencia: aplicar } }); aviso("Registrado."); await cargar(); } catch (e) { aviso(e.message, "danger"); }
  }
  filtro.addEventListener("change", () => { pendientes = filtro.value === "1"; cargar(); });
  await cargar();
  return h("section", {}, h("h1", { class: "h3 mb-1" }, "Inasistencias"),
    h("p", { class: "text-body-secondary" }, "Una inasistencia solo cuenta cuando una persona autorizada la verifica. Nada se sanciona automáticamente.",
      pol ? null : " No hay política de inasistencia registrada, así que no se puede aplicar ninguna consecuencia."),
    h("div", { class: "card" }, h("div", { class: "card-header" }, filtro), zonaTabla));
}

// ---------- lista de espera ----------
export async function listaEspera(ctx) {
  const zona = ctx.config.zona;
  const [especialidades, profesionales] = await Promise.all([api("/especialidades"), api("/profesionales")]);
  const nombre = (lista, id, c = "nombre") => lista.find((x) => x.id === id)?.[c] ?? "—";
  const zonaTabla = h("div", { class: "card-body p-0" });

  async function cargar() {
    const lista = await api("/lista-espera");
    zonaTabla.replaceChildren(tabla([
      { titulo: "Paciente", render: (i) => `#${i.paciente_id}` }, { titulo: "Especialidad", render: (i) => nombre(especialidades, i.especialidad_id) },
      { titulo: "Prefiere", render: (i) => i.profesional_id ? nombre(profesionales, i.profesional_id, "nombre_completo") : "Cualquiera" },
      { titulo: "Entre", render: (i) => `${i.desde} → ${i.hasta}` },
      { titulo: "", render: (i) => h("div", { class: "btn-group btn-group-sm" }, h("button", { class: "btn btn-outline-primary", type: "button", onClick: () => asignar(i) }, "Asignar cupo"),
          h("button", { class: "btn btn-outline-secondary", type: "button", onClick: async () => { if (!confirm("¿Quitar de la lista de espera?")) return; try { await api(`/lista-espera/${i.id}/cancelar`, { metodo: "POST" }); await cargar(); } catch (e) { aviso(e.message, "danger"); } } }, "Quitar")) },
    ], lista, "Nadie está en lista de espera."));
  }
  function asignar(i) {
    const v = ventana("Asignar cupo", { grande: true });
    const sel = selectorCupos(ctx, { especialidad_id: i.especialidad_id, servicio_id: i.servicio_id, profesional_id: i.profesional_id, sede_id: i.sede_id });
    v.cuerpo.append(sel.nodo, botonModal("Agendar", "btn-primary", async () => {
      const s = sel.valor(); if (!s) { aviso("Elige un cupo.", "warning"); return; }
      try { const c = await api(`/lista-espera/${i.id}/asignar`, { metodo: "POST", cuerpo: { profesional_id: s.profesional_id, inicio: s.inicio } }); aviso(`Cita agendada: ${c.codigo_reserva}`); v.cerrar(); await cargar(); } catch (e) { aviso(e.message, "danger"); }
    }));
    sel.buscar();
  }
  function agregar() {
    const v = ventana("Agregar a la lista de espera");
    const pac = selectorPaciente();
    const esp = h("select", { class: "form-select", id: "e2" }, h("option", { value: "" }, "Selecciona…"), especialidades.filter((e) => e.activa).map((e) => h("option", { value: e.id }, e.nombre)));
    const hoy = hoyEn(zona);
    const desde = h("input", { class: "form-control", type: "date", value: hoy }), hasta = h("input", { class: "form-control", type: "date", value: sumarDias(hoy, 30) });
    v.cuerpo.append(pac.nodo, h("div", { class: "mb-3" }, h("label", { class: "form-label", for: "e2" }, "Especialidad *"), esp),
      h("div", { class: "row g-2 mb-3" }, h("div", { class: "col" }, h("label", { class: "form-label" }, "Desde"), desde), h("div", { class: "col" }, h("label", { class: "form-label" }, "Hasta"), hasta)),
      botonModal("Agregar", "btn-primary", async () => {
        const p = pac.valor(); if (!p || !esp.value) { aviso("Elige paciente y especialidad.", "warning"); return; }
        try { await api("/lista-espera", { metodo: "POST", cuerpo: { paciente_id: p.id, especialidad_id: Number(esp.value), desde: desde.value, hasta: hasta.value } }); aviso("Agregado."); v.cerrar(); await cargar(); } catch (e) { aviso(e.message, "danger"); }
      }));
  }
  await cargar();
  return h("section", {}, h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
    h("div", {}, h("h1", { class: "h3 mb-1" }, "Lista de espera"), h("p", { class: "text-body-secondary mb-0" }, "Pacientes que esperan un cupo. Al cancelarse una cita, aquí puedes ofrecérselo en orden de llegada.")),
    ctx.usuario.permisos.includes("pacientes:ver") ? h("button", { class: "btn btn-primary", type: "button", onClick: agregar }, "+ Agregar") : null),
    h("div", { class: "card" }, zonaTabla));
}
