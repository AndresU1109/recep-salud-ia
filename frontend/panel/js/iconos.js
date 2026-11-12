// Iconos propios (SVG de trazo, 24×24). Se dibujan con nodos del DOM —nunca con HTML armado— y toman el color del texto (currentColor),
// así cambian solos con el modo día/noche. Cada icono es una lista de formas: "c:cx,cy,r" círculo · "r:x,y,ancho,alto,radio" rectángulo · otro texto = trazo.
const SVG = "http://www.w3.org/2000/svg";

const ICONOS = {
  inicio: ["M3 11.5L12 4l9 7.5", "M5.5 10v9.5h13V10", "M10 19.5v-5h4v5"],
  citas: ["r:3,5,18,16,3.5", "M8 3v4", "M16 3v4", "M3 10h18", "M9 15.2l2 2 4-4"],
  agenda: ["c:12,12,9", "M12 7v5l3.2 2"],
  espera: ["M7 3h10", "M7 21h10", "M8 3c0 5 4 6 4 9s-4 4-4 9", "M16 3c0 5-4 6-4 9s4 4 4 9"],
  inasistencias: ["c:9,8,3.5", "M2.5 20c0-3.6 2.8-6 6.5-6 1.6 0 3 .4 4 1.1", "M16.5 15.5l5 5", "M21.5 15.5l-5 5"],
  pacientes: ["c:9,8,3.5", "M2.5 20c0-3.6 2.8-6 6.5-6s6.5 2.4 6.5 6", "M16 4.6a3.5 3.5 0 010 6.8", "M18 14.4c2 .8 3.5 2.6 3.5 5.6"],
  notificaciones: ["M6 9.5a6 6 0 1112 0c0 5.5 2.3 7 2.3 7H3.7S6 15 6 9.5z", "M10 20a2 2 0 004 0"],
  horarios: ["c:12,12,9", "M12 7v5l-3.5 2.2", "M3 3l2.5 2.5"],
  bloqueos: ["c:12,12,9", "M5.6 5.6l12.8 12.8"],
  profesionales: ["M6 3v6a4 4 0 008 0V3", "M10 13v2.5a4.5 4.5 0 009 0V13", "c:19,11,2"],
  especialidades: ["M12 20.5S3.5 15 3.5 9.3A4.6 4.6 0 0112 7a4.6 4.6 0 018.5 2.3C20.5 15 12 20.5 12 20.5z", "M6.5 12.5H9l1.4-2.6 2.6 5 1.4-2.4h3.1"],
  servicios: ["M12 3l9 5-9 5-9-5 9-5z", "M3 12.5l9 5 9-5", "M3 16.8l9 5 9-5"],
  requisitos: ["M9.5 6.5H20", "M9.5 12H20", "M9.5 17.5H20", "M3.8 6.4l1.2 1.2 2.2-2.4", "M3.8 11.9l1.2 1.2 2.2-2.4", "M3.8 17.4l1.2 1.2 2.2-2.4"],
  sedes: ["r:5,3,14,18,2", "M9 8h2", "M13 8h2", "M9 12h2", "M13 12h2", "M10 21v-4h4v4"],
  consultorios: ["M6 21V4.5A1.5 1.5 0 017.5 3h9A1.5 1.5 0 0118 4.5V21", "M3.5 21h17", "c:14.6,12.5,.6"],
  documentos: ["M14 3H7.5A2.5 2.5 0 005 5.5v13A2.5 2.5 0 007.5 21h9a2.5 2.5 0 002.5-2.5V8z", "M14 3v5h5", "M9 13h6", "M9 17h4"],
  faqs: ["c:12,12,9", "M9.5 9.6a2.6 2.6 0 114.2 2c-.9.7-1.7 1.2-1.7 2.4", "M12 17.2h.01"],
  buscar: ["c:11,11,6.5", "M20.5 20.5L16 16"],
  asistente: ["M21 11.8a8.3 8.3 0 01-12 7.4L3.8 20.5l1.4-4.4A8.3 8.3 0 1121 11.8z", "M8.5 11.8h.01", "M12 11.8h.01", "M15.5 11.8h.01"],
  politicas: ["M12 3l8 3v5.5c0 5-3.3 8.3-8 9.5-4.7-1.2-8-4.5-8-9.5V6z", "M8.6 12l2.5 2.5 4.5-5"],
  reportes: ["M4 20.5h16", "r:5,12,3.2,8.5,1", "r:10.4,6,3.2,14.5,1", "r:15.8,9.5,3.2,11,1"],
  auditoria: ["M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z", "c:12,12,3"],
  "config-ia": ["M12 3l1.9 5 5.1 1.9-5.1 1.9L12 17l-1.9-5.2L5 9.9 10.1 8z", "M19 15.5l.8 2.1 2.1.8-2.1.8-.8 2.1-.8-2.1-2.1-.8 2.1-.8z"],
  usuarios: ["c:8,15,4", "M10.9 12.1L20.5 3", "M16.5 7l3 3", "M13.8 9.8l2 2"],
  sol: ["c:12,12,4", "M12 2.5v2.2", "M12 19.3v2.2", "M2.5 12h2.2", "M19.3 12h2.2", "M5.3 5.3l1.6 1.6", "M17.1 17.1l1.6 1.6", "M5.3 18.7l1.6-1.6", "M17.1 6.9l1.6-1.6"],
  luna: ["M20.5 14.6A8.5 8.5 0 019.4 3.5a8.5 8.5 0 1011.1 11.1z"],
  menu: ["M4 7h16", "M4 12h16", "M4 17h16"],
  salir: ["M10 4H5.5A1.5 1.5 0 004 5.5v13A1.5 1.5 0 005.5 20H10", "M15.5 7.5L20 12l-4.5 4.5", "M20 12H9.5"],
  ayuda: ["c:12,12,9", "M9.5 9.6a2.6 2.6 0 114.2 2c-.9.7-1.7 1.2-1.7 2.4", "M12 17.2h.01"],
  foco: ["M9 18.5h6", "M10 21.5h4", "M12 2.5a6.2 6.2 0 00-3.6 11.2c.7.6 1.1 1.4 1.1 2.3v.5h5v-.5c0-.9.4-1.7 1.1-2.3A6.2 6.2 0 0012 2.5z"],
  brujula: ["c:12,12,9", "M15.6 8.4l-2.1 5.1-5.1 2.1 2.1-5.1z"],
  ok: ["M4.8 12.8l4.6 4.6L19.2 7.4"],
  x: ["M6 6l12 12", "M18 6L6 18"],
  alerta: ["M12 3.6L2.8 19.8h18.4z", "M12 10v4.2", "M12 17.2h.01"],
  info: ["c:12,12,9", "M12 11v5.2", "M12 7.8h.01"],
  peligro: ["c:12,12,9", "M12 7.5v5.3", "M12 16.2h.01"],
  mas: ["M12 5v14", "M5 12h14"],
  descargar: ["M12 4v11", "M7.5 10.8l4.5 4.5 4.5-4.5", "M4.5 20h15"],
  refrescar: ["M20 12a8 8 0 01-14.2 5", "M4 12a8 8 0 0114.2-5", "M18.5 3v4.2h-4.2", "M5.5 21v-4.2h4.2"],
  enviar: ["M21 3L3 10.5l7 2.5 2.5 7z", "M10 13L21 3"],
  lapiz: ["M4 20l.9-4L16.5 4.4a1.8 1.8 0 012.5 0l.6.6a1.8 1.8 0 010 2.5L8 19.1z", "M14.5 6.5l3 3"],
  guardar: ["M5 3.5h11.5L20.5 7.5V19A1.5 1.5 0 0119 20.5H5A1.5 1.5 0 013.5 19V5A1.5 1.5 0 015 3.5z", "M7.5 3.5v5h7v-5", "M7.5 20.5v-6h9v6"],
  ver: ["M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z", "c:12,12,3"],
  basura: ["M4 7h16", "M9.5 7V4.5h5V7", "M6.5 7l.8 12.5h9.4L17.5 7"],
  candado: ["r:4.5,10.5,15,10,2.5", "M8 10.5V8a4 4 0 018 0v2.5"],
  correo: ["r:3,5.5,18,13,2.5", "M3.5 7.5l8.5 6 8.5-6"],
  voz: ["r:9,3,6,11.5,3", "M5.5 11a6.5 6.5 0 0013 0", "M12 17.5V21", "M2.5 9v2", "M21.5 9v2"],
  microfono: ["r:9,3,6,11.5,3", "M5.5 11a6.5 6.5 0 0013 0", "M12 17.5V21"],
  colgar: ["M3.2 14.5c5.2-4.4 12.4-4.4 17.6 0l-1.6 3-3.4-1.2-.6-2.5c-2-.6-3.8-.6-5.8 0l-.6 2.5-3.4 1.2z"],
  parar: ["r:6,6,12,12,2.5"],
  jugar: ["M7.5 4.5l12 7.5-12 7.5z"],
  filtro: ["M3.5 5h17l-6.5 7.8V19l-4 2v-8.2z"],
  bandeja: ["M3.5 13.5l2.4-8A2 2 0 017.8 4h8.4a2 2 0 011.9 1.5l2.4 8", "M3.5 13.5V18A2 2 0 005.5 20h13a2 2 0 002-2v-4.5H15l-1.3 2h-3.4L9 13.5z"],
  flecha: ["M4.5 12h15", "M13.5 6l6 6-6 6"],
  atras: ["M19.5 12h-15", "M10.5 6l-6 6 6 6"],
  estrella: ["M12 3.5l2.6 5.4 5.9.8-4.3 4.1 1 5.9L12 16.9l-5.2 2.8 1-5.9-4.3-4.1 5.9-.8z"],
  corazon: ["M12 20.5S3.5 15 3.5 9.3A4.6 4.6 0 0112 7a4.6 4.6 0 018.5 2.3C20.5 15 12 20.5 12 20.5z"],
  rayo: ["M13.5 2.5L5 13.5h6l-1 8 8.5-11h-6z"],
  cuaderno: ["M6.5 3.5h11A1.5 1.5 0 0119 5v14a1.5 1.5 0 01-1.5 1.5h-11A1.5 1.5 0 015 19V5a1.5 1.5 0 011.5-1.5z", "M9 3.5v17", "M12 8h4", "M12 12h4"],
};

