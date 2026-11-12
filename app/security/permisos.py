"""Matriz de permisos por rol. Todo endpoint protegido declara el permiso que exige."""
from enum import Enum

from app.models.enums import Rol


class Permiso(str, Enum):
    USUARIOS_GESTIONAR = "usuarios:gestionar"
    PACIENTES_VER = "pacientes:ver"
    PACIENTES_GESTIONAR = "pacientes:gestionar"
    DOCUMENTO_PACIENTE_VER = "pacientes:ver_documento_completo"
    AGENDA_VER = "agenda:ver"
    AGENDA_GESTIONAR = "agenda:gestionar"  # sedes, especialidades, profesionales, horarios
    CITAS_VER = "citas:ver"
    CITAS_GESTIONAR = "citas:gestionar"
    INASISTENCIAS_VERIFICAR = "inasistencias:verificar"
    POLITICAS_GESTIONAR = "politicas:gestionar"
    DOCUMENTOS_GESTIONAR = "documentos:gestionar"
    SOLICITUDES_GESTIONAR = "solicitudes:gestionar"
    CONVERSACIONES_VER = "conversaciones:ver"
    TRANSCRIPCIONES_VER = "transcripciones:ver"  # contenido de conversaciones y grabaciones
    REPORTES_VER = "reportes:ver"
    CONFIG_IA_GESTIONAR = "config_ia:gestionar"
    AGENTE_PROBAR = "agente:probar"  # conversar con el asistente en modo prueba (no cuenta en reportes)
    AGENTE_ATENDER = "agente:atender"  # atender a un paciente con el asistente por voz desde un puesto autorizado (conversación REAL: cuenta en reportes)
    AUDITORIA_VER = "auditoria:ver"


P = Permiso

PERMISOS_POR_ROL: dict[Rol, frozenset[Permiso]] = {
    Rol.ADMINISTRADOR: frozenset(Permiso),
    Rol.COORDINADOR: frozenset({
        P.PACIENTES_VER, P.PACIENTES_GESTIONAR, P.AGENDA_VER, P.AGENDA_GESTIONAR, P.CITAS_VER,
        P.CITAS_GESTIONAR, P.INASISTENCIAS_VERIFICAR, P.POLITICAS_GESTIONAR, P.DOCUMENTOS_GESTIONAR,
        P.SOLICITUDES_GESTIONAR, P.CONVERSACIONES_VER, P.TRANSCRIPCIONES_VER, P.REPORTES_VER, P.AGENTE_PROBAR, P.AGENTE_ATENDER,
    }),
    Rol.RECEPCIONISTA: frozenset({
        P.PACIENTES_VER, P.PACIENTES_GESTIONAR, P.DOCUMENTO_PACIENTE_VER, P.AGENDA_VER, P.CITAS_VER,
        P.CITAS_GESTIONAR, P.SOLICITUDES_GESTIONAR, P.CONVERSACIONES_VER, P.AGENTE_ATENDER,
    }),
    Rol.PROFESIONAL_SALUD: frozenset({P.PACIENTES_VER, P.AGENDA_VER, P.CITAS_VER}),
    Rol.CONSULTA: frozenset({P.AGENDA_VER, P.CITAS_VER, P.REPORTES_VER}),
}


def tiene_permiso(rol: Rol, permiso: Permiso) -> bool:
    return permiso in PERMISOS_POR_ROL.get(rol, frozenset())
