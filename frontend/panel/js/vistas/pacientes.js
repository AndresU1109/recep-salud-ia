import { api } from "../api.js";
import { aviso, formModal, h, insignia, tabla, ventana } from "../ui.js";

const TIPOS_DOC = [["CC", "Cédula de ciudadanía"], ["TI", "Tarjeta de identidad"], ["RC", "Registro civil"], ["CE", "Cédula de extranjería"],
                   ["PA", "Pasaporte"], ["PPT", "Permiso por protección temporal"]].map(([value, label]) => ({ value, label }));
const ESTADOS_CITA = { PROGRAMADA: "primary", CONFIRMADA: "success", CANCELADA: "secondary", ATENDIDA: "info", INASISTENCIA: "danger" };

const fechaCorta = (iso) => { const [a, m, d] = iso.split("-"); return `${d}/${m}/${a}`; };  // sin pasar por Date: evita corrimientos de zona horaria
const fechaHora = (iso) => new Date(iso).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short", timeZone: "America/Bogota" });
const consentimientoInsignia = (c) => !c ? insignia("Sin registro", "warning") : c.aceptado ? insignia("Datos autorizados", "success") : insignia("Autorización revocada", "danger");

export async function pacientes(ctx) {
  const puede = (p) => ctx.usuario.permisos.includes(p);
  const zona = h("div", { class: "card-body p-0" }, h("p", { class: "text-body-secondary text-center py-4 mb-0" }, "Busca por nombre o por número de documento."));
  const campoBusqueda = h("input", { class: "form-control", type: "search", id: "q", placeholder: "Nombre, apellido o documento", "aria-label": "Buscar paciente", maxlength: 100 });

  async function buscar(e) {
    e?.preventDefault();
    const q = campoBusqueda.value.trim();
    if (q.length < 2) { aviso("Escribe al menos 2 caracteres.", "warning"); return; }
    try { pintar(await api(`/pacientes?q=${encodeURIComponent(q)}`)); } catch (err) { aviso(err.message, "danger"); }
  }

  function pintar(lista) {
    zona.replaceChildren(tabla([
      { titulo: "Paciente", render: (p) => h("div", {}, h("strong", {}, `${p.nombres} ${p.apellidos}`), h("div", { class: "small text-body-secondary" }, `${p.tipo_documento} ${p.documento}`)) },
      { titulo: "Nacimiento", render: (p) => fechaCorta(p.fecha_nacimiento) },
      { titulo: "Contacto", render: (p) => h("div", { class: "small" }, p.telefono ?? "—", h("br"), p.email ?? "") },
      { titulo: "Datos personales", render: (p) => consentimientoInsignia(p.consentimiento_vigente) },
      { titulo: "", render: (p) => h("button", { class: "btn btn-sm btn-outline-primary", type: "button", onClick: () => abrirFicha(p.id) }, "Abrir") },
    ], lista, "No se encontraron pacientes."));
  }

  function nuevo() {
    formModal({
      titulo: "Registrar paciente", guardarTexto: "Registrar",
      campos: [
        { name: "tipo_documento", label: "Tipo de documento", type: "select", required: true, options: TIPOS_DOC },
        { name: "numero_documento", label: "Número de documento", required: true, max: 20 },
        { name: "nombres", label: "Nombres", required: true, max: 120 }, { name: "apellidos", label: "Apellidos", required: true, max: 120 },
        { name: "fecha_nacimiento", label: "Fecha de nacimiento", type: "date", required: true, ayuda: "Se usa para verificar su identidad." },
        { name: "telefono", label: "Teléfono", type: "tel", max: 20 }, { name: "email", label: "Correo electrónico", type: "email", max: 254 },
        { name: "entidad_aseguradora", label: "Entidad aseguradora", max: 150 },
        { name: "politica_version", label: "Versión de la política de datos aceptada", required: true, max: 30, ayuda: "Tal como aparece en el documento que firmó el paciente." },
        { name: "acepta", label: "El paciente autoriza el tratamiento de sus datos personales", type: "checkbox", obligatorio: "Sin la autorización del paciente no se puede registrar." },
        { name: "transcripcion", label: "Autoriza guardar la transcripción de sus conversaciones con el asistente", type: "checkbox" },
      ],
      onGuardar: async (v) => {
        const { acepta, transcripcion, politica_version, ...datos } = v;
        const p = await api("/pacientes", { metodo: "POST", cuerpo: { ...datos, consentimiento: { politica_version, aceptado: acepta, autoriza_transcripcion: transcripcion } } });
        aviso("Paciente registrado.");
        campoBusqueda.value = p.apellidos; pintar([p]);
      },
    });
  }

  async function abrirFicha(id) {
    const { cuerpo } = ventana("Ficha del paciente", { grande: true });
    cuerpo.append(h("div", { class: "text-center py-4" }, h("div", { class: "spinner-border", role: "status" })));
    async function cargar() {
      const [p, cons] = await Promise.all([api(`/pacientes/${id}`), api(`/pacientes/${id}/consentimientos`)]);
      const citas = puede("citas:ver") ? await api(`/pacientes/${id}/citas`) : null;
      const filaDato = (k, v) => h("div", { class: "col-sm-6" }, h("div", { class: "small text-body-secondary" }, k), h("div", {}, v ?? "—"));
      const zonaDoc = h("span", {}, `${p.tipo_documento} ${p.documento}`);
      const botones = h("div", { class: "d-flex flex-wrap gap-2 mt-3" },
        puede("pacientes:gestionar") ? h("button", { class: "btn btn-sm btn-primary", type: "button", onClick: () => editar(p) }, "Editar contacto") : null,
        puede("pacientes:gestionar") ? h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onClick: () => consentimiento(p) }, "Registrar consentimiento") : null,
        puede("pacientes:ver_documento_completo") ? h("button", { class: "btn btn-sm btn-outline-secondary", type: "button", onClick: async (e) => {
          e.target.disabled = true;
          try { const d = await api(`/pacientes/${id}/documento`); zonaDoc.textContent = `${d.tipo_documento} ${d.numero_documento}`; setTimeout(() => { zonaDoc.textContent = `${p.tipo_documento} ${p.documento}`; e.target.disabled = false; }, 15000); }
          catch (err) { aviso(err.message, "danger"); e.target.disabled = false; }
        } }, "Ver documento completo (15 s)") : null);
      cuerpo.replaceChildren(
        h("h3", { class: "h5" }, `${p.nombres} ${p.apellidos}`, " ", p.activo ? null : insignia("Inactivo", "secondary")),
        h("div", { class: "row g-3" }, filaDato("Documento", zonaDoc), filaDato("Nacimiento", fechaCorta(p.fecha_nacimiento)), filaDato("Teléfono", p.telefono),
          filaDato("Correo", p.email), filaDato("Avisos por correo", p.notificaciones_activas ? "Sí" : "No (el paciente los apagó)"), filaDato("Entidad aseguradora", p.entidad_aseguradora), filaDato("Datos personales", consentimientoInsignia(p.consentimiento_vigente))),
        botones,
        h("h4", { class: "h6 mt-4" }, "Citas (historial administrativo)"),
        citas === null ? h("p", { class: "text-body-secondary small" }, "Tu rol no puede ver las citas.") :
          tabla([{ titulo: "Fecha", render: (c) => fechaHora(c.inicio) }, { titulo: "Especialidad", render: (c) => c.especialidad }, { titulo: "Profesional", render: (c) => c.profesional },
                 { titulo: "Sede", render: (c) => c.sede }, { titulo: "Código", render: (c) => h("code", {}, c.codigo_reserva) }, { titulo: "Estado", render: (c) => insignia(c.estado, ESTADOS_CITA[c.estado] ?? "secondary") }],
                citas, "Este paciente no tiene citas."),
        h("h4", { class: "h6 mt-4" }, "Historial de consentimientos"),
        tabla([{ titulo: "Fecha", render: (c) => fechaHora(c.otorgado_en) }, { titulo: "Política", render: (c) => c.politica_version }, { titulo: "Canal", render: (c) => c.canal },
               { titulo: "Decisión", render: (c) => c.aceptado ? insignia("Autoriza", "success") : insignia("Revoca", "danger") }, { titulo: "Transcripción", render: (c) => c.autoriza_transcripcion ? "Sí" : "No" }], cons));
    }
    function editar(p) {
      formModal({
        titulo: "Editar contacto", valores: p,
        campos: [{ name: "nombres", label: "Nombres", required: true, max: 120 }, { name: "apellidos", label: "Apellidos", required: true, max: 120 },
                 { name: "telefono", label: "Teléfono", type: "tel", max: 20 }, { name: "email", label: "Correo electrónico", type: "email", max: 254 },
                 { name: "entidad_aseguradora", label: "Entidad aseguradora", max: 150 }, { name: "activo", label: "Paciente activo", type: "checkbox" },
                 { name: "notificaciones_activas", label: "Enviar avisos por correo (confirmación, cambios, recordatorio)", type: "checkbox" }],
        onGuardar: async (v) => { await api(`/pacientes/${p.id}`, { metodo: "PATCH", cuerpo: v }); aviso("Datos actualizados."); await cargar(); },
      });
    }
    function consentimiento(p) {
      formModal({
        titulo: "Registrar consentimiento", guardarTexto: "Registrar",
        campos: [{ name: "politica_version", label: "Versión de la política de datos", required: true, max: 30 },
                 { name: "aceptado", label: "El paciente autoriza el tratamiento de sus datos (desmarcar = revocación)", type: "checkbox", defecto: true },
                 { name: "autoriza_transcripcion", label: "Autoriza guardar la transcripción de sus conversaciones", type: "checkbox" }],
        onGuardar: async (v) => { await api(`/pacientes/${p.id}/consentimientos`, { metodo: "POST", cuerpo: v }); aviso("Consentimiento registrado."); await cargar(); },
      });
    }
    try { await cargar(); } catch (e) { cuerpo.replaceChildren(h("div", { class: "alert alert-danger" }, e.message)); }
  }

  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Pacientes"), h("p", { class: "text-body-secondary mb-0" }, "Los datos personales se muestran enmascarados; cada consulta queda registrada.")),
      puede("pacientes:gestionar") ? h("button", { class: "btn btn-primary", type: "button", onClick: nuevo }, "+ Registrar paciente") : null),
    h("div", { class: "card" },
      h("form", { class: "card-header d-flex gap-2", onSubmit: buscar }, h("div", { class: "flex-grow-1" }, campoBusqueda), h("button", { class: "btn btn-outline-primary", type: "submit" }, "Buscar")),
      zona));
}