/** icono("citas", 20) → <svg> listo para insertar. Decorativo (aria-hidden): el texto del botón o enlace ya dice qué es. */
export function icono(nombre, tam = 20, clase = "") {
  const formas = ICONOS[nombre] ?? ICONOS.info;
  const svg = document.createElementNS(SVG, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 24 24", width: tam, height: tam, fill: "none", stroke: "currentColor", "stroke-width": 1.8, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", focusable: "false" })) svg.setAttribute(k, v);
  svg.setAttribute("class", `icono ${clase}`.trim());
  for (const f of formas) {
    let el;
    if (f.startsWith("c:")) {
      const [cx, cy, r] = f.slice(2).split(",");
      el = document.createElementNS(SVG, "circle");
      Object.entries({ cx, cy, r }).forEach(([k, v]) => el.setAttribute(k, v));
    } else if (f.startsWith("r:")) {
      const [x, y, width, height, rx] = f.slice(2).split(",");
      el = document.createElementNS(SVG, "rect");
      Object.entries({ x, y, width, height, rx }).forEach(([k, v]) => el.setAttribute(k, v));
    } else {
      el = document.createElementNS(SVG, "path");
      el.setAttribute("d", f);
    }
    svg.append(el);
  }
  return svg;
}

export const hayIcono = (nombre) => nombre in ICONOS;

