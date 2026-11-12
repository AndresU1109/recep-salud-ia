import { api, sesion } from "./api.js";
import { aviso, h } from "./ui.js";
import { abrirAyuda, abrirRecorrido, saludo } from "./ayuda.js";
import { iniciarDecorado } from "./decorar.js";
import { icono, logo } from "./iconos.js";
import { botonTema } from "./tema.js";
import { inicio } from "./vistas/inicio.js";
import { consultorios, especialidades, profesionales, requisitos, sedes, servicios } from "./vistas/catalogo.js";
import { agendaDia, bloqueos, horarios } from "./vistas/agenda.js";
import { asistente } from "./vistas/asistente.js";
import { auditoria } from "./vistas/auditoria.js";
import { citas, inasistencias, listaEspera } from "./vistas/citas.js";
import { configIA } from "./vistas/config_ia.js";
import { documentos, preguntasFrecuentes } from "./vistas/conocimiento.js";
import { notificaciones } from "./vistas/notificaciones.js";
import { pacientes } from "./vistas/pacientes.js";
import { politicas } from "./vistas/politicas.js";
import { reportes } from "./vistas/reportes.js";
import { probarBusqueda } from "./vistas/rag.js";
import { ROLES, usuarios } from "./vistas/usuarios.js";
import { voz } from "./vistas/voz.js";

// Cada módulo nuevo (pacientes, agenda, RAG…) se agrega aquí cuando existe su API. El icono de cada ruta se llama igual que ella (iconos.js).
const RUTAS = [
  { ruta: "inicio", titulo: "Inicio", vista: inicio },
  { ruta: "voz", titulo: "Atención por voz", grupo: "Atención", permiso: "agente:atender", vista: voz },
  { ruta: "citas", titulo: "Citas", grupo: "Atención", permiso: "citas:ver", vista: citas },
  { ruta: "agenda", titulo: "Agenda del día", grupo: "Atención", permiso: "agenda:ver", vista: agendaDia },
  { ruta: "espera", titulo: "Lista de espera", grupo: "Atención", permiso: "citas:gestionar", vista: listaEspera },
  { ruta: "inasistencias", titulo: "Inasistencias", grupo: "Atención", permiso: "inasistencias:verificar", vista: inasistencias },
  { ruta: "notificaciones", titulo: "Notificaciones", grupo: "Atención", permiso: "citas:ver", vista: notificaciones },
  { ruta: "pacientes", titulo: "Pacientes", grupo: "Atención", permiso: "pacientes:ver", vista: pacientes },
  { ruta: "horarios", titulo: "Horarios", grupo: "Agenda", permiso: "agenda:ver", vista: horarios },
  { ruta: "bloqueos", titulo: "Bloqueos", grupo: "Agenda", permiso: "agenda:ver", vista: bloqueos },
  { ruta: "profesionales", titulo: "Profesionales", grupo: "Catálogo", permiso: "agenda:ver", vista: profesionales },
  { ruta: "especialidades", titulo: "Especialidades", grupo: "Catálogo", permiso: "agenda:ver", vista: especialidades },
  { ruta: "servicios", titulo: "Servicios", grupo: "Catálogo", permiso: "agenda:ver", vista: servicios },
  { ruta: "requisitos", titulo: "Requisitos", grupo: "Catálogo", permiso: "agenda:ver", vista: requisitos },
  { ruta: "sedes", titulo: "Sedes", grupo: "Catálogo", permiso: "agenda:ver", vista: sedes },
  { ruta: "consultorios", titulo: "Consultorios", grupo: "Catálogo", permiso: "agenda:ver", vista: consultorios },
  { ruta: "documentos", titulo: "Documentos", grupo: "Conocimiento", permiso: "documentos:gestionar", vista: documentos },
  { ruta: "faqs", titulo: "Preguntas frecuentes", grupo: "Conocimiento", permiso: "documentos:gestionar", vista: preguntasFrecuentes },
  { ruta: "buscar", titulo: "Probar búsqueda", grupo: "Conocimiento", permiso: "documentos:gestionar", vista: probarBusqueda },
  { ruta: "asistente", titulo: "Probar asistente", grupo: "Conocimiento", permiso: "agente:probar", vista: asistente },
  { ruta: "reportes", titulo: "Reportes", grupo: "Administración", permiso: "reportes:ver", vista: reportes },
  { ruta: "auditoria", titulo: "Auditoría", grupo: "Administración", permiso: "auditoria:ver", vista: auditoria },
  { ruta: "politicas", titulo: "Políticas", grupo: "Administración", permiso: "citas:ver", vista: politicas },
  { ruta: "config-ia", titulo: "Configuración de IA", grupo: "Administración", permiso: "config_ia:gestionar", vista: configIA },
  { ruta: "usuarios", titulo: "Usuarios y roles", grupo: "Administración", permiso: "usuarios:gestionar", vista: usuarios },
];

