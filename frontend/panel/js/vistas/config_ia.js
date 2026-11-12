// Configuración de IA: dónde piensa el asistente (modelo local o proveedor en la nube con solo la clave de API),
// parámetros de generación, personalización y modelo de embeddings de la búsqueda en documentos.
import { api, audio } from "../api.js";
import { aviso, h, insignia } from "../ui.js";

export async function configIA() {
  let estado = await api("/config-ia");
  const raiz = h("section", {});

  // ---------- IA en la nube: elegir proveedor, pegar la clave, probar ----------
  function tarjetaNube() {
    const n = estado.nube;
    const enNube = n.proveedores.some((p) => p.id === n.activo);
    let proveedor = enNube ? n.activo : "openai";
    let prueba = null;
    const caja = h("div", { class: "card mb-4" });

    const clave = h("input", { class: "form-control", id: "nube_clave", type: "password", autocomplete: "off", spellcheck: false });
    const direccion = h("input", { class: "form-control", id: "nube_direccion", type: "url", placeholder: "https://mi-proveedor.com/v1", value: n.direccion || "" });
    const modelo = h("input", { class: "form-control", id: "nube_modelo", list: "nube_modelos", autocomplete: "off", value: enNube ? n.modelo || "" : "" });
    const lista = h("datalist", { id: "nube_modelos" });
    const acepto = h("input", { class: "form-check-input", id: "nube_acepto", type: "checkbox", checked: false });
    const resultado = h("div", { class: "mt-3" });
    const botonProbar = h("button", { class: "btn btn-outline-primary", type: "button" }, "Probar conexión");
    const botonUsar = h("button", { class: "btn btn-primary", type: "button" }, "Guardar y usar este proveedor");
    const selector = h("select", { class: "form-select", id: "nube_proveedor" },
      ...n.proveedores.map((p) => h("option", { value: p.id, selected: p.id === proveedor }, p.nombre)));
    const actual = () => n.proveedores.find((p) => p.id === proveedor);

    function pintarEstado() {
      const p = actual();
      clave.placeholder = p.clave_guardada ? `Guardada (termina en …${p.clave_ultimos4 ?? "****"}). Déjalo vacío para usarla` : "Pega aquí tu clave de API";
      modelo.placeholder = p.modelo_sugerido || "Nombre del modelo";
      direccion.closest(".mb-3").classList.toggle("d-none", !p.pide_direccion);
      nota.textContent = p.nota;
      nota.classList.toggle("d-none", !p.nota);
      botonBorrar.classList.toggle("d-none", !p.clave_guardada || n.activo === p.id);
      botonUsar.disabled = !(prueba?.ok && acepto.checked);
    }
    const invalidar = () => { prueba = null; resultado.replaceChildren(); pintarEstado(); };
    selector.addEventListener("change", () => { proveedor = selector.value; clave.value = ""; modelo.value = ""; lista.replaceChildren(); invalidar(); });
    for (const el of [clave, direccion, modelo]) el.addEventListener("input", invalidar);
    acepto.addEventListener("change", pintarEstado);

    const cuerpo = () => ({ proveedor, api_key: clave.value.trim() || null, modelo: modelo.value.trim() || null, direccion: direccion.value.trim() || null });
    async function probar() {
      botonProbar.disabled = true; resultado.replaceChildren(h("div", { class: "spinner-border spinner-border-sm", role: "status" }));
      try {
        prueba = await api("/config-ia/nube/probar", { metodo: "POST", cuerpo: cuerpo() });
        if (prueba.ok) {
          lista.replaceChildren(...prueba.modelos.map((m) => h("option", { value: m })));
          modelo.value = prueba.modelo;
          resultado.replaceChildren(h("div", { class: "alert alert-success py-2 mb-0" },
            `Funciona. Modelo ${prueba.modelo}: primer texto en ${prueba.ttft_ms ?? "?"} ms, respuesta completa en ${prueba.duracion_ms} ms.`,
            prueba.modelos.length ? ` El proveedor ofrece ${prueba.modelos.length} modelos: puedes cambiar el modelo y volver a probar.` : ""));
        } else {
          resultado.replaceChildren(h("div", { class: "alert alert-danger py-2 mb-0" }, prueba.error));
        }
      } catch (err) { prueba = null; resultado.replaceChildren(h("div", { class: "alert alert-danger py-2 mb-0" }, err.message)); }
      botonProbar.disabled = false; pintarEstado();
    }
    async function usar() {
      botonUsar.disabled = true;
      try {
        estado = await api("/config-ia/nube", { metodo: "PUT", cuerpo: { ...cuerpo(), api_key: clave.value.trim() || null, modelo: modelo.value.trim() || null, autoriza: acepto.checked } });
        aviso(`El asistente ahora usa ${actual().nombre}.`); pintar();
      } catch (err) { aviso(err.message, "danger"); botonUsar.disabled = false; }
    }
    async function volverLocal() {
      try { estado = await api("/config-ia/nube/usar-local", { metodo: "POST" }); aviso("El asistente vuelve a usar el modelo local."); pintar(); }
      catch (err) { aviso(err.message, "danger"); }
    }
    async function borrar() {
      if (!confirm(`¿Borrar la clave guardada de ${actual().nombre}?`)) return;
      try { estado = await api(`/config-ia/nube/${proveedor}/clave`, { metodo: "DELETE" }); aviso("Clave borrada."); pintar(); }
      catch (err) { aviso(err.message, "danger"); }
    }
    botonProbar.addEventListener("click", probar);
    botonUsar.addEventListener("click", usar);
    const botonBorrar = h("button", { class: "btn btn-outline-danger btn-sm", type: "button", onClick: borrar }, "Borrar la clave guardada");
    const nota = h("div", { class: "form-text" });

    const activo = n.activo === "ollama" ? insignia("Modelo local (Ollama)", "success")
      : enNube ? insignia(`En la nube: ${actual().nombre} · ${n.modelo}`, "warning") : insignia("API externa definida por el servidor", "warning");
    caja.append(h("div", { class: "card-body" },
      h("h2", { class: "h5" }, "Dónde piensa el asistente ", activo),
      h("p", { class: "text-body-secondary" }, "Por defecto usa el modelo local: las conversaciones no salen de la institución. Si prefieres una IA en la nube, elige el proveedor, pega tu clave de API y prueba la conexión; la dirección y el modelo ya vienen puestos."),
      enNube ? h("div", { class: "alert alert-warning py-2 d-flex flex-wrap justify-content-between align-items-center gap-2" },
        h("span", {}, "Las conversaciones se están enviando a ", h("strong", {}, actual().nombre), ". La voz (reconocimiento y síntesis) sigue siendo local."),
        h("button", { class: "btn btn-sm btn-outline-dark", type: "button", onClick: volverLocal }, "Volver al modelo local")) : null,
      h("div", { class: "row" },
        h("div", { class: "col-md-5 mb-3" }, h("label", { class: "form-label", for: "nube_proveedor" }, "Proveedor"), selector, nota),
        h("div", { class: "col-md-7 mb-3" }, h("label", { class: "form-label", for: "nube_clave" }, "Clave de API"), clave,
          h("div", { class: "form-text" }, "Se guarda cifrada y no se vuelve a mostrar. Nunca se escribe en los registros."))),
      h("div", { class: "mb-3 d-none" }, h("label", { class: "form-label", for: "nube_direccion" }, "Dirección base del proveedor"), direccion,
        h("div", { class: "form-text" }, "Debe ser https://.")),
      h("div", { class: "mb-3" }, h("label", { class: "form-label", for: "nube_modelo" }, "Modelo"), modelo, lista,
        h("div", { class: "form-text" }, "Opcional: si lo dejas vacío se usa el sugerido. Al probar la conexión se llena con los modelos de tu cuenta.")),
      h("div", { class: "d-flex flex-wrap gap-2 align-items-center" }, botonProbar, botonBorrar),
      resultado,
      h("div", { class: "form-check mt-3" }, acepto, h("label", { class: "form-check-label", for: "nube_acepto" },
        "Entiendo que, al usar este proveedor, el texto de las conversaciones con los pacientes (que puede incluir datos personales) se enviará a sus servidores fuera de la institución, y que debo tener la autorización y el contrato de tratamiento de datos que exige la ley.")),
      h("div", { class: "mt-3" }, botonUsar)));
    pintarEstado();
    return caja;
  }

  // ---------- modelo de embeddings (búsqueda en documentos) ----------
  function tarjetaEmbeddings() {
    const e = estado.embedding;
    const nombres = e.instalados.map((m) => m.nombre);
    const opciones = nombres.includes(e.modelo) ? nombres : [e.modelo, ...nombres];
    const selector = h("select", { class: "form-select", id: "embedding_modelo" }, ...opciones.map((m) => h("option", { value: m, selected: m === e.modelo }, `${m}${m === e.por_defecto ? " (por defecto)" : ""}${m === e.modelo ? " · en uso" : ""}`)));
    const resultado = h("div", { class: "mt-3" });
    const avance = h("div", { class: "mt-3" });
    const botonProbar = h("button", { class: "btn btn-outline-primary", type: "button" }, "Probar este modelo");
    const botonUsar = h("button", { class: "btn btn-primary", type: "button" }, "Usar este modelo y reindexar");

    async function probar() {
      botonProbar.disabled = true; resultado.replaceChildren(h("div", { class: "spinner-border spinner-border-sm", role: "status" }));
      try {
        const r = await api("/config-ia/embeddings/probar", { metodo: "POST", cuerpo: { modelo: selector.value } });
        resultado.replaceChildren(r.ok
          ? h("div", { class: "alert alert-success py-2 mb-0" }, `Sirve: ${r.dimensiones} dimensiones (las que usa la base), ${r.duracion_ms} ms por consulta. Umbral de similitud con este modelo: ${r.umbral}.`)
          : h("div", { class: "alert alert-danger py-2 mb-0" }, r.error));
      } catch (err) { resultado.replaceChildren(h("div", { class: "alert alert-danger py-2 mb-0" }, err.message)); }
      botonProbar.disabled = false;
    }
    async function seguirReindexado() {
      for (let i = 0; i < 300 && raiz.isConnected; i++) {
        let t;
        try { t = await api("/rag/reindexar/estado"); } catch { return; }
        const fin = t.estado !== "ejecutando";
        avance.replaceChildren(h("div", { class: `alert ${fin ? "alert-success" : "alert-info"} py-2 mb-0` },
          fin ? `Reindexado terminado: ${t.hechos} de ${t.total} documentos${t.errores?.length ? `, ${t.errores.length} con error` : ""}.` : `Reindexando documentos: ${t.hechos} de ${t.total}…`));
        if (fin) return;
        await new Promise((ok) => setTimeout(ok, 2000));
      }
    }
    async function usar() {
      if (selector.value === e.modelo) return aviso("Ese modelo ya está en uso.", "info");
      if (!confirm("Al cambiar el modelo hay que volver a indexar todos los documentos (unos segundos por documento). Mientras tanto la búsqueda solo encuentra lo ya reindexado. ¿Continuar?")) return;
      botonUsar.disabled = true;
      try {
        estado = await api("/config-ia/embeddings", { metodo: "PUT", cuerpo: { modelo: selector.value } });
        aviso("Modelo de embeddings cambiado. Reindexando en segundo plano."); pintar(); seguirReindexado();
      } catch (err) { aviso(err.message, "danger"); botonUsar.disabled = false; }
    }
    botonProbar.addEventListener("click", probar); botonUsar.addEventListener("click", usar);

    return h("div", { class: "card mb-4" }, h("div", { class: "card-body" },
      h("h2", { class: "h5" }, "Búsqueda en documentos (embeddings) ", insignia(`${e.modelo} · ${e.dimensiones} dimensiones`, "info")),
      h("p", { class: "text-body-secondary" }, "Convierte los documentos y las preguntas en vectores para encontrar la respuesta correcta. Se listan los modelos Gemma de embeddings instalados en Ollama que dan las dimensiones que guarda la base de datos."),
      e.instalados.length ? null : h("div", { class: "alert alert-warning py-2" }, "No se pudo consultar Ollama o no hay modelos Gemma de embeddings instalados (por ejemplo: ollama pull embeddinggemma)."),
      h("div", { class: "mb-3" }, h("label", { class: "form-label", for: "embedding_modelo" }, "Modelo de embeddings"), selector,
        h("div", { class: "form-text" }, "Cada modelo tiene su propio umbral de similitud (ya calibrado); al cambiar de modelo se reindexa el conocimiento.")),
      h("div", { class: "d-flex flex-wrap gap-2" }, botonProbar, botonUsar), resultado, avance));
  }

  // ---------- voz del asistente ----------
  function tarjetaVoz() {
    const v = estado.voz;
    const selector = h("select", { class: "form-select", id: "voz_asistente" }, ...v.voces.map((o) => h("option", { value: o.id, selected: o.id === v.elegida }, `${o.nombre}${o.motor === "kokoro" ? " · Kokoro" : o.motor === "piper" ? " · Piper" : ""}`)));
    const velocidad = h("input", { class: "form-range", id: "voz_velocidad", type: "range", min: 0.7, max: 1.3, step: 0.05, value: v.velocidad });
    const etiquetaVelocidad = h("span", { class: "fw-semibold" }, `${Number(v.velocidad).toFixed(2)}×`);
    velocidad.addEventListener("input", () => { etiquetaVelocidad.textContent = `${Number(velocidad.value).toFixed(2)}×`; });
    const botonMuestra = h("button", { class: "btn btn-outline-primary", type: "button" }, "Escuchar una muestra");
    const botonGuardar = h("button", { class: "btn btn-primary", type: "button" }, "Usar esta voz");
    const resultado = h("div", { class: "mt-3" });

    async function muestra() {
      botonMuestra.disabled = true; resultado.replaceChildren(h("div", { class: "spinner-border spinner-border-sm", role: "status" }));
      try {
        const t0 = performance.now();
        const datos = await audio("/tts/sintetizar", { texto: "Buenos días. Soy el asistente virtual. ¿En qué puedo ayudarle?", voz: selector.value || null, velocidad: Number(velocidad.value) });
        const ms = Math.round(performance.now() - t0);
        const ctx = new AudioContext();
        const buffer = await ctx.decodeAudioData(datos);
        const fuente = ctx.createBufferSource();
        fuente.buffer = buffer; fuente.connect(ctx.destination); fuente.onended = () => ctx.close().catch(() => {}); fuente.start();
        resultado.replaceChildren(h("div", { class: "text-body-secondary small" }, `Sonando… (se generó en ${ms} ms)`));
      } catch (err) { resultado.replaceChildren(h("div", { class: "alert alert-danger py-2 mb-0" }, err.message)); }
      botonMuestra.disabled = false;
    }
    async function guardarVoz() {
      botonGuardar.disabled = true;
      try { estado = await api("/config-ia/voz", { metodo: "PUT", cuerpo: { voz: selector.value, velocidad: Number(velocidad.value) } }); aviso("Voz del asistente actualizada."); pintar(); }
      catch (err) { aviso(err.message, "danger"); botonGuardar.disabled = false; }
    }
    botonMuestra.addEventListener("click", muestra); botonGuardar.addEventListener("click", guardarVoz);
    const actual = v.voces.find((o) => o.id === v.elegida);
    return h("div", { class: "card mb-4" }, h("div", { class: "card-body" },
      h("h2", { class: "h5" }, "Voz del asistente ", insignia(actual ? actual.nombre : "Automática", "info")),
      h("p", { class: "text-body-secondary" }, "Con qué voz habla el asistente en las llamadas. Piper es la más rápida; Kokoro suena más natural pero tarda más en generar cada frase (alrededor de 1 segundo por frase en este equipo)."),
      h("div", { class: "row" },
        h("div", { class: "col-md-7 mb-3" }, h("label", { class: "form-label", for: "voz_asistente" }, "Voz"), selector),
        h("div", { class: "col-md-5 mb-3" }, h("label", { class: "form-label", for: "voz_velocidad" }, "Velocidad ", etiquetaVelocidad), velocidad)),
      h("div", { class: "d-flex flex-wrap gap-2" }, botonMuestra, botonGuardar), resultado));
  }

  function pintar() {
    const e = estado.efectiva;
    const personalizada = (k) => estado.personalizadas.includes(k) ? insignia("personalizado", "info") : null;
    const chat = estado.modelos.filter((m) => m.chat);
    const modelo = h("select", { class: "form-select", id: "llm_modelo" },
      ...(chat.length ? chat : [{ nombre: e.llm_modelo, herramientas: true }]).map((m) => h("option", { value: m.nombre, selected: m.nombre === e.llm_modelo },
        `${m.nombre}${m.herramientas ? "" : " (sin herramientas: no sirve para el asistente)"}`)));
    const temp = h("input", { class: "form-control", id: "llm_temperatura", type: "number", min: 0, max: 1, step: 0.05, value: e.llm_temperatura });
    const tokens = h("input", { class: "form-control", id: "llm_max_tokens", type: "number", min: 50, max: 1500, step: 10, value: e.llm_max_tokens });
    const encuesta = h("input", { class: "form-check-input", id: "encuesta_activa", type: "checkbox", checked: e.encuesta_activa });
    const razona = h("input", { class: "form-check-input", id: "llm_razonamiento", type: "checkbox", checked: e.llm_razonamiento });
    const frase = h("input", { class: "form-control", id: "frase_activacion", maxlength: 40, placeholder: "hola asistente", value: e.frase_activacion });
    const nombre = h("input", { class: "form-control", id: "nombre_asistente", maxlength: 60, value: e.nombre_asistente });
    const bienvenida = h("textarea", { class: "form-control", id: "mensaje_bienvenida", rows: 3, maxlength: 300 }, e.mensaje_bienvenida);
    const resultado = h("div", { class: "mt-3" });
    const campo = (clave, etiqueta, control, ayuda) => h("div", { class: "mb-3" }, h("label", { class: "form-label", for: clave }, etiqueta, " ", personalizada(clave)), control, ayuda ? h("div", { class: "form-text" }, ayuda) : null);

    async function guardar(ev) {
      ev.preventDefault();
      try {
        estado = await api("/config-ia", { metodo: "PUT", cuerpo: { llm_modelo: modelo.value, llm_temperatura: Number(temp.value), llm_max_tokens: Number(tokens.value),
          llm_razonamiento: razona.checked, nombre_asistente: nombre.value, mensaje_bienvenida: bienvenida.value, encuesta_activa: encuesta.checked, frase_activacion: frase.value.trim() || null } });
        aviso("Configuración guardada."); pintar();
      } catch (err) { aviso(err.message, "danger"); }
    }
    async function restablecer() {
      if (!confirm("¿Volver a los valores por defecto?")) return;
      try { estado = await api("/config-ia", { metodo: "PUT", cuerpo: { llm_modelo: null, llm_temperatura: null, llm_max_tokens: null, llm_razonamiento: null, nombre_asistente: null, mensaje_bienvenida: null, encuesta_activa: null, frase_activacion: null } }); aviso("Valores restablecidos."); pintar(); }
      catch (err) { aviso(err.message, "danger"); }
    }
    async function probar(ev) {
      ev.target.disabled = true; resultado.replaceChildren(h("div", { class: "spinner-border spinner-border-sm", role: "status" }));
      try {
        const r = await api("/config-ia/probar", { metodo: "POST" });
        resultado.replaceChildren(r.ok
          ? h("div", { class: "alert alert-success py-2" }, `Funciona. Modelo ${r.modelo}: primer texto en ${r.ttft_ms ?? "?"} ms, respuesta completa en ${r.duracion_ms} ms${r.tokens_por_segundo ? `, ${r.tokens_por_segundo} tokens/s` : ""}.`)
          : h("div", { class: "alert alert-danger py-2" }, r.error));
      } catch (err) { resultado.replaceChildren(h("div", { class: "alert alert-danger py-2" }, err.message)); }
      ev.target.disabled = false;
    }

    raiz.replaceChildren(
      h("h1", { class: "h3 mb-1" }, "Configuración de IA"),
      h("p", { class: "text-body-secondary" }, `Proveedor activo: ${estado.proveedor === "ollama" ? "Ollama (local: los datos no salen de la institución)" : "IA en la nube"}.`,
        estado.proveedor !== "ollama" ? " Las conversaciones se envían fuera de la institución." : ""),
      tarjetaNube(),
      h("form", { class: "card mb-4", onSubmit: guardar }, h("div", { class: "card-body" },
        h("h2", { class: "h5" }, "Modelo y comportamiento"),
        campo("llm_modelo", "Modelo local (Ollama)", modelo, estado.modelos.length ? "Solo se listan los modelos instalados que pueden conversar. Con un proveedor en la nube este modelo queda en reserva." : "No se pudo consultar Ollama."),
        h("div", { class: "row" }, h("div", { class: "col-md-6" }, campo("llm_temperatura", "Temperatura (0 a 1)", temp, "Más baja = respuestas más consistentes. Recomendado: 0,2.")),
          h("div", { class: "col-md-6" }, campo("llm_max_tokens", "Largo máximo de la respuesta (tokens)", tokens, "Respuestas cortas funcionan mejor por voz."))),
        h("div", { class: "form-check mb-3" }, razona, h("label", { class: "form-check-label", for: "llm_razonamiento" }, "Activar razonamiento ", personalizada("llm_razonamiento")),
          h("div", { class: "form-text" }, "Elige mejor las herramientas pero tarda unos 5 segundos por respuesta. Para voz, mejor apagado.")),
        h("div", { class: "form-check mb-3" }, encuesta, h("label", { class: "form-check-label", for: "encuesta_activa" }, "Encuesta de satisfacción al final de la llamada de voz ", personalizada("encuesta_activa"),
          h("div", { class: "form-text" }, "Cuando el paciente dice que no necesita más ayuda, el asistente le pide una calificación de 1 a 5 (y, si es baja, qué mejorar). Puede no responder. Los resultados están en Reportes → Satisfacción."))),
        campo("nombre_asistente", "Nombre del asistente", nombre),
        campo("frase_activacion", "Frase para activar la llamada por voz (opcional)", frase, "En «Atención por voz → Activar escucha por voz», el panel espera esta frase para empezar solo. Siempre sirven «hola asistente», «oye asistente» y «hola» + el nombre del asistente."), campo("mensaje_bienvenida", "Mensaje de bienvenida (opcional)", bienvenida, "Si lo dejas vacío, el asistente saluda e identifica a la institución por sí mismo."),
        h("div", { class: "d-flex flex-wrap gap-2" }, h("button", { class: "btn btn-primary", type: "submit" }, "Guardar"),
          h("button", { class: "btn btn-outline-secondary", type: "button", onClick: probar }, "Probar conexión"), h("button", { class: "btn btn-outline-danger", type: "button", onClick: restablecer }, "Restablecer")),
        resultado)),
      tarjetaVoz(),
      tarjetaEmbeddings(),
      h("p", { class: "text-body-secondary small mt-3" }, "Las reglas del asistente (no diagnosticar, no inventar, derivar emergencias) y las herramientas que puede usar no se configuran aquí."));
  }
  pintar();
  return raiz;
}
