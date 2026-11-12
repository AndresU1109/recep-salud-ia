// Llamada de voz con el asistente: micrófono → WebSocket (PCM16 16 kHz) y respuesta hablada que llega frase por frase.
// El audio nunca se guarda en el navegador. Estados que ve la pantalla: conectando · escuchando · tu · procesando · asistente · cerrada.
import { sesion } from "../api.js";
import { DetectorDeVoz } from "./detector.js";

const CUADROS_POR_ENVIO = 5; // 100 ms por mensaje: pocos mensajes sin añadir espera notable

export class ClienteVoz {
  /** ``cb``: {onEstado(estado), onNivel(rms 0..1), onEvento(evento del servidor)}. ``real``: conversación real con un paciente (solo la concede el permiso de atender). */
  constructor(cb, { real = false, institucionId = null } = {}) {
    this.institucionId = institucionId;
    this.cb = cb;
    this.real = real;
    this.detector = new DetectorDeVoz();
    this.estado = "conectando";
    this.servidorOcupado = false; // transcribiendo o pensando
    this.fuentes = new Set();
    this.siguiente = 0;
    this.generacion = 0; // sube con cada interrupción: el audio que se estaba decodificando deja de valer
    this.decodificando = 0;
    this.descartando = false; // entre pedir interrumpir y su confirmación, el audio que llegue es de la respuesta cortada
    this.cabecera = null;
    this.cierreDelAsistente = false;
    this.salida = [];
    this.cola = []; // lo que el paciente dice mientras el servidor aún procesa lo anterior: no se pierde, se envía al quedar libre
    this.colaLista = false;
    this.ultimoCuadro = this.ultimoEvento = performance.now();
    this.cuadrosMudos = 0;
  }

