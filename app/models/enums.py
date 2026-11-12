from enum import Enum


class Rol(str, Enum):
    ADMINISTRADOR = "ADMINISTRADOR"
    RECEPCIONISTA = "RECEPCIONISTA"
    PROFESIONAL_SALUD = "PROFESIONAL_SALUD"
    COORDINADOR = "COORDINADOR"
    CONSULTA = "CONSULTA"


class TipoDocumento(str, Enum):
    CC = "CC"  # cédula de ciudadanía
    TI = "TI"  # tarjeta de identidad
    RC = "RC"  # registro civil
    CE = "CE"  # cédula de extranjería
    PA = "PA"  # pasaporte
    PPT = "PPT"  # permiso por protección temporal


class EstadoCita(str, Enum):
    PROGRAMADA = "PROGRAMADA"
    CONFIRMADA = "CONFIRMADA"
    CANCELADA = "CANCELADA"  # libera el cupo
    ATENDIDA = "ATENDIDA"
    INASISTENCIA = "INASISTENCIA"


class CanalCita(str, Enum):
    WEB = "WEB"
    VOZ = "VOZ"
    AGENTE = "AGENTE"
    PRESENCIAL = "PRESENCIAL"
    TELEFONO = "TELEFONO"


class AccionCita(str, Enum):
    CREADA = "CREADA"
    CONFIRMADA = "CONFIRMADA"
    REPROGRAMADA = "REPROGRAMADA"
    CANCELADA = "CANCELADA"
    ATENDIDA = "ATENDIDA"
    INASISTENCIA = "INASISTENCIA"


class ActorTipo(str, Enum):
    USUARIO = "USUARIO"
    AGENTE = "AGENTE"
    PACIENTE = "PACIENTE"
    SISTEMA = "SISTEMA"


class EstadoListaEspera(str, Enum):
    ACTIVA = "ACTIVA"
    NOTIFICADA = "NOTIFICADA"
    ASIGNADA = "ASIGNADA"
    CANCELADA = "CANCELADA"
    VENCIDA = "VENCIDA"


class TipoPolitica(str, Enum):
    CANCELACION = "CANCELACION"
    REPROGRAMACION = "REPROGRAMACION"
    INASISTENCIA = "INASISTENCIA"
    OTRA = "OTRA"


class TipoSolicitud(str, Enum):
    SOLICITUD = "SOLICITUD"
    QUEJA = "QUEJA"
    RECLAMO = "RECLAMO"
    SUGERENCIA = "SUGERENCIA"
    TRANSFERENCIA = "TRANSFERENCIA"  # pedido de hablar con una persona


class EstadoSolicitud(str, Enum):
    ABIERTA = "ABIERTA"
    EN_PROCESO = "EN_PROCESO"
    RESUELTA = "RESUELTA"
    CERRADA = "CERRADA"


class CategoriaDocumento(str, Enum):
    REGLAMENTO = "REGLAMENTO"
    PROTOCOLO = "PROTOCOLO"
    MANUAL = "MANUAL"
    PREGUNTAS_FRECUENTES = "PREGUNTAS_FRECUENTES"
    ESPECIALIDAD = "ESPECIALIDAD"
    REQUISITOS = "REQUISITOS"
    POLITICA = "POLITICA"
    SERVICIOS = "SERVICIOS"
    OTRO = "OTRO"


class EstadoDocumento(str, Enum):
    BORRADOR = "BORRADOR"
    ACTIVO = "ACTIVO"
    INACTIVO = "INACTIVO"


class CanalConversacion(str, Enum):
    TEXTO = "TEXTO"
    VOZ = "VOZ"


class EstadoConversacion(str, Enum):
    ACTIVA = "ACTIVA"
    RESUELTA = "RESUELTA"
    TRANSFERIDA = "TRANSFERIDA"
    ABANDONADA = "ABANDONADA"
    ERROR = "ERROR"


class RolMensaje(str, Enum):
    PACIENTE = "PACIENTE"
    AGENTE = "AGENTE"
    SISTEMA = "SISTEMA"


class ResultadoHerramienta(str, Enum):
    EXITO = "EXITO"
    RECHAZADA = "RECHAZADA"  # el backend la negó (permiso, regla de negocio, datos inválidos)
    ERROR = "ERROR"


class TipoNotificacion(str, Enum):
    CITA_AGENDADA = "CITA_AGENDADA"
    CITA_CANCELADA = "CITA_CANCELADA"
    CITA_REPROGRAMADA = "CITA_REPROGRAMADA"
    RECORDATORIO = "RECORDATORIO"
    CUPO_DISPONIBLE = "CUPO_DISPONIBLE"  # aviso a la lista de espera: NO reserva el cupo


class CanalNotificacion(str, Enum):
    EMAIL = "EMAIL"
    SMS = "SMS"  # sin proveedor integrado (N/D): se deja en el modelo para no migrar después


class EstadoNotificacion(str, Enum):
    PENDIENTE = "PENDIENTE"
    ENVIADA = "ENVIADA"
    FALLIDA = "FALLIDA"  # agotó los reintentos
    OMITIDA = "OMITIDA"  # no se envió a propósito (sin correo, no acepta avisos, canal sin configurar, la cita cambió)
