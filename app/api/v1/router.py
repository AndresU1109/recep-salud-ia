from fastapi import APIRouter

from app.api.v1 import agenda, agente, auditoria, auth, catalogos, citas, config_ia, documentos, health, instituciones, notificaciones, pacientes, panel, rag, reportes, stt, tts, usuarios, voz

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(usuarios.router)
api_router.include_router(catalogos.router)
api_router.include_router(agenda.router)
api_router.include_router(pacientes.router)
api_router.include_router(instituciones.router)
api_router.include_router(notificaciones.router)
api_router.include_router(reportes.router)
api_router.include_router(auditoria.router)
api_router.include_router(citas.router)
api_router.include_router(documentos.router)
api_router.include_router(rag.router)
api_router.include_router(config_ia.router)
api_router.include_router(agente.router)
api_router.include_router(stt.router)
api_router.include_router(tts.router)
api_router.include_router(voz.router)
api_router.include_router(panel.router)