const raiz = document.getElementById("app");

let usuario = null;
let config = { zona: "America/Bogota", institucion: "" };  // se completa al iniciar sesión

const recordar = (clave, valor) => { try { if (valor === undefined) return localStorage.getItem(clave); localStorage.setItem(clave, valor); return valor; } catch { return null; } };

// ---------- pantallas de acceso ----------
function marca() {
  return h("span", { class: "marca" }, logo(38), h("span", { class: "marca-nombre" }, "RECEP-SALUD", h("em", {}, "IA")));
}

function campoConIcono(nombreIcono, input, extra = null) {
  return h("div", { class: "campo-icono" }, h("span", { class: "campo-icono-simbolo" }, icono(nombreIcono, 18)), input, extra);
}

function verClave(input) {
  const b = h("button", { class: "campo-ojo", type: "button", "aria-label": "Mostrar la contraseña", onClick: () => {
    const oculto = input.type === "password";
    input.type = oculto ? "text" : "password";
    b.replaceChildren(icono(oculto ? "ver" : "candado", 18));
    b.setAttribute("aria-label", oculto ? "Ocultar la contraseña" : "Mostrar la contraseña");
  } }, icono("candado", 18));
  return b;
}

const BENEFICIOS = [
  ["citas", "Agenda sin choques", "Ningún horario se reserva dos veces."],
  ["asistente", "Un asistente que te escucha", "Atiende por texto y por voz, y sabe cuándo pasar a una persona."],
  ["politicas", "Datos bajo llave", "Documentos cifrados y cada acción queda registrada."],
];

function pantallaAcceso(titulo, subtitulo, ...contenido) {
  const lista = BENEFICIOS.map(([ic, t, d], k) => {
    const li = h("li", { class: "beneficio" }, h("span", { class: "beneficio-icono" }, icono(ic, 22)), h("div", {}, h("strong", {}, t), h("span", {}, d)));
    li.style.setProperty("--i", k);
    return li;
  });
  raiz.replaceChildren(h("main", { class: "acceso" },
    h("section", { class: "acceso-lado" },
      h("span", { class: "hoja hoja-1" }), h("span", { class: "hoja hoja-2" }), h("span", { class: "hoja hoja-3" }),
      marca(),
      h("h1", { class: "acceso-titulo" }, "La recepción que ", h("em", {}, "nunca"), " se cansa de escuchar."),
      h("p", { class: "acceso-texto" }, "Citas, pacientes y respuestas claras en un solo lugar, para que tu equipo dedique el tiempo a las personas."),
      h("ul", { class: "beneficios" }, lista)),
    h("section", { class: "acceso-form" },
      h("div", { class: "acceso-tema" }, botonTema()),
      h("div", { class: "acceso-tarjeta" }, h("h2", { class: "acceso-saludo" }, titulo), h("p", { class: "acceso-sub" }, subtitulo), contenido))));
}

