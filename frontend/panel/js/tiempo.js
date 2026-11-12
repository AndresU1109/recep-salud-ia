// Fechas y horas en la zona horaria de la institución (no la del navegador).
const partes = (fecha, zona, opciones) => Object.fromEntries(
  new Intl.DateTimeFormat("en-CA", { timeZone: zona, hourCycle: "h23", ...opciones }).formatToParts(fecha).map((p) => [p.type, p.value]));

export function hoyEn(zona) {
  const p = partes(new Date(), zona, { year: "numeric", month: "2-digit", day: "2-digit" });
  return `${p.year}-${p.month}-${p.day}`;
}

/** "2030-03-04T09:00" escrito en la zona de la institución → ISO con su desfase real (p. ej. -05:00). */
export function aIsoEnZona(valor, zona) {
  const supuesto = new Date(`${valor}:00Z`);                       // lo leemos como UTC para tener un instante de referencia
  const p = partes(supuesto, zona, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  const enZona = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  const minutos = Math.round((enZona - supuesto.getTime()) / 60000);  // desfase de la zona en ese instante
  const signo = minutos < 0 ? "-" : "+";
  const abs = Math.abs(minutos);
  return `${valor}:00${signo}${String(Math.floor(abs / 60)).padStart(2, "0")}:${String(abs % 60).padStart(2, "0")}`;
}

export const hora = (iso, zona) => new Date(iso).toLocaleTimeString("es-CO", { timeZone: zona, hour: "2-digit", minute: "2-digit", hour12: false });
export const fechaHora = (iso, zona) => new Date(iso).toLocaleString("es-CO", { timeZone: zona, dateStyle: "medium", timeStyle: "short" });
export const hhmm = (t) => (t ?? "").slice(0, 5);
