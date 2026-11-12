// Corre en el hilo de audio: baja el micrófono (44.1 o 48 kHz) a 16 kHz, lo pasa a enteros de 16 bits y lo entrega en cuadros de 20 ms
// junto con su volumen (RMS). El navegador no deja pedir 16 kHz de forma fiable (Firefox lo rechaza), por eso se remuestrea aquí.
const CUADRO = 320; // 20 ms a 16 kHz

class CapturaPcm extends AudioWorkletProcessor {
  constructor() {
    super();
    this.razon = sampleRate / 16000; // muestras de entrada por cada muestra de salida
    this.fase = 0;
    this.suma = 0;
    this.n = 0;
    this.cuadro = new Float32Array(CUADRO);
    this.pos = 0;
  }

  process(entradas) {
    const canal = entradas[0]?.[0];
    if (!canal) return true;
    for (let i = 0; i < canal.length; i++) {
      this.suma += canal[i];
      this.n++;
      this.fase++;
      if (this.fase >= this.razon) { // promedio de las muestras del tramo: filtro sencillo contra el aliasing
        this.fase -= this.razon;
        this.cuadro[this.pos++] = this.suma / this.n;
        this.suma = 0;
        this.n = 0;
        if (this.pos === CUADRO) this.entregar();
      }
    }
    return true;
  }

  entregar() {
    const pcm = new Int16Array(CUADRO);
    let energia = 0;
    for (let i = 0; i < CUADRO; i++) {
      const x = Math.max(-1, Math.min(1, this.cuadro[i]));
      energia += x * x;
      pcm[i] = x < 0 ? x * 32768 : x * 32767;
    }
    this.port.postMessage({ pcm: pcm.buffer, rms: Math.sqrt(energia / CUADRO) }, [pcm.buffer]);
    this.pos = 0;
  }
}

registerProcessor("captura-pcm", CapturaPcm);