function pantallaLogin(mensaje) {
  const error = h("div", { class: mensaje ? "alert alert-info" : "alert alert-danger d-none", role: "alert" }, mensaje ?? "");
  const email = h("input", { class: "form-control", id: "email", type: "email", required: true, autocomplete: "username", placeholder: "tucorreo@institucion.co" });
  const clave = h("input", { class: "form-control", id: "clave", type: "password", required: true, autocomplete: "current-password", placeholder: "Tu contraseña" });
  const boton = h("button", { class: "btn btn-primary btn-lg w-100 mt-2", type: "submit" }, "Entrar");
  const form = h("form", { onSubmit: async (e) => {
    e.preventDefault(); boton.disabled = true; boton.classList.add("cargando"); error.classList.add("d-none");
    try {
      const r = await api("/auth/login", { metodo: "POST", autenticado: false, cuerpo: { email: email.value, clave: clave.value } });
      sesion.guardar(r.access_token);
      await arrancar();
    } catch (err) {
      error.className = "alert alert-danger sacudir"; error.textContent = err.message; clave.value = ""; clave.focus();
    } finally { boton.disabled = false; boton.classList.remove("cargando"); }
  } },
  error,
  h("div", { class: "mb-3" }, h("label", { class: "form-label", for: "email" }, "Correo electrónico"), campoConIcono("correo", email)),
  h("div", { class: "mb-2" }, h("label", { class: "form-label", for: "clave" }, "Contraseña"), campoConIcono("candado", clave, verClave(clave))), boton,
  h("p", { class: "acceso-ayuda" }, "¿Problemas para entrar? Habla con la persona que administra el sistema."));
  pantallaAcceso(`${saludo()}, te damos la bienvenida`, "Entra para continuar con tu jornada.", form);
  email.focus();
}

function pantallaCambioClave() {
  const error = h("div", { class: "alert alert-danger d-none", role: "alert" });
  const campo = (id, etiqueta, auto) => ({ id, input: h("input", { class: "form-control", id, type: "password", required: true, autocomplete: auto }), etiqueta });
  const campos = [campo("actual", "Contraseña actual (la temporal)", "current-password"), campo("nueva", "Nueva contraseña", "new-password"), campo("repetir", "Repite la nueva contraseña", "new-password")];
  const [actual, nueva, repetir] = campos.map((c) => c.input);
  const form = h("form", { onSubmit: async (e) => {
    e.preventDefault(); error.classList.add("d-none");
    if (nueva.value !== repetir.value) { error.textContent = "Las contraseñas nuevas no coinciden."; error.classList.remove("d-none"); return; }
    try {
      await api("/auth/cambiar-clave", { metodo: "POST", cuerpo: { clave_actual: actual.value, clave_nueva: nueva.value } });
      sesion.borrar(); pantallaLogin("Contraseña actualizada. Inicia sesión con la nueva.");
    } catch (err) { error.textContent = err.message; error.classList.remove("d-none"); }
  } }, error,
  campos.map((c) => h("div", { class: "mb-3" }, h("label", { class: "form-label", for: c.id }, c.etiqueta), campoConIcono("candado", c.input))),
  h("p", { class: "form-text" }, "Mínimo 12 caracteres, con mayúsculas, minúsculas y números."),
  h("button", { class: "btn btn-primary btn-lg w-100", type: "submit" }, "Cambiar contraseña"));
  pantallaAcceso("Elige tu propia contraseña", `Hola, ${usuario.nombre}. Por seguridad, la temporal solo sirve para este primer paso.`, form);
  actual.focus();
}

// ---------- panel ----------
function permitido(r) { return !r.permiso || usuario.permisos.includes(r.permiso); }

