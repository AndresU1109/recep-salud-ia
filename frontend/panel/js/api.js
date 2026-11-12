// Cliente de la API. El token vive solo en sessionStorage (se borra al cerrar la pestaña).
const CLAVE = "recep_token";

export const sesion = {
  get token() { try { return sessionStorage.getItem(CLAVE); } catch { return null; } },
  guardar(t) { try { sessionStorage.setItem(CLAVE, t); } catch { /* sin almacenamiento: la sesión dura lo que dure la página */ } },
  borrar() { try { sessionStorage.removeItem(CLAVE); } catch { /* nada */ } },
};

export class ApiError extends Error {
  constructor(status, error) {
    const detalle = (error.campos || []).map((c) => `${c.campo}: ${c.motivo}`).join("; ");
    super(detalle ? `${error.mensaje} ${detalle}` : error.mensaje);
    this.status = status;
    this.codigo = error.codigo;
  }
}

/** Descarga un archivo protegido (necesita el token, por eso no es un simple enlace). */
export async function descargar(ruta, nombre) {
  const r = await fetch(`/api/v1${ruta}`, { headers: { Authorization: `Bearer ${sesion.token}` } });
  if (!r.ok) { const d = await r.json().catch(() => null); throw new ApiError(r.status, d?.error ?? { codigo: "error", mensaje: "No se pudo descargar." }); }
  const url = URL.createObjectURL(await r.blob());
  const a = Object.assign(document.createElement("a"), { href: url, download: nombre });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

/** POST que devuelve audio (la muestra de una voz): el ArrayBuffer listo para decodificar. */
export async function audio(ruta, cuerpo) {
  let r;
  try {
    r = await fetch(`/api/v1${ruta}`, { method: "POST", headers: { "Content-Type": "application/json", Authorization: `Bearer ${sesion.token}` }, body: JSON.stringify(cuerpo) });
  } catch { throw new ApiError(0, { codigo: "sin_conexion", mensaje: "No hay conexión con el servidor." }); }
  if (!r.ok) { const d = await r.json().catch(() => null); throw new ApiError(r.status, d?.error ?? { codigo: "error", mensaje: "No se pudo generar la muestra." }); }
  return r.arrayBuffer();
}

export async function api(ruta, { metodo = "GET", cuerpo, autenticado = true } = {}) {
  const headers = { Accept: "application/json" };
  // FormData (subida de archivos): el navegador pone el Content-Type con su separador.
  if (cuerpo !== undefined && !(cuerpo instanceof FormData)) headers["Content-Type"] = "application/json";
  if (autenticado && sesion.token) headers.Authorization = `Bearer ${sesion.token}`;
  let r;
  try {
    r = await fetch(`/api/v1${ruta}`, { method: metodo, headers, body: cuerpo === undefined ? undefined : cuerpo instanceof FormData ? cuerpo : JSON.stringify(cuerpo) });
  } catch {
    throw new ApiError(0, { codigo: "sin_conexion", mensaje: "No hay conexión con el servidor." });
  }
  if (r.status === 204) return null;
  const datos = await r.json().catch(() => null);
  if (!r.ok) {
    const error = datos?.error ?? { codigo: "error", mensaje: "Ocurrió un error inesperado." };
    if (r.status === 401 && autenticado && error.codigo !== "credenciales_invalidas") {
      document.dispatchEvent(new CustomEvent("sesion-expirada", { detail: error.mensaje }));
    }
    throw new ApiError(r.status, error);
  }
  return datos;
}
