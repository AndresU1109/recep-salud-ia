// Parte didáctica: saludo según la hora, ayuda de cada pantalla (panel lateral) y recorrido de bienvenida paso a paso.
import { h } from "./ui.js";
import { icono, logo } from "./iconos.js";

export function saludo(ahora = new Date()) {
  const hora = Number(new Intl.DateTimeFormat("es-CO", { hour: "numeric", hourCycle: "h23", timeZone: "America/Bogota" }).format(ahora));
  return hora < 5 ? "Buenas noches" : hora < 12 ? "Buenos días" : hora < 19 ? "Buenas tardes" : "Buenas noches";
}

export const fechaLarga = (ahora = new Date(), zona = "America/Bogota") => {
  const t = new Intl.DateTimeFormat("es-CO", { dateStyle: "full", timeZone: zona }).format(ahora);
  return t.charAt(0).toUpperCase() + t.slice(1);
};

/** Qué hace cada pantalla, en palabras simples. «pasos» es lo que normalmente se hace ahí; «ojo» una advertencia útil. */
export const AYUDA = {
  inicio: { icono: "inicio", que: "Tu punto de partida: cifras del día y los primeros pasos para dejar el sistema listo.", pasos: ["Mira las cifras para saber cómo va la jornada.", "Sigue la lista «Primeros pasos»: cada tarea te lleva a su pantalla."] },
  voz: { icono: "voz", que: "Atiende a un paciente solo con la voz: el paciente habla y el asistente le responde en voz alta, pide sus datos, ofrece horarios, especialistas y sedes, y agenda la cita.", pasos: ["Pulse «Iniciar conversación» y permita el micrófono.", "El paciente habla con normalidad: no hay que pulsar nada.", "Lo que el asistente dice aparece en pantalla como subtítulo, con ejemplos de qué responder."], ojo: "Active «conversación real» solo con un paciente de verdad; apagado es modo prueba y no cuenta en los reportes. Las respuestas se dicen en voz alta: cuide la privacidad del lugar." },
  citas: { icono: "citas", que: "Aquí se agendan, confirman, reprograman y cancelan las citas.", pasos: ["Busca al paciente o regístralo.", "Elige profesional y un horario libre.", "Confirma: la cita queda con un código de reserva."], ojo: "Cada cambio queda en el historial y, si el correo está configurado, el paciente recibe un aviso." },
  agenda: { icono: "agenda", que: "La agenda de un día: quién atiende, qué horarios están libres, ocupados o bloqueados.", pasos: ["Elige el día y el profesional.", "Los colores te dicen el estado de cada cupo."] },
  espera: { icono: "espera", que: "Personas que esperan un cupo. Si se cancela una cita parecida, se avisa a las primeras por orden de llegada.", pasos: ["Agrega al paciente con la especialidad y las fechas que le sirven.", "Cuando haya un cupo, asígnaselo desde aquí."], ojo: "El aviso no reserva el cupo: quien responda primero lo ocupa." },
  inasistencias: { icono: "inasistencias", que: "Citas a las que el paciente no llegó.", pasos: ["Revisa cada caso.", "Verifica o descarta: solo las verificadas cuentan."], ojo: "Nada se sanciona solo; una persona autorizada decide." },
  pacientes: { icono: "pacientes", que: "Registro y búsqueda de pacientes.", pasos: ["Registra con la autorización de datos (obligatoria).", "Busca por nombre o documento."], ojo: "El documento se guarda cifrado y se muestra enmascarado; verlo completo queda auditado." },
  notificaciones: { icono: "notificaciones", que: "Los avisos por correo a pacientes: confirmaciones, cambios, recordatorios y cupos.", pasos: ["Revisa que el correo esté configurado.", "Reintenta los que fallaron."], ojo: "Sin servidor de correo, los avisos quedan «omitidos»: nadie los recibe." },
  horarios: { icono: "horarios", que: "Cuándo atiende cada profesional. De aquí salen los cupos.", pasos: ["Elige profesional, día y franja.", "Define cuánto dura cada cita."] },
  bloqueos: { icono: "bloqueos", que: "Ausencias, festivos o mantenimientos que cierran cupos.", pasos: ["Marca el rango y el motivo.", "El sistema te avisa qué citas quedan afectadas."] },
  profesionales: { icono: "profesionales", que: "Las personas que atienden, con sus especialidades.", pasos: ["Crea al profesional.", "Asígnale especialidades."] },
  especialidades: { icono: "especialidades", que: "Las áreas de atención que ofrece la institución.", pasos: ["Crea la especialidad.", "Luego vincúlala a profesionales."] },
  servicios: { icono: "servicios", que: "Los servicios concretos dentro de cada especialidad.", pasos: ["Define nombre y duración."] },
  requisitos: { icono: "requisitos", que: "Qué debe llevar o saber el paciente. El asistente los usa al responder.", pasos: ["Escribe cada requisito en lenguaje claro."], ojo: "Escribe solo lo que la institución realmente exige." },
  sedes: { icono: "sedes", que: "Las sedes físicas de la institución.", pasos: ["Crea la sede.", "Agrega sus consultorios."] },
  consultorios: { icono: "consultorios", que: "Los espacios de atención de cada sede.", pasos: ["Elige la sede.", "Crea el consultorio."] },
  documentos: { icono: "documentos", que: "La base de conocimiento: de aquí el asistente saca respuestas con fuente.", pasos: ["Sube un PDF, DOCX, TXT o MD.", "Revisa los datos y actívalo.", "Prueba en «Probar búsqueda»."], ojo: "El asistente solo cita documentos activos y vigentes." },
  faqs: { icono: "faqs", que: "Preguntas frecuentes con su respuesta oficial.", pasos: ["Escribe la pregunta como la diría un paciente.", "Escribe la respuesta aprobada."] },
  buscar: { icono: "buscar", que: "Prueba qué encuentra el buscador con una pregunta.", pasos: ["Escribe una pregunta.", "Mira los fragmentos y su puntaje."] },
  asistente: { icono: "asistente", que: "Conversa con el asistente como lo haría un paciente, por texto o por voz.", pasos: ["Escribe o pulsa «Iniciar llamada de voz».", "Debajo de cada respuesta ves qué herramientas y fuentes usó."], ojo: "Es modo prueba: estas conversaciones no cuentan en los reportes." },
  politicas: { icono: "politicas", que: "Las reglas de la institución: cancelaciones, reprogramaciones e inasistencias.", pasos: ["Define solo lo que la institución decidió.", "Lo que no esté aquí, el asistente no lo inventa."] },
  reportes: { icono: "reportes", que: "Cifras agregadas para decidir y rendir cuentas.", pasos: ["Elige reporte y fechas.", "Descarga en Excel o PDF."], ojo: "Ningún reporte trae datos de pacientes. «N/D» significa que no se puede calcular." },
  auditoria: { icono: "auditoria", que: "Quién hizo qué y cuándo. No se puede editar ni borrar.", pasos: ["Filtra por acción, persona o fecha.", "Exporta si lo necesitas."] },
  "config-ia": { icono: "config-ia", que: "Cómo habla y se comporta el asistente.", pasos: ["Cambia nombre y saludo.", "Prueba la conexión con el modelo."] },
  usuarios: { icono: "usuarios", que: "Quién entra al panel y qué puede hacer según su rol.", pasos: ["Crea al usuario con una contraseña temporal.", "Elige su rol: define sus permisos."], ojo: "Dale a cada persona solo el rol que necesita." },
};

