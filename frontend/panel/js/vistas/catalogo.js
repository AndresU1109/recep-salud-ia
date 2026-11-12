// Vistas del catálogo: sedes, consultorios, especialidades, servicios, requisitos y profesionales.
import { api } from "../api.js";
import { vistaCrud } from "../crud.js";
import { h, insignia } from "../ui.js";

const opciones = (lista, texto = (x) => x.nombre) => lista.map((x) => ({ value: x.id, label: texto(x) }));
const nombreDe = (lista, id) => lista.find((x) => x.id === id)?.nombre ?? "—";
const gestiona = (ctx) => ctx.usuario.permisos.includes("agenda:gestionar");

export const sedes = (ctx) => vistaCrud({
  titulo: "Sedes", descripcion: "Lugares físicos donde se atiende a los pacientes.", ruta: "/sedes", campoActivo: "activa",
  nuevoTexto: "Nueva sede", puedeGestionar: gestiona(ctx), nombreFila: (f) => f.nombre,
  columnas: [{ titulo: "Nombre", render: (f) => h("strong", {}, f.nombre) }, { titulo: "Ciudad", render: (f) => f.ciudad ?? "—" },
             { titulo: "Dirección", render: (f) => f.direccion ?? "—" }, { titulo: "Teléfono", render: (f) => f.telefono ?? "—" }],
  campos: () => [{ name: "nombre", label: "Nombre", required: true, max: 150 }, { name: "ciudad", label: "Ciudad", max: 100 },
                 { name: "direccion", label: "Dirección", max: 250 }, { name: "telefono", label: "Teléfono", max: 30 },
                 { name: "activa", label: "Sede activa", type: "checkbox", defecto: true }],
})(ctx);

export const consultorios = (ctx) => vistaCrud({
  titulo: "Consultorios", descripcion: "Espacios de atención dentro de cada sede.", ruta: "/consultorios", campoActivo: "activo",
  nuevoTexto: "Nuevo consultorio", puedeGestionar: gestiona(ctx), nombreFila: (f) => f.nombre,
  cargarExtras: async () => ({ sedes: await api("/sedes") }),
  textoFila: (f, c) => `${f.nombre} ${nombreDe(c.sedes, f.sede_id)}`,
  columnas: [{ titulo: "Consultorio", render: (f) => h("strong", {}, f.nombre) }, { titulo: "Sede", render: (f, c) => nombreDe(c.sedes, f.sede_id) },
             { titulo: "Piso", render: (f) => f.piso ?? "—" }],
  campos: (c) => [{ name: "sede_id", label: "Sede", type: "select", numero: true, required: true, soloCrear: true, options: opciones(c.sedes.filter((s) => s.activa)) },
                  { name: "nombre", label: "Nombre o número", required: true, max: 100 }, { name: "piso", label: "Piso", max: 20 },
                  { name: "activo", label: "Consultorio activo", type: "checkbox", defecto: true }],
})(ctx);

export const especialidades = (ctx) => vistaCrud({
  titulo: "Especialidades", descripcion: "Áreas médicas que ofrece la institución.", ruta: "/especialidades", campoActivo: "activa",
  nuevoTexto: "Nueva especialidad", puedeGestionar: gestiona(ctx), nombreFila: (f) => f.nombre,
  columnas: [{ titulo: "Especialidad", render: (f) => h("strong", {}, f.nombre) }, { titulo: "Descripción", render: (f) => f.descripcion ?? "—" }],
  campos: () => [{ name: "nombre", label: "Nombre", required: true, max: 150 }, { name: "descripcion", label: "Descripción", type: "textarea" },
                 { name: "activa", label: "Especialidad activa", type: "checkbox", defecto: true }],
})(ctx);