  async iniciar() {
    this.ctx = new AudioContext();
    await this.ctx.resume();
    this.analizador = this.ctx.createAnalyser(); // mide lo que suena del asistente (para animar la pantalla)
    this.analizador.fftSize = 512;
    this.analizador.connect(this.ctx.destination);
    this.datos = new Uint8Array(this.analizador.fftSize);
    await this.ctx.audioWorklet.addModule("/panel/js/voz/captura.worklet.js");
    // El navegador cancela el eco de los parlantes: sin esto, el asistente se oiría a sí mismo y se interrumpiría.
    this.mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 } });
    const pista = this.mic.getAudioTracks()[0];
    pista?.addEventListener("ended", () => this._aviso("El micrófono se desconectó. Revise el dispositivo y vuelva a iniciar la conversación."));
    pista?.addEventListener("mute", () => this._aviso("El sistema silenció el micrófono (¿otra aplicación lo está usando?)."));
    this.nodo = new AudioWorkletNode(this.ctx, "captura-pcm");
    this.nodo.port.onmessage = (m) => this._cuadro(new Int16Array(m.data.pcm), m.data.rms);
    const mudo = this.ctx.createGain(); // el nodo debe estar conectado a algo para que el navegador lo ejecute; en silencio para no oírse
    mudo.gain.value = 0;
    this.ctx.createMediaStreamSource(this.mic).connect(this.nodo);
    this.nodo.connect(mudo).connect(this.ctx.destination);
    await this._conectar();
    this.vigilante = setInterval(() => this._vigilar(), 2000);
  }

  _aviso(mensaje) { this.cb.onEvento?.({ tipo: "aviso_local", mensaje }); }

  /** Cada 2 s: si el micrófono dejó de entregar audio o el servidor lleva demasiado sin responder, se recupera o se avisa (nunca se queda «sordo» en silencio). */
  _vigilar() {
    if (this.estado === "cerrada") return;
    const ahora = performance.now();
    if (ahora - this.ultimoCuadro > 3000) {
      this.ctx?.resume?.().catch(() => {});
      if (ahora - this.ultimoCuadro > 6000 && !this.avisoSinAudio) { this.avisoSinAudio = true; this._aviso("No llega audio del micrófono. Revise que el navegador tenga permiso y que no esté en uso por otra aplicación."); }
    } else this.avisoSinAudio = false;
    if (this.servidorOcupado && ahora - this.ultimoEvento > 60000) { // el servidor no contestó: se destraba para que pueda volver a hablar
      this.servidorOcupado = false;
      this._aviso("El asistente tardó demasiado en responder. Puede volver a hablar.");
      this._liberarCola();
      this._refrescar();
    }
  }

  _conectar() {
    return new Promise((resolver, rechazar) => {
      const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/v1/voz/ws`;
      const ws = (this.ws = new WebSocket(url));
      ws.binaryType = "arraybuffer";
      let listo = false;
      ws.onopen = () => this._enviar({ tipo: "auth", token: sesion.token, real: this.real, institucion_id: this.institucionId }); // el token va en el primer mensaje, nunca en la URL
      ws.onerror = () => { if (!listo) rechazar(new Error("No se pudo abrir la conexión de voz.")); };
      ws.onclose = async () => {
        if (this.cierreDelAsistente) await this._esperarFinDelAudio(); // la despedida se oye completa antes de colgar
        this._liberar(); this._estado("cerrada"); if (!listo) rechazar(new Error("La conexión de voz se cerró."));
      };
      ws.onmessage = (ev) => {
        if (typeof ev.data !== "string") return this._audio(ev.data);
        const e = JSON.parse(ev.data);
        if (e.tipo === "listo") { listo = true; resolver(e); }
        else if (e.tipo === "error" && !listo) rechazar(new Error(e.mensaje));
        this._evento(e);
      };
    });
  }

  _enviar(obj) { if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(obj)); }

  _evento(e) {
    this.ultimoEvento = performance.now();
    switch (e.tipo) {
      case "estado":
        if (e.valor === "transcribiendo" || e.valor === "pensando") this.servidorOcupado = true;
        else if (e.valor === "escuchando") { this.servidorOcupado = false; this._liberarCola(); }
        break;
      case "audio": this.cabecera = e; break;
      case "interrumpida": this.descartando = false; break;
      case "fin_llamada": this.cierreDelAsistente = true; break; // el paciente dijo que no necesita más ayuda: el servidor cuelga tras la despedida
      case "error": if (e.codigo !== "ocupado") { this.servidorOcupado = false; this._liberarCola(); } break;
      default:
    }
    this._refrescar();
    if (e.tipo === "cerrada" && this.cierreDelAsistente) { this._esperarFinDelAudio().then(() => this.cb.onEvento?.(e)); return; }
    this.cb.onEvento?.(e);
  }

  /** Espera a que termine de sonar lo que el asistente está diciendo (como mucho 40 s). */
  async _esperarFinDelAudio() {
    const limite = Date.now() + 40000;
    while ((this._reproduciendo() || this.decodificando) && Date.now() < limite) await new Promise((ok) => setTimeout(ok, 150));
  }

  // ---------------- micrófono ----------------
  _cuadro(pcm, rms) {
    this.ultimoCuadro = performance.now();
    this.cuadrosMudos = rms === 0 && !this.silenciado ? this.cuadrosMudos + 1 : 0;
    if (this.cuadrosMudos === 500 && !this.avisoMudo) { this.avisoMudo = true; this._aviso("El micrófono solo envía silencio absoluto. Revise que no esté silenciado en Windows o en el navegador."); }
    this.cb.onNivel?.(Math.min(1, rms * 4));
    const r = this.detector.procesar(pcm, rms, { encima: this._reproduciendo() });
    if (!r) return;
    if (r.accion === "inicio" && this._reproduciendo()) this.interrumpir(); // el paciente habla encima: se calla el asistente
    this.salida.push(...r.cuadros);
    if (this.servidorOcupado) { // el servidor sigue con lo anterior: lo que diga ahora se guarda y se envía en cuanto quede libre
      this.cola.push(...this.salida);
      this.salida = [];
      if (r.accion === "fin") this.colaLista = true;
    } else if (r.accion === "fin") {
      this._vaciar();
      this._enviar({ tipo: "fin_audio" });
      this.servidorOcupado = true; // hasta que el servidor diga lo contrario
    } else if (this.salida.length >= CUADROS_POR_ENVIO || r.accion === "inicio") this._vaciar();
    this._refrescar();
  }

  /** El servidor quedó libre: se envía lo que el paciente dijo mientras tanto (y, si ya terminó de hablar, se cierra su intervención). */
  _liberarCola() {
    if (!this.cola.length) { this.colaLista = false; return; }
    this.salida = [...this.cola, ...this.salida];
    this.cola = [];
    this._vaciar();
    if (this.colaLista) {
      this.colaLista = false;
      this._enviar({ tipo: "fin_audio" });
      this.servidorOcupado = true;
    }
  }

  _vaciar() {
    if (!this.salida.length || this.ws?.readyState !== WebSocket.OPEN) { this.salida = []; return; }
    const junto = new Int16Array(this.salida.length * 320);
    this.salida.forEach((c, i) => junto.set(c, i * 320));
    this.ws.send(junto.buffer);
    this.salida = [];
  }

  // ---------------- reproducción ----------------
  _reproduciendo() { return this.fuentes.size > 0 || this.decodificando > 0; }

  async _audio(buffer) {
    const g = this.generacion;
    if (!this.cabecera || this.descartando) { this.cabecera = null; return; }
    this.cabecera = null;
    this.decodificando++;
    this._refrescar();
    try {
      const datos = await this.ctx.decodeAudioData(buffer);
      if (g !== this.generacion) return;
      const fuente = this.ctx.createBufferSource();
      fuente.buffer = datos;
      fuente.connect(this.analizador);
      const t = Math.max(this.ctx.currentTime + 0.03, this.siguiente);
      fuente.start(t);
      this.siguiente = t + datos.duration;
      this.fuentes.add(fuente);
      fuente.onended = () => { this.fuentes.delete(fuente); this._refrescar(); };
    } catch { /* un trozo ilegible: se sigue con el siguiente */ } finally {
      this.decodificando--;
      this._refrescar();
    }
  }

  _detenerReproduccion() {
    this.generacion++;
    for (const f of this.fuentes) { f.onended = null; try { f.stop(); } catch { /* ya había terminado */ } }
    this.fuentes.clear();
    this.siguiente = 0;
  }

  interrumpir() {
    this._detenerReproduccion();
    this.descartando = true;
    this._enviar({ tipo: "interrumpir" });
    this._refrescar();
  }

  /** Volumen (0..1) de lo que está diciendo el asistente en este instante. */
  nivelSalida() {
    if (!this.analizador || !this._reproduciendo()) return 0;
    this.analizador.getByteTimeDomainData(this.datos);
    let e = 0;
    for (const v of this.datos) { const x = (v - 128) / 128; e += x * x; }
    return Math.min(1, Math.sqrt(e / this.datos.length) * 3.5);
  }

  /** Silencia el micrófono sin cortar la llamada. */
  silenciar(si) { this.mic?.getAudioTracks().forEach((p) => { p.enabled = !si; }); this.silenciado = si; }

  enviarTexto(texto) { this._enviar({ tipo: "texto", texto }); }

  // ---------------- estado y cierre ----------------
  _estado(nuevo) { if (nuevo !== this.estado) { this.estado = nuevo; this.cb.onEstado?.(nuevo); } }

  _refrescar() {
    if (this.estado === "cerrada") return;
    this._estado(this._reproduciendo() ? "asistente" : this.servidorOcupado ? "procesando" : this.detector.hablando ? "tu" : "escuchando");
  }

  _liberar() {
    clearInterval(this.vigilante);
    this._detenerReproduccion();
    this.mic?.getTracks().forEach((p) => p.stop());
    this.nodo?.disconnect();
    this.ctx?.close().catch(() => {});
    this.mic = this.nodo = null;
  }

  colgar() {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._enviar({ tipo: "finalizar" });
      setTimeout(() => this.ws?.close(), 1500); // el servidor responde «cerrada» y cuelga; si no, se corta aquí
    }
    this._liberar();
    this._estado("cerrada");
  }
}