function plantillaPanel(titulo, cuerpo) {
  const raiz = h("div", { class: "offcanvas offcanvas-end panel-ayuda", tabindex: "-1", "aria-labelledby": "titulo-ayuda" },
    h("div", { class: "offcanvas-header" }, h("h2", { class: "offcanvas-title", id: "titulo-ayuda" }, icono("foco", 22), titulo), h("button", { type: "button", class: "btn-close", "data-bs-dismiss": "offcanvas", "aria-label": "Cerrar" })),
    h("div", { class: "offcanvas-body" }, cuerpo));
  document.body.append(raiz);
  const panel = new bootstrap.Offcanvas(raiz);
  raiz.addEventListener("hidden.bs.offcanvas", () => { panel.dispose(); raiz.remove(); });
  panel.show();
}

export function abrirAyuda(ruta, titulo, alRepetirRecorrido) {
  const a = AYUDA[ruta] ?? { que: "Esta pantalla no tiene ayuda escrita todavía.", pasos: [] };
  plantillaPanel(`Ayuda: ${titulo}`, [
    h("p", { class: "ayuda-que" }, a.que),
    a.pasos.length ? h("div", {}, h("h3", { class: "ayuda-sub" }, "Cómo se usa"), h("ol", { class: "ayuda-pasos" }, a.pasos.map((p) => h("li", {}, p)))) : null,
    a.ojo ? h("div", { class: "ayuda-ojo" }, icono("alerta", 18), h("span", {}, a.ojo)) : null,
    h("hr"),
    h("button", { class: "btn btn-outline-primary w-100", type: "button", "data-bs-dismiss": "offcanvas", onClick: () => setTimeout(alRepetirRecorrido, 350) }, icono("brujula", 18), "Repetir el recorrido de bienvenida")]);
}

