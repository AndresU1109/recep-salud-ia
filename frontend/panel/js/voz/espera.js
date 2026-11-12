// Modo de espera: el panel escucha el micrófono y, cuando alguien dice la frase de activación («hola asistente»), inicia la llamada solo.
// Cada frase corta se manda al servidor de esta institución, que responde únicamente sí o no: no se guarda audio ni texto, y la llamada
// completa solo empieza después de la activación. Mientras dura una llamada, esta escucha está apagada (el micrófono lo usa la llamada).
import { sesion } from "../api.js";
import { DetectorDeVoz } from "./detector.js";

const MAX_CUADROS = 5 * 50; // una frase de activación dura un par de segundos: lo más largo que se prueba son 5 s

export class EscuchaDeActivacion {
  /** ``cb``: {onActivar(), onNivel(rms 0..1), onAviso(mensaje)} */
  constructor(cb) {
    this.cb = cb;
    this.detector = new DetectorDeVoz(6);
    this.cuadros = [];
    this.probando = false;
    this.activa = false;
  }

  async iniciar() {
    this.ctx = new AudioContext();
    await this.ctx.resume();
    await this.ctx.audioWorklet.addModule("/panel/js/voz/captura.worklet.js");
    this.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    this.nodo = new AudioWorkletNode(this.ctx, "captura-pcm");
    this.nodo.port.onmessage = (m) => this._cuadro(new Int16Array(m.data.pcm), m.data.rms);
    const mudo = this.ctx.createGain(); // el nodo debe estar conectado a algo para que el navegador lo ejecute; en silencio
    mudo.gain.value = 0;
    this.ctx.createMediaStreamSource(this.mic).connect(this.nodo);
    this.nodo.connect(mudo).connect(this.ctx.destination);
    this.activa = true;
  }

  _cuadro(pcm, rms) {
    if (!this.activa) return;
    this.cb.onNivel?.(Math.min(1, rms * 4));
    const r = this.detector.procesar(pcm, rms, { bloqueado: this.probando });
    if (!r) return;
    if (r.accion === "inicio") this.cuadros = [];
    this.cuadros.push(...r.cuadros);
    if (r.accion === "fin") {
      const cuadros = this.cuadros;
      this.cuadros = [];
      if (cuadros.length <= MAX_CUADROS) this._probar(cuadros); // una conversación entera cerca del micrófono no se manda
    }
  }

  async _probar(cuadros) {
    this.probando = true;
    try {
      const junto = new Int16Array(cuadros.length * 320);
      cuadros.forEach((c, i) => junto.set(c, i * 320));
      const r = await fetch("/api/v1/voz/activacion", { method: "POST", headers: { "Content-Type": "application/octet-stream", Authorization: `Bearer ${sesion.token}` }, body: junto.buffer });
      if (r.status === 401) { this.detener(); this.cb.onAviso?.("La sesión terminó: la escucha por voz se apagó."); return; }
      if (r.ok && (await r.json()).activar && this.activa) { this.detener(); this.cb.onActivar?.(); }
    } catch { /* sin conexión un momento: se sigue escuchando */ } finally { this.probando = false; }
  }

  detener() {
    this.activa = false;
    this.mic?.getTracks().forEach((p) => p.stop());
    this.nodo?.disconnect();
    this.ctx?.close().catch(() => {});
    this.mic = this.nodo = null;
  }
}