/** Marca de la casa: cuatro pétalos formando una cruz (salud) sobre un círculo cálido. Se anima con CSS (.logo). */
export function logo(tam = 40) {
  const svg = document.createElementNS(SVG, "svg");
  for (const [k, v] of Object.entries({ viewBox: "0 0 48 48", width: tam, height: tam, "aria-hidden": "true", focusable: "false", class: "logo" })) svg.setAttribute(k, v);
  const base = document.createElementNS(SVG, "circle");
  Object.entries({ cx: 24, cy: 24, r: 22, class: "logo-fondo" }).forEach(([k, v]) => base.setAttribute(k, v));
  svg.append(base);
  const petalos = document.createElementNS(SVG, "g");
  petalos.setAttribute("class", "logo-petalos");
  for (const giro of [0, 90, 180, 270]) {
    const p = document.createElementNS(SVG, "path");
    p.setAttribute("d", "M24 24C24 16 25.5 10.5 24 8.5C22.5 10.5 24 16 24 24z");
    p.setAttribute("transform", `rotate(${giro} 24 24)`);
    petalos.append(p);
  }
  const centro = document.createElementNS(SVG, "circle");
  Object.entries({ cx: 24, cy: 24, r: 3.2, class: "logo-centro" }).forEach(([k, v]) => centro.setAttribute(k, v));
  svg.append(petalos, centro);
  return svg;
}