export const servicios = (ctx) => vistaCrud({
  titulo: "Servicios", descripcion: "Tipos de cita de cada especialidad y su duración.", ruta: "/servicios", campoActivo: "activo",
  nuevoTexto: "Nuevo servicio", puedeGestionar: gestiona(ctx), nombreFila: (f) => f.nombre,
  cargarExtras: async () => ({ especialidades: await api("/especialidades") }),
  textoFila: (f, c) => `${f.nombre} ${nombreDe(c.especialidades, f.especialidad_id)}`,
  columnas: [{ titulo: "Servicio", render: (f) => h("strong", {}, f.nombre) }, { titulo: "Especialidad", render: (f, c) => nombreDe(c.especialidades, f.especialidad_id) },
             { titulo: "Duración", render: (f) => `${f.duracion_minutos} min` }],
  campos: (c) => [{ name: "especialidad_id", label: "Especialidad", type: "select", numero: true, required: true, soloCrear: true, options: opciones(c.especialidades.filter((e) => e.activa)) },
                  { name: "nombre", label: "Nombre", required: true, max: 150 },
                  { name: "duracion_minutos", label: "Duración (minutos)", type: "number", required: true, min: 1, max: 480 },
                  { name: "descripcion", label: "Descripción", type: "textarea" }, { name: "activo", label: "Servicio activo", type: "checkbox", defecto: true }],
})(ctx);

export const requisitos = (ctx) => vistaCrud({
  titulo: "Requisitos", descripcion: "Lo que el paciente debe presentar o cumplir. Sin especialidad ni servicio, aplica a todos.", ruta: "/requisitos", campoActivo: "activo",
  nuevoTexto: "Nuevo requisito", puedeGestionar: gestiona(ctx), nombreFila: (f) => f.titulo,
  cargarExtras: async () => ({ especialidades: await api("/especialidades"), servicios: await api("/servicios") }),
  textoFila: (f) => `${f.titulo} ${f.descripcion}`,
  columnas: [{ titulo: "Requisito", render: (f) => h("div", {}, h("strong", {}, f.titulo), h("div", { class: "small text-body-secondary" }, f.descripcion)) },
             { titulo: "Aplica a", render: (f, c) => f.servicio_id ? insignia(`Servicio: ${nombreDe(c.servicios, f.servicio_id)}`, "info")
                 : f.especialidad_id ? insignia(nombreDe(c.especialidades, f.especialidad_id), "primary") : insignia("General", "dark") }],
  campos: (c) => [{ name: "especialidad_id", label: "Especialidad (opcional)", type: "select", numero: true, soloCrear: true, options: opciones(c.especialidades) },
                  { name: "servicio_id", label: "Servicio (opcional)", type: "select", numero: true, soloCrear: true, options: opciones(c.servicios) },
                  { name: "titulo", label: "Título", required: true, max: 200 }, { name: "descripcion", label: "Descripción", type: "textarea", required: true },
                  { name: "orden", label: "Orden de aparición", type: "number", min: 0, max: 999 },
                  { name: "activo", label: "Requisito activo", type: "checkbox", defecto: true }],
})(ctx);

export const profesionales = (ctx) => vistaCrud({
  titulo: "Profesionales", descripcion: "Personal de salud que atiende citas. Los horarios se configuran en la agenda.", ruta: "/profesionales", campoActivo: "activo",
  nuevoTexto: "Nuevo profesional", puedeGestionar: gestiona(ctx), nombreFila: (f) => f.nombre_completo,
  cargarExtras: async () => ({ especialidades: await api("/especialidades") }),
  textoFila: (f) => `${f.nombre_completo} ${f.registro_profesional ?? ""} ${f.especialidades.map((e) => e.nombre).join(" ")}`,
  columnas: [{ titulo: "Nombre", render: (f) => h("strong", {}, f.nombre_completo) }, { titulo: "Registro", render: (f) => f.registro_profesional ?? "—" },
             { titulo: "Especialidades", render: (f) => h("div", { class: "d-flex flex-wrap gap-1" }, f.especialidades.map((e) => insignia(e.nombre, "primary"))) }],
  campos: (c) => [{ name: "nombre_completo", label: "Nombre completo", required: true, max: 200 }, { name: "registro_profesional", label: "Registro profesional", max: 50 },
                  { name: "especialidad_ids", label: "Especialidades", type: "multiselect", required: true, options: opciones(c.especialidades.filter((e) => e.activa)) },
                  { name: "activo", label: "Profesional activo", type: "checkbox", defecto: true }],
  // el formulario edita especialidad_ids; la API devuelve especialidades[]
  normalizar: (f) => ({ ...f, especialidad_ids: f.especialidades.map((e) => e.id) }),
})(ctx);
