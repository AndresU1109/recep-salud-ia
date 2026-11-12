"""Importar todos los modelos aquí hace que Alembic y SQLAlchemy los vean."""
from app.models.auditoria import AuditoriaLog
from app.models.base import Base
from app.models.catalogo import (
    Consultorio, Especialidad, ExcepcionAgenda, HorarioProfesional, Profesional, Requisito, Sede,
    Servicio, Usuario, profesional_especialidad,
)
from app.models.cita import Cita, CitaHistorial, ListaEspera, RegistroInasistencia
from app.models.conversacion import Conversacion, EjecucionHerramienta, EncuestaSatisfaccion, Mensaje, MensajeFuente
from app.models.institucion import ConfiguracionIA, Institucion, PoliticaInstitucional, PreguntaFrecuente, Solicitud
from app.models.notificacion import Notificacion
from app.models.paciente import ConsentimientoDatos, Paciente
from app.models.rag import Documento, DocumentoFragmento

__all__ = [
    "AuditoriaLog", "Base", "Cita", "CitaHistorial", "ConfiguracionIA", "ConsentimientoDatos",
    "Consultorio", "Conversacion", "Documento", "DocumentoFragmento", "EjecucionHerramienta", "EncuestaSatisfaccion",
    "Especialidad", "ExcepcionAgenda", "HorarioProfesional", "Institucion", "ListaEspera", "Mensaje",
    "MensajeFuente", "Notificacion", "Paciente", "PoliticaInstitucional", "PreguntaFrecuente", "Profesional",
    "RegistroInasistencia", "Requisito", "Sede", "Servicio", "Solicitud", "Usuario",
    "profesional_especialidad",
]
