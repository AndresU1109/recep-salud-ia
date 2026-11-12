// Detecta cuándo empieza y termina de hablar el paciente, a partir del volumen de cuadros de 20 ms. Sin dependencias: se puede probar solo.
export const CUADROS_POR_SEGUNDO = 50;
const CALIBRACION = 25; // 0,5 s iniciales para medir el ruido de fondo
// Una frase natural tiene pausas entre palabras: no se exigen cuadros SEGUIDOS sino una cantidad mínima dentro de una ventana corta
// (con «seguidos» al hablar encima del asistente casi nunca se abría la intervención). Un golpe o un clic (≈100 ms) no alcanza.
const INICIO = 8, VENTANA = 12; // 160 ms de voz dentro de los últimos 240 ms
const INICIO_ENCIMA = 14, VENTANA_ENCIMA = 22; // para hablar encima del asistente se exige más (el eco de los parlantes no debe cortarlo)
const FIN_SILENCIO = 45; // 0,9 s de silencio cierran la intervención
const PRECUADROS = 15; // 0,3 s anteriores a la detección: el comienzo de la frase no se pierde
const UMBRAL_MINIMO = 0.012;

export class DetectorDeVoz {
  constructor(maxSegundos = 28) {
    this.maxCuadros = maxSegundos * CUADROS_POR_SEGUNDO;
    this.reiniciar();
  }

  reiniciar() {
    this.piso = null;
    this.acumulado = 0;
    this.calibrados = 0;
    this.recientes = []; // los últimos cuadros: ¿superaron el umbral?
    this.silencio = 0;
    this.cuadros = 0;
    this.hablando = false;
    this.pre = [];
  }

  /** Procesa un cuadro. Devuelve null o {accion: "inicio"|"cuadro"|"fin", cuadros: [...]}.
   *  ``encima``: el asistente está hablando; ``bloqueado``: el servidor está procesando (no se abre una intervención nueva). */
  procesar(cuadro, rms, { encima = false, bloqueado = false } = {}) {
    if (this.piso === null) {
      this.acumulado += rms;
      if (++this.calibrados >= CALIBRACION) this.piso = this.acumulado / CALIBRACION;
      return null;
    }
    const alto = rms > Math.max(UMBRAL_MINIMO, this.piso * 3) * (encima ? 2 : 1);
    if (!this.hablando) {
      this.pre.push(cuadro);
      if (this.pre.length > PRECUADROS) this.pre.shift();
      if (bloqueado) { this.recientes = []; return null; }
      this.recientes.push(alto);
      if (this.recientes.length > (encima ? VENTANA_ENCIMA : VENTANA)) this.recientes.shift();
      if (!alto) this.piso = this.piso * 0.98 + rms * 0.02; // el ruido de fondo cambia con el tiempo
      if (this.recientes.filter(Boolean).length < (encima ? INICIO_ENCIMA : INICIO)) return null;
      const cuadros = this.pre;
      Object.assign(this, { hablando: true, silencio: 0, cuadros: cuadros.length, pre: [], recientes: [] });
      return { accion: "inicio", cuadros };
    }
    this.cuadros++;
    this.silencio = alto ? 0 : this.silencio + 1;
    if (this.silencio >= FIN_SILENCIO || this.cuadros >= this.maxCuadros) {
      this.hablando = false;
      return { accion: "fin", cuadros: [cuadro] };
    }
    return { accion: "cuadro", cuadros: [cuadro] };
  }
}