const PASOS = (nombre) => [
  { icono: "corazon", titulo: `¡Bienvenido/a, ${nombre}!`, texto: "Esta es la recepción inteligente de la institución. Desde aquí organizas citas, pacientes y conocimiento, y revisas cómo se comporta el asistente que atiende por texto y por voz." },
  { icono: "menu", titulo: "El menú, a tu izquierda", texto: "Las secciones están agrupadas: Atención (el día a día), Agenda, Catálogo, Conocimiento y Administración. Solo ves lo que tu rol permite. En el celular, el botón de las tres rayitas lo abre." },
  { icono: "asistente", titulo: "Prueba al asistente", texto: "En «Probar asistente» conversas con él como un paciente, escribiendo o hablando. Debajo de cada respuesta ves qué hizo: qué herramientas usó y en qué documentos se apoyó." },
  { icono: "luna", titulo: "Día o noche, como prefieras", texto: "El botón del sol y la luna, arriba, cambia el modo. Si trabajas de noche o con poca luz, el modo noche cansa menos la vista. Recuerda tu elección." },
  { icono: "foco", titulo: "¿Dudas? Hay ayuda en cada pantalla", texto: "El botón de ayuda, arriba, explica qué hace la pantalla en la que estás y cómo se usa. Puedes repetir este recorrido cuando quieras desde ahí." },
];

/** Recorrido de bienvenida: una tarjeta por paso, con puntos de avance. */
export function abrirRecorrido(nombre, alTerminar = () => {}) {
  const pasos = PASOS(nombre);
  let i = 0;
  const icon = h("div", { class: "tour-icono" });
  const titulo = h("h2", { class: "tour-titulo" });
  const texto = h("p", { class: "tour-texto" });
  const puntos = h("div", { class: "tour-puntos", "aria-hidden": "true" }, pasos.map(() => h("span", { class: "tour-punto" })));
  const anterior = h("button", { class: "btn btn-outline-secondary", type: "button" }, "Anterior");
  const siguiente = h("button", { class: "btn btn-primary", type: "button" });
  const saltar = h("button", { class: "btn btn-link tour-saltar", type: "button", "data-bs-dismiss": "modal" }, "Saltar recorrido");
  const tarjeta = h("div", { class: "tour-tarjeta" }, icon, titulo, texto);
  const raiz = h("div", { class: "modal fade tour", tabindex: "-1", "aria-labelledby": "tour-titulo" },
    h("div", { class: "modal-dialog modal-dialog-centered" }, h("div", { class: "modal-content" },
      h("div", { class: "tour-cabecera" }, logo(44), h("span", { class: "tour-paso" }), saltar),
      h("div", { class: "modal-body" }, tarjeta),
      h("div", { class: "tour-pie" }, puntos, h("div", { class: "d-flex gap-2" }, anterior, siguiente)))));
  titulo.id = "tour-titulo";
  document.body.append(raiz);
  const modal = new bootstrap.Modal(raiz);
  raiz.addEventListener("hidden.bs.modal", () => { modal.dispose(); raiz.remove(); alTerminar(); });

  function pintar() {
    const p = pasos[i];
    tarjeta.classList.remove("tour-entra"); void tarjeta.offsetWidth; tarjeta.classList.add("tour-entra"); // reinicia la animación
    icon.replaceChildren(icono(p.icono, 34));
    titulo.textContent = p.titulo; texto.textContent = p.texto;
    raiz.querySelector(".tour-paso").textContent = `Paso ${i + 1} de ${pasos.length}`;
    [...puntos.children].forEach((d, k) => { d.classList.toggle("activo", k === i); d.classList.toggle("hecho", k < i); });
    anterior.disabled = i === 0;
    siguiente.replaceChildren(i === pasos.length - 1 ? "Empezar" : "Siguiente", icono(i === pasos.length - 1 ? "ok" : "flecha", 18));
  }
  anterior.addEventListener("click", () => { if (i > 0) { i--; pintar(); } });
  siguiente.addEventListener("click", () => { if (i < pasos.length - 1) { i++; pintar(); } else modal.hide(); });
  pintar();
  modal.show();
}
