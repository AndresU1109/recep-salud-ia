"""Interfaz de voz por WebSocket: el navegador manda lo que dice el paciente (PCM16 mono 16 kHz) y recibe, frase por frase, la respuesta hablada.

Protocolo (JSON de texto salvo que se indique):
  cliente → servidor   {"tipo":"auth","token":…} (primer mensaje; el token NO va en la URL para que no quede en registros)
                       bytes PCM16 (fragmentos de una intervención) · {"tipo":"fin_audio"} · {"tipo":"texto","texto":…}
                       {"tipo":"interrumpir"} · {"tipo":"finalizar"} · {"tipo":"ping"}
  servidor → cliente   listo · estado(escuchando|transcribiendo|pensando|hablando) · transcripcion · respuesta · audio (+ un mensaje binario WAV)
                       fin_respuesta · interrumpida · error · cerrada · pong
El audio no se guarda. Se audita cuánto duró y qué motor se usó, nunca lo dicho."""
import asyncio
import json
import logging
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errores import ErrorNegocio
from app.database.session import get_db
from app.models import Institucion, Usuario
from app.models.enums import CanalConversacion, EstadoConversacion
from app.security.dependencias import usuario_desde_token
from app.api.v1.instituciones import nombre_corto, puede_conversar
from app.security.permisos import Permiso, tiene_permiso
from app.services import activacion_service, agente_service, auditoria, institucion_service
from app.stt import transcriptor
from app.tts import servicio as tts

router = APIRouter(prefix="/voz", tags=["Interfaz de voz"])
log = logging.getLogger("voz")

BYTES_POR_SEGUNDO = 16000 * 2  # PCM16 mono 16 kHz
MAX_TEXTO = 1000
NO_ENTENDI = "Disculpe, no le escuché bien. ¿Puede repetirlo, por favor?"
VARIOS_NO_ENTENDI = "Parece que el audio no se escucha con claridad. Puede escribirme su mensaje, o decirme «quiero hablar con una persona»."


def _origen_permitido(ws: WebSocket) -> bool:
    """Un WebSocket no pasa por CORS: se comprueba el origen para que otra página no abra la conexión desde el navegador de alguien."""
    origen = ws.headers.get("origin")
    if not origen:
        return True  # clientes que no son navegadores (pruebas, herramientas); igual necesitan un token válido
    return origen in get_settings().cors_origins or urlparse(origen).netloc == ws.headers.get("host")


MAX_BYTES_ACTIVACION = 8 * BYTES_POR_SEGUNDO  # una frase de activación dura un par de segundos


@router.get("/activacion", summary="Las frases que activan la llamada en el modo de espera")
def frases_de_activacion(_: Usuario = Depends(puede_conversar), db: Session = Depends(get_db)) -> dict:
    return {"frases": activacion_service.frases(db)}


@router.post("/activacion", summary="¿Lo que se oyó es la frase de activación? (PCM16 16 kHz en el cuerpo; solo responde sí o no, no guarda ni devuelve el texto)")
async def detectar_activacion(request: Request, _: Usuario = Depends(puede_conversar), db: Session = Depends(get_db)) -> dict:
    datos = await request.body()
    if not datos or len(datos) > MAX_BYTES_ACTIVACION:
        raise ErrorNegocio("audio_invalido", f"El audio debe durar hasta {MAX_BYTES_ACTIVACION // BYTES_POR_SEGUNDO} segundos.", 422)
    r = await run_in_threadpool(transcriptor.obtener_transcriptor().transcribir, datos, "pcm16")
    if r.silencio or r.baja_confianza or not r.texto.strip():
        return {"activar": False}
    return {"activar": activacion_service.coincide(r.texto, await run_in_threadpool(activacion_service.frases, db))}


