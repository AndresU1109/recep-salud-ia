import { api } from "../api.js";
import { aviso, estadoInsignia, formModal, h, insignia, tabla } from "../ui.js";

export const ROLES = {
  ADMINISTRADOR: "Administrador", RECEPCIONISTA: "Recepcionista", PROFESIONAL_SALUD: "Profesional de salud",
  COORDINADOR: "Coordinador", CONSULTA: "Consulta",
};
const opcionesRol = Object.entries(ROLES).map(([value, label]) => ({ value, label }));
const fecha = (iso) => (iso ? new Date(iso).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" }) : "Nunca");

export async function usuarios(ctx) {
  let lista = await api("/usuarios");
  const zona = h("div", { class: "card-body p-0" });

  const recargar = async () => { lista = await api("/usuarios"); pintar(); };

  function editar(u) {
    formModal({
      titulo: `Editar: ${u.nombre}`, valores: u,
      campos: [{ name: "nombre", label: "Nombre", required: true, max: 150 }, { name: "rol", label: "Rol", type: "select", required: true, options: opcionesRol }],
      onGuardar: async (v) => { await api(`/usuarios/${u.id}`, { metodo: "PATCH", cuerpo: v }); aviso("Cambios guardados."); await recargar(); },
    });
  }
  async function conmutar(u) {
    if (u.activo && !confirm(`¿Desactivar a ${u.nombre}? No podrá iniciar sesión.`)) return;
    try { await api(`/usuarios/${u.id}`, { metodo: "PATCH", cuerpo: { activo: !u.activo } }); aviso(u.activo ? "Usuario desactivado." : "Usuario activado."); await recargar(); }
    catch (e) { aviso(e.message, "danger"); }
  }
  function restablecer(u) {
    formModal({
      titulo: `Restablecer contraseña de ${u.nombre}`, guardarTexto: "Asignar",
      campos: [{ name: "clave_temporal", label: "Contraseña temporal", type: "password", required: true, autocomplete: "new-password", max: 200,
                 ayuda: "Mínimo 12 caracteres, con mayúsculas, minúsculas y números. La persona deberá cambiarla al entrar." }],
      onGuardar: async (v) => { await api(`/usuarios/${u.id}/restablecer-clave`, { metodo: "POST", cuerpo: v }); aviso("Contraseña temporal asignada."); await recargar(); },
    });
  }
  function nuevo() {
    formModal({
      titulo: "Nuevo usuario", guardarTexto: "Crear",
      campos: [{ name: "nombre", label: "Nombre completo", required: true, max: 150 }, { name: "email", label: "Correo electrónico", type: "email", required: true, max: 254, autocomplete: "off" },
               { name: "rol", label: "Rol", type: "select", required: true, options: opcionesRol },
               { name: "clave_temporal", label: "Contraseña temporal", type: "password", required: true, autocomplete: "new-password", max: 200,
                 ayuda: "Mínimo 12 caracteres, con mayúsculas, minúsculas y números. Debe cambiarla en su primer ingreso." }],
      onGuardar: async (v) => { await api("/usuarios", { metodo: "POST", cuerpo: v }); aviso("Usuario creado."); await recargar(); },
    });
  }

  const columnas = [
    { titulo: "Nombre", render: (u) => h("div", {}, h("strong", {}, u.nombre), h("div", { class: "small text-body-secondary" }, u.email)) },
    { titulo: "Rol", render: (u) => insignia(ROLES[u.rol] ?? u.rol, u.rol === "ADMINISTRADOR" ? "danger" : "primary") },
    { titulo: "Último acceso", render: (u) => fecha(u.ultimo_acceso) },
    { titulo: "Estado", render: (u) => h("div", {}, estadoInsignia(u.activo), u.debe_cambiar_clave ? h("div", { class: "small text-warning-emphasis" }, "Debe cambiar su clave") : null) },
    { titulo: "", render: (u) => h("div", { class: "btn-group btn-group-sm" },
        h("button", { class: "btn btn-outline-primary", type: "button", onClick: () => editar(u) }, "Editar"),
        h("button", { class: "btn btn-outline-secondary", type: "button", onClick: () => restablecer(u) }, "Clave"),
        u.id === ctx.usuario.id ? null : h("button", { class: "btn btn-outline-secondary", type: "button", onClick: () => conmutar(u) }, u.activo ? "Desactivar" : "Activar")) },
  ];
  const pintar = () => zona.replaceChildren(tabla(columnas, lista));
  pintar();

  return h("section", {},
    h("div", { class: "d-flex flex-wrap justify-content-between align-items-start gap-2 mb-3" },
      h("div", {}, h("h1", { class: "h3 mb-1" }, "Usuarios y roles"), h("p", { class: "text-body-secondary mb-0" }, "Quién puede entrar al panel y qué puede hacer.")),
      h("button", { class: "btn btn-primary", type: "button", onClick: nuevo }, "+ Nuevo usuario")),
    h("div", { class: "card" }, zona));
}
