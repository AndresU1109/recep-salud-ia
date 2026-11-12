"""Canales de salida. Hoy solo CORREO por SMTP; no hay proveedor de SMS ni de WhatsApp (no se simula ninguno)."""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from app.core.config import Settings, get_settings
from app.models.enums import CanalNotificacion

log = logging.getLogger("notificaciones")


class ErrorDeEnvio(Exception):
    def __init__(self, codigo: str, reintentable: bool = True):
        super().__init__(codigo)
        self.codigo, self.reintentable = codigo, reintentable


class EmisorCorreo:
    nombre = "correo"
    canal = CanalNotificacion.EMAIL

    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()

    def configurado(self) -> bool:
        return bool(self.s.smtp_host and self.s.smtp_remitente)

    def enviar(self, destino: str, asunto: str, cuerpo: str) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = asunto, self.s.smtp_remitente, destino
        msg["Date"], msg["Message-ID"] = formatdate(localtime=False), make_msgid()
        msg.set_content(cuerpo)
        contexto = ssl.create_default_context()
        try:
            if self.s.smtp_ssl:
                servidor = smtplib.SMTP_SSL(self.s.smtp_host, self.s.smtp_port, timeout=self.s.smtp_timeout_s, context=contexto)
            else:
                servidor = smtplib.SMTP(self.s.smtp_host, self.s.smtp_port, timeout=self.s.smtp_timeout_s)
            with servidor:
                if self.s.smtp_starttls and not self.s.smtp_ssl:
                    servidor.starttls(context=contexto)
                if self.s.smtp_usuario:
                    servidor.login(self.s.smtp_usuario, self.s.smtp_clave)
                servidor.send_message(msg)
        except smtplib.SMTPAuthenticationError as e:
            raise ErrorDeEnvio("smtp_autenticacion", reintentable=False) from e  # reintentar con la misma clave solo bloquearía la cuenta
        except smtplib.SMTPRecipientsRefused as e:
            raise ErrorDeEnvio("smtp_destino_rechazado", reintentable=False) from e
        except (smtplib.SMTPException, OSError) as e:  # sin conexión, tiempo agotado, rechazo temporal...
            log.warning("smtp: fallo de envío (%s)", type(e).__name__)  # nunca el destino ni el contenido
            raise ErrorDeEnvio("smtp_no_disponible") from e


def emisor(canal: CanalNotificacion):
    """Punto único para obtener el emisor de un canal (las pruebas lo reemplazan). None si el canal no existe."""
    return EmisorCorreo() if canal == CanalNotificacion.EMAIL else None