class SesionVoz:
    def __init__(self, ws: WebSocket, db: Session):
        self.ws, self.db, self.s = ws, db, get_settings()
        self.usuario: Usuario | None = None
        self.institucion_id: int | None = None  # solo si el cliente la indica al abrir (opcional); normalmente se elige HABLANDO, durante la conversación
        self.real = False  # True: conversación REAL con un paciente (cuenta en reportes); False: de prueba
        self.conv_id: uuid.UUID | None = None
        self.buffer = bytearray()
        self.excedido = False
        self.tarea: asyncio.Task | None = None
        self.cancelar = asyncio.Event()
        self.no_entendidos = 0
        self.turnos = 0
        self.audio_s = 0.0
        self.motivo_cierre = "desconexion"
        self.terminada = False  # la conversación terminó (finalizada o caducada)
        self.desconectado = False  # el cliente ya no está: no tiene sentido seguir hablando
        self._fin = asyncio.Event()  # despierta el bucle principal cuando la llamada debe cerrarse desde otra tarea
        self._envio = asyncio.Lock()  # el audio y su encabezado deben salir juntos aunque haya dos tareas enviando

    def _colgar(self, motivo: str) -> None:
        self.motivo_cierre, self.terminada = motivo, True
        self._fin.set()

    # ---------------- salida ----------------
    async def emitir(self, **evento) -> None:
        try:
            async with self._envio:
                await self.ws.send_text(json.dumps(evento, ensure_ascii=False))
        except Exception:  # noqa: BLE001 - el cliente se fue
            self.desconectado = True
            self._fin.set()

    async def emitir_audio(self, indice: int, frase: str, audio) -> None:
        try:
            async with self._envio:
                await self.ws.send_text(json.dumps({"tipo": "audio", "indice": indice, "frase": frase, "frecuencia": audio.frecuencia,
                                                   "duracion_s": round(audio.duracion_s, 2), "motor": audio.motor}, ensure_ascii=False))
                await self.ws.send_bytes(audio.wav)
        except Exception:  # noqa: BLE001
            self.desconectado = True
            self._fin.set()

    async def error(self, codigo: str, mensaje: str) -> None:
        await self.emitir(tipo="error", codigo=codigo, mensaje=mensaje)

    async def hablar(self, texto: str, *, firme: bool = False) -> None:
        """Sintetiza y envía frase por frase (la primera sale antes de terminar la última). ``firme``: no se corta aunque el paciente interrumpa."""
        await self.emitir(tipo="estado", valor="hablando")
        frases = iter(tts.obtener_tts().sintetizar_frases(texto))
        indice, interrumpida = 0, False
        try:
            while not self.desconectado:
                if self.cancelar.is_set() and not firme:
                    interrumpida = True
                    break
                par = await run_in_threadpool(next, frases, None)
                if par is None:
                    break
                await self.emitir_audio(indice, par[0], par[1])
                indice += 1
        except ErrorNegocio as e:  # sin voz disponible: el texto ya se envió, el paciente puede leerlo
            await self.error(e.codigo, e.mensaje)
        await self.emitir(tipo="fin_respuesta", frases=indice, interrumpida=interrumpida)
        await self.emitir(tipo="estado", valor="escuchando")

    # ---------------- entrada ----------------
    async def _recibir_texto(self, timeout: float) -> dict | None:
        msg = await asyncio.wait_for(self.ws.receive(), timeout)
        if msg["type"] == "websocket.disconnect":
            raise ConnectionError
        return self._json(msg.get("text")) if msg.get("text") is not None else None

    @staticmethod
    def _json(texto: str) -> dict | None:
        if len(texto) > 4096:
            return None
        try:
            d = json.loads(texto)
        except ValueError:
            return None
        return d if isinstance(d, dict) else None

    async def autenticar(self) -> bool:
        try:
            primero = await self._recibir_texto(self.s.voz_espera_auth_s)
        except (asyncio.TimeoutError, ConnectionError):
            return False
        token = primero.get("token") if primero and primero.get("tipo") == "auth" else None
        if not isinstance(token, str):
            await self.error("no_autenticado", "El primer mensaje debe ser {\"tipo\":\"auth\",\"token\":…}.")
            return False
        try:
            usuario = await run_in_threadpool(self._validar, token, primero.get("institucion_id"))
        except ErrorNegocio as e:
            await self.error(e.codigo, e.mensaje)
            return False
        self.usuario = usuario
        # «real» solo lo concede el permiso de atender pacientes; quien solo puede probar siempre queda en modo prueba.
        self.real = primero.get("real") is True and tiene_permiso(usuario.rol, Permiso.AGENTE_ATENDER)
        return True

    def _validar(self, token: str, institucion_id=None) -> Usuario:
        usuario = usuario_desde_token(self.db, token)
        if institucion_id is not None:
            if isinstance(institucion_id, bool) or not isinstance(institucion_id, int):
                raise ErrorNegocio("institucion_invalida", "La institución debe ser un número.", 422)
            institucion_service.obtener(self.db, institucion_id)  # 404 si no existe o está inactiva
            self.institucion_id = institucion_id
        if usuario.debe_cambiar_clave:
            raise ErrorNegocio("cambio_de_clave_requerido", "Debes cambiar tu contraseña antes de continuar.", 403)
        if not (tiene_permiso(usuario.rol, Permiso.AGENTE_PROBAR) or tiene_permiso(usuario.rol, Permiso.AGENTE_ATENDER)):
            auditoria.registrar(self.db, accion="acceso_denegado", usuario_id=usuario.id, exito=False, detalle={"permiso": Permiso.AGENTE_ATENDER.value, "canal": "voz"})
            self.db.commit()
            raise ErrorNegocio("sin_permiso", "No tienes permiso para esta acción.", 403)
        self.db.commit()  # cierra la lectura: la conexión no debe quedar «en transacción» durante toda la llamada
        return usuario

    # ---------------- conversación ----------------
    def _abrir(self):
        conv, r = agente_service.iniciar(self.db, CanalConversacion.VOZ, es_prueba=not self.real, institucion_id=self.institucion_id)
        auditoria.registrar(self.db, accion="voz_sesion_iniciada", usuario_id=self.usuario.id, entidad="conversacion", entidad_id=str(conv.id), detalle={"real": self.real, "institucion_id": conv.institucion_id})
        self.db.commit()
        inst = self.db.get(Institucion, conv.institucion_id) if conv.institucion_id else None
        return conv.id, r, ({"id": inst.id, "nombre": nombre_corto(inst.nombre), "tipo": inst.tipo} if inst else None)

    def _enviar(self, texto: str):
        return agente_service.enviar(self.db, self.conv_id, texto)[1]

    def _auditar_stt(self, r) -> None:
        auditoria.registrar(self.db, accion="stt_transcribir", usuario_id=self.usuario.id, detalle={"audio_s": r.duracion_audio_s, "ms": r.duracion_proceso_ms, "silencio": r.silencio})
        self.db.commit()

    def _cerrar(self, estado: EstadoConversacion) -> None:
        if self.conv_id:
            agente_service.finalizar(self.db, self.conv_id, estado)
            auditoria.registrar(self.db, accion="voz_sesion_cerrada", usuario_id=self.usuario.id, entidad="conversacion", entidad_id=str(self.conv_id),
                                detalle={"turnos": self.turnos, "audio_s": round(self.audio_s, 1), "motivo": self.motivo_cierre})
            self.db.commit()

    async def _anunciar(self, r) -> None:
        if r.institucion:  # quedó elegida la institución durante la conversación: la pantalla lo muestra
            await self.emitir(tipo="institucion", institucion=r.institucion)
        await self.emitir(tipo="respuesta", texto=r.texto, intencion=r.intencion, fuentes=r.fuentes, herramientas=r.herramientas, transferida=r.transferida,
                          emergencia=r.emergencia, requiere_confirmacion=r.requiere_confirmacion, terminar=r.terminar)
        # Un aviso de emergencia (llamar al 123) y la despedida se dicen completos aunque el paciente hable encima.
        await self.hablar(r.texto, firme=r.emergencia or r.terminar)
        if r.terminar:  # el paciente confirmó que no necesita más ayuda: tras la despedida la llamada se cierra sola
            await self.emitir(tipo="fin_llamada", motivo="sin_mas_ayuda")
            self._colgar("finalizada_por_el_asistente")

    async def saludar(self) -> None:
        self.conv_id, r, institucion = await run_in_threadpool(self._abrir)
        await self.emitir(tipo="listo", conversacion_id=str(self.conv_id), entrada={"formato": "pcm16", "frecuencia": 16000, "canales": 1},
                          max_segundos=self.s.stt_max_segundos, es_prueba=not self.real, institucion=institucion)
        await self._anunciar(r)

    async def _transcribir(self, pcm: bytes) -> str | None:
        """El texto a enviar al asistente, o None si no se entendió (entonces ya se le pidió repetir)."""
        await self.emitir(tipo="estado", valor="transcribiendo")
        r = await run_in_threadpool(transcriptor.obtener_transcriptor().transcribir, pcm, "pcm16")
        self.audio_s += r.duracion_audio_s
        await run_in_threadpool(self._auditar_stt, r)
        await self.emitir(tipo="transcripcion", texto=r.texto, confianza=round(r.confianza, 2), silencio=r.silencio, baja_confianza=r.baja_confianza)
        if r.silencio or not r.texto.strip() or r.baja_confianza:  # con poca confianza NO se actúa sobre lo transcrito: se pide repetir
            self.no_entendidos += 1
            texto = VARIOS_NO_ENTENDI if self.no_entendidos >= self.s.voz_max_no_entendidos else NO_ENTENDI
            await self.emitir(tipo="respuesta", texto=texto, intencion="no_entendido", fuentes=[], herramientas=[], transferida=False, emergencia=False, requiere_confirmacion=False)
            await self.hablar(texto)
            return None
        self.no_entendidos = 0
        return r.texto

    async def turno(self, *, pcm: bytes | None = None, texto: str | None = None) -> None:
        self.cancelar.clear()
        try:
            if pcm is not None:
                texto = await self._transcribir(pcm)
                if texto is None:
                    return
            await self.emitir(tipo="estado", valor="pensando")
            r = await run_in_threadpool(self._enviar, texto)
            self.turnos += 1
            await self._anunciar(r)
        except ErrorNegocio as e:
            await self.error(e.codigo, e.mensaje)
            if e.codigo in ("sesion_expirada", "conversacion_cerrada", "conversacion_no_encontrada"):
                self._colgar("sesion_terminada")
            else:
                await self.emitir(tipo="estado", valor="escuchando")
        except Exception:  # noqa: BLE001
            log.exception("voz: fallo inesperado en un turno")
            self.db.rollback()
            await self.error("error_interno", "Ocurrió un error. Intenta de nuevo.")
            await self.emitir(tipo="estado", valor="escuchando")

    # ---------------- bucle principal ----------------
    def _acumular(self, trozo: bytes) -> bool:
        if self.excedido:
            return False
        if len(self.buffer) + len(trozo) > self.s.stt_max_segundos * BYTES_POR_SEGUNDO:
            self.excedido = True
            self.buffer.clear()
            return False
        self.buffer += trozo
        return True

    async def _iniciar_turno(self, **datos) -> None:
        if self.tarea and not self.tarea.done():
            if not self.cancelar.is_set():
                await self.error("ocupado", "El asistente todavía está respondiendo. Espera un momento o interrumpe.")
                return
            await self.tarea  # interrumpió: la respuesta anterior se corta en la próxima frase
        self.tarea = asyncio.create_task(self.turno(**datos))

    async def _comando(self, d: dict) -> bool:
        """False para terminar la llamada."""
        tipo = d.get("tipo")
        if tipo == "fin_audio":
            pcm, excedido = bytes(self.buffer[: len(self.buffer) // 2 * 2]), self.excedido  # PCM16: siempre muestras completas
            self.buffer.clear()
            self.excedido = False
            if excedido:
                await self.error("audio_muy_largo", f"Cada intervención puede durar hasta {self.s.stt_max_segundos} segundos.")
            elif pcm:
                await self._iniciar_turno(pcm=pcm)
        elif tipo == "texto":
            texto = d.get("texto")
            if not isinstance(texto, str) or not 1 <= len(texto.strip()) <= MAX_TEXTO:
                await self.error("texto_invalido", f"El texto debe tener entre 1 y {MAX_TEXTO} caracteres.")
            else:
                await self._iniciar_turno(texto=texto.strip())
        elif tipo == "interrumpir":
            self.cancelar.set()
            self.buffer.clear()
            self.excedido = False
            await self.emitir(tipo="interrumpida")
        elif tipo == "finalizar":
            self._colgar("finalizada_por_el_usuario")
            return False
        elif tipo == "ping":
            await self.emitir(tipo="pong")
        else:
            await self.error("mensaje_desconocido", "Mensaje no reconocido.")
        return True

    async def correr(self) -> None:
        if not await self.autenticar():
            await self._cerrar_socket(1008)
            return
        fin = recibir = None
        try:
            await self.saludar()
            fin = asyncio.ensure_future(self._fin.wait())
            while True:
                recibir = recibir or asyncio.ensure_future(self.ws.receive())
                hechas, _ = await asyncio.wait({recibir, fin}, timeout=self.s.voz_inactividad_s, return_when=asyncio.FIRST_COMPLETED)
                if not hechas:
                    self._colgar("inactividad")
                    await self.error("inactividad", "Se cerró la llamada por inactividad.")
                    break
                if recibir not in hechas:  # otra tarea pidió cerrar (conversación caducada o cliente ausente)
                    break
                msg, recibir = recibir.result(), None
                if msg["type"] == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    self._acumular(msg["bytes"])  # si se pasa del máximo, se avisa al llegar fin_audio
                elif msg.get("text") is not None:
                    d = self._json(msg["text"])
                    if d is None:
                        await self.error("mensaje_invalido", "Se esperaba un objeto JSON de hasta 4 KB.")
                    elif not await self._comando(d):
                        break
        finally:
            for pendiente in (fin, recibir):
                if pendiente is not None and not pendiente.done():
                    pendiente.cancel()
            await self._terminar()

    async def _terminar(self) -> None:
        if self.tarea and not self.tarea.done():
            self.cancelar.set()
            try:
                await asyncio.wait_for(asyncio.shield(self.tarea), 60)  # una reserva a medias no se corta: se espera a que termine el turno
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                log.warning("voz: el último turno no terminó a tiempo al cerrar")
        normal = self.motivo_cierre in ("finalizada_por_el_usuario", "finalizada_por_el_asistente", "sesion_terminada")
        try:
            await run_in_threadpool(self._cerrar, EstadoConversacion.RESUELTA if normal else EstadoConversacion.ABANDONADA)
        except Exception:  # noqa: BLE001
            log.exception("voz: no se pudo cerrar la conversación")
        if not self.desconectado:
            await self.emitir(tipo="cerrada", motivo=self.motivo_cierre)
        await self._cerrar_socket(1000)

    async def _cerrar_socket(self, codigo: int) -> None:
        try:
            await self.ws.close(code=codigo)
        except Exception:  # noqa: BLE001 - ya estaba cerrado
            pass


@router.websocket("/ws")
async def canal_de_voz(ws: WebSocket, db: Session = Depends(get_db)) -> None:
    if not _origen_permitido(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    await SesionVoz(ws, db).correr()