const rutaActual = () => RUTAS.find((r) => r.ruta === (location.hash.replace(/^#\//, "") || "inicio")) ?? RUTAS[0];
const iniciales = (nombre) => nombre.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("");
const claveTour = () => `recep_tour_${usuario.email}`;

function empezarRecorrido() { abrirRecorrido(usuario.nombre.split(" ")[0], () => recordar(claveTour(), "1")); }

function pantallaPanel() {
  const grupos = [...new Set(RUTAS.filter(permitido).map((r) => r.grupo ?? ""))];
  const nav = h("nav", { class: "nav flex-column", "aria-label": "Principal" }, grupos.map((g) => [
    g ? h("div", { class: "nav-grupo" }, g) : null,
    RUTAS.filter((r) => permitido(r) && (r.grupo ?? "") === g).map((r) =>
      // data-bs-dismiss cancelaría la navegación del enlace; el menú móvil se cierra a mano
      h("a", { class: "nav-link", href: `#/${r.ruta}`, "data-ruta": r.ruta, onClick: () => bootstrap.Offcanvas.getInstance(document.getElementById("menu"))?.hide() }, icono(r.ruta, 20), h("span", {}, r.titulo))),
  ]));
  raiz.replaceChildren(
    h("header", { class: "barra-superior" },
      h("button", { class: "boton-icono d-lg-none", type: "button", "data-bs-toggle": "offcanvas", "data-bs-target": "#menu", "aria-controls": "menu", "aria-label": "Abrir menú" }, icono("menu", 22)),
      marca(), h("span", { class: "flex-grow-1" }),
      h("button", { class: "boton-icono", type: "button", "aria-label": "Ayuda de esta pantalla", title: "Ayuda de esta pantalla", onClick: () => abrirAyuda(rutaActual().ruta, rutaActual().titulo, empezarRecorrido) }, icono("ayuda", 22)),
      botonTema(),
      h("span", { class: "usuario-chip" }, h("span", { class: "avatar", "aria-hidden": "true" }, iniciales(usuario.nombre)),
        h("span", { class: "usuario-datos d-none d-md-flex" }, h("strong", {}, usuario.nombre), h("small", {}, ROLES[usuario.rol] ?? usuario.rol))),
      h("button", { class: "boton-icono", type: "button", "aria-label": "Salir", title: "Salir", onClick: cerrarSesion }, icono("salir", 22))),
    h("div", { class: "panel" },
      h("aside", { id: "menu", class: "offcanvas-lg offcanvas-start barra-lateral", tabindex: "-1", "aria-label": "Menú" },
        h("div", { class: "offcanvas-header d-lg-none" }, marca(), h("button", { class: "btn-close", type: "button", "data-bs-dismiss": "offcanvas", "aria-label": "Cerrar" })),
        h("div", { class: "offcanvas-body d-block p-3" }, nav)),
      h("main", { id: "contenido", class: "contenido", tabindex: "-1" })));
  navegar();
  if (!recordar(claveTour())) setTimeout(empezarRecorrido, 700);
}

function cargando() {
  return h("div", { class: "cargando-vista", role: "status" }, logo(54), h("span", {}, "Preparando tu pantalla…"));
}

async function navegar() {
  const contenido = document.getElementById("contenido");
  if (!contenido) return;
  const ruta = rutaActual();
  document.querySelectorAll("[data-ruta]").forEach((a) => {
    const activo = a.dataset.ruta === ruta.ruta;
    a.classList.toggle("active", activo);
    if (activo) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  if (!permitido(ruta)) { contenido.replaceChildren(h("div", { class: "alert alert-warning" }, "No tienes permiso para ver esta sección.")); return; }
  contenido.replaceChildren(cargando());
  try {
    const vista = await ruta.vista({ usuario, config });
    contenido.replaceChildren(vista);
    vista.classList.add("vista-entra");
    [...vista.querySelectorAll(":scope > *, :scope .card, :scope .tarjeta-dato")].slice(0, 14).forEach((el, k) => el.style.setProperty("--i", k)); // entran escalonadas
    contenido.focus({ preventScroll: true });
  } catch (e) { if (e.status !== 401) contenido.replaceChildren(h("div", { class: "alert alert-danger" }, e.message)); }
  document.title = `${ruta.titulo} · RECEP-SALUD IA`;
}

function cerrarSesion() { sesion.borrar(); usuario = null; location.hash = ""; pantallaLogin(); }

async function arrancar() {
  if (!sesion.token) return pantallaLogin();
  try { usuario = await api("/auth/me"); } catch { sesion.borrar(); return pantallaLogin(); }
  if (usuario.debe_cambiar_clave) return pantallaCambioClave();
  try { const r = await api("/panel/resumen"); config = { zona: r.zona_horaria, institucion: r.institucion }; } catch { /* se usa la zona por defecto */ }
  pantallaPanel();
}

window.addEventListener("hashchange", navegar);
document.addEventListener("sesion-expirada", (e) => { sesion.borrar(); usuario = null; pantallaLogin(e.detail || "Tu sesión terminó. Inicia sesión de nuevo."); });
iniciarDecorado();
arrancar().catch((e) => aviso(e.message, "danger"));
