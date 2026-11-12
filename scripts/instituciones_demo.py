"""Cuatro instituciones FICTICIAS para la demostración, además de la clínica de siempre (Clínica Santa Aurora). Cada una tiene su catálogo,
horarios, políticas, preguntas frecuentes y documentos. Todo es inventado: nombres, direcciones (calles que no existen), teléfonos 601 000 xxxx y
profesionales. Cada configuración usa las mismas claves que ``seed_demo_completo.LEGADO``."""
from datetime import time

L_V = [0, 1, 2, 3, 4]

IPS_SALUD_VITAL = {
    "nombre": "IPS Salud Vital del Norte (ficticia)", "tipo": "IPS ambulatoria", "ciudad": "Ciudad Demo",
    "descripcion": "Red ambulatoria de atención primaria: medicina general y familiar, nutrición, fisioterapia, psicología y laboratorio.",
    "sedes": {"Sede Principal Norte": {"ciudad": "Ciudad Demo", "direccion": "Carrera Ficticia 45 # 100-20", "telefono": "601 000 1001"},
              "Sede Colinas": {"ciudad": "Ciudad Demo", "direccion": "Diagonal Inventada 12 # 8-30", "telefono": "601 000 1002"}},
    "consultorios": {"Sede Principal Norte": [("Consultorio A1", "1"), ("Consultorio A2", "1"), ("Consultorio A3", "2")],
                     "Sede Colinas": [("Consultorio B1", "1"), ("Consultorio B2", "1")]},
    "especialidades": {
        "Medicina General (demo)": ("Atención de primer nivel.", [("Consulta general", 20), ("Control de seguimiento", 15)]),
        "Medicina Familiar (demo)": ("Atención integral de la familia.", [("Consulta de medicina familiar", 30)]),
        "Nutrición (demo)": ("Valoración y plan alimentario.", [("Consulta de nutrición", 30)]),
        "Fisioterapia (demo)": ("Rehabilitación física.", [("Sesión de fisioterapia", 40)]),
        "Psicología (demo)": ("Acompañamiento psicológico.", [("Sesión de psicología", 45)]),
        "Toma de muestras (demo)": ("Laboratorio clínico: toma de muestras con cita.", [("Toma de muestras", 10)]),
    },
    "profesionales": [
        ("Dr. Ricardo Meneses (ficticio)", "RM-IPS-001", "Medicina General (demo)", "Sede Principal Norte", "Consultorio A1", [(L_V, time(7), time(12), 20)]),
        ("Dra. Luisa Fernanda Arango (ficticia)", "RM-IPS-002", "Medicina General (demo)", "Sede Colinas", "Consultorio B1", [(L_V, time(13), time(18), 20)]),
        ("Dra. Beatriz Cardona (ficticia)", "RM-IPS-003", "Medicina Familiar (demo)", "Sede Principal Norte", "Consultorio A2", [([0, 2, 4], time(8), time(13), 30)]),
        ("Dra. Mónica Valencia (ficticia)", "RN-IPS-004", "Nutrición (demo)", "Sede Colinas", "Consultorio B2", [([1, 3], time(8), time(16), 30)]),
        ("Dr. Esteban Quintana (ficticio)", "RF-IPS-005", "Fisioterapia (demo)", "Sede Principal Norte", "Consultorio A3", [(L_V, time(7), time(12), 40)]),
        ("Dra. Adriana Lozano (ficticia)", "RP-IPS-006", "Psicología (demo)", "Sede Principal Norte", "Consultorio A2", [([1, 3], time(14), time(18), 45)]),
        ("Laboratorio Salud Vital (ficticio)", "LAB-IPS-007", "Toma de muestras (demo)", "Sede Principal Norte", "Consultorio A3", [(L_V, time(14), time(17), 10), ([5], time(7), time(10), 10)]),
    ],
    "requisitos": {
        "Medicina General (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad."), ("Carné de la aseguradora", "Si lo tiene.")],
        "Toma de muestras (demo)": [("Orden médica", "Lleve la orden con los exámenes solicitados."), ("Ayuno", "Algunos exámenes lo piden; confirme en la orden o llame a la sede.")],
        "Fisioterapia (demo)": [("Orden de fisioterapia", "Lleve la orden o remisión.")],
    },
    "politicas": [("CANCELACION", "Cancelación de citas IPS (demo)", {"horas_minimas_cancelar": 24}, "Las citas se cancelan con al menos 24 horas de anticipación."),
                  ("REPROGRAMACION", "Reprogramación de citas IPS (demo)", {"horas_minimas_reprogramar": 24, "maximo_reprogramaciones": 3}, "Una cita se puede reprogramar hasta 3 veces con 24 horas de anticipación."),
                  ("INASISTENCIA", "Inasistencias IPS (demo)", {"inasistencias_para_aviso": 2}, "Si no puede asistir, avise para liberar el cupo.")],
    "faqs": [
        ("Horarios", "¿Cuál es el horario de la IPS?", "De lunes a viernes de 7:00 a. m. a 6:00 p. m. La toma de muestras también atiende los sábados de 7:00 a 10:00 a. m. en la Sede Principal Norte."),
        ("Sedes", "¿Dónde están las sedes?", "Sede Principal Norte: Carrera Ficticia 45 número 100 20. Sede Colinas: Diagonal Inventada 12 número 8 30."),
        ("Laboratorio", "¿Cómo pido una cita para toma de muestras?", "Pídala por este asistente con la especialidad toma de muestras. Lleve la orden médica; algunos exámenes requieren ayuno y eso lo indica la orden."),
        ("Cambios", "¿Cómo cancelo una cita?", "Con el código de reserva, hasta 24 horas antes. Una cita se puede reprogramar hasta 3 veces."),
        ("Servicios", "¿Tienen nutrición y psicología?", "Sí. Nutrición atiende en la Sede Colinas los martes y jueves; psicología, en la Sede Principal Norte los martes y jueves en la tarde."),
    ],
    "documentos": [
        ("Guía de toma de muestras IPS (demo)", "SERVICIOS", "Laboratorio IPS (demo)", "Guía de toma de muestras\n\nLa toma de muestras se hace con cita en la Sede Principal Norte de lunes a viernes de 2:00 a 5:00 p. m. y los sábados de 7:00 a 10:00 a. m. Lleve el documento de identidad y la orden médica. El ayuno, cuando se necesita, lo indica la orden. Los resultados los entrega el laboratorio según el examen."),
        ("Horarios y sedes de la IPS (demo)", "SERVICIOS", "Administración IPS (demo)", "Horarios y sedes\n\nLa IPS tiene dos sedes: Sede Principal Norte (Carrera Ficticia 45 número 100 20) y Sede Colinas (Diagonal Inventada 12 número 8 30). Atiende de lunes a viernes de 7:00 a. m. a 6:00 p. m. Medicina general atiende en ambas sedes; medicina familiar, fisioterapia, psicología y laboratorio en la Sede Principal Norte; nutrición en la Sede Colinas."),
        ("Reglamento de citas de la IPS (demo)", "REGLAMENTO", "Gerencia IPS (demo)", "Reglamento de citas\n\nLas citas se asignan por orden de solicitud y se confirman con un código de reserva. Se cancelan con 24 horas de anticipación y se reprograman hasta 3 veces. Las inasistencias las revisa una persona del equipo antes de registrarse."),
    ],
    "bloqueo": {"sede": "Sede Colinas", "profesional": "Dra. Mónica Valencia (ficticia)"},
}

CENTRO_SONRISA = {
    "nombre": "Centro Odontológico Sonrisa Plena (ficticio)", "tipo": "Centro odontológico", "ciudad": "Ciudad Demo",
    "descripcion": "Odontología general y especializada: ortodoncia, endodoncia, periodoncia y odontopediatría.",
    "sedes": {"Sede Plaza Mayor": {"ciudad": "Ciudad Demo", "direccion": "Calle Imaginaria 7 # 20-15, local 204", "telefono": "601 000 2001"}},
    "consultorios": {"Sede Plaza Mayor": [("Unidad 1", "2"), ("Unidad 2", "2"), ("Unidad 3", "2"), ("Unidad 4", "2")]},
    "especialidades": {
        "Odontología General (demo)": ("Valoración, resinas y limpieza.", [("Valoración odontológica", 30), ("Higiene oral", 40)]),
        "Ortodoncia (demo)": ("Corrección de la posición dental.", [("Valoración de ortodoncia", 30), ("Control de ortodoncia", 20)]),
        "Endodoncia (demo)": ("Tratamientos de conducto.", [("Valoración de endodoncia", 30)]),
        "Periodoncia (demo)": ("Salud de las encías.", [("Consulta de periodoncia", 40)]),
        "Odontopediatría (demo)": ("Odontología para niños.", [("Consulta odontopediátrica", 30)]),
    },
    "profesionales": [
        ("Dra. Carolina Pineda (ficticia)", "RO-CS-001", "Odontología General (demo)", "Sede Plaza Mayor", "Unidad 1", [(L_V, time(8), time(12), 30), (L_V, time(14), time(18), 30)]),
        ("Dr. Felipe Cárdenas (ficticio)", "RO-CS-002", "Odontología General (demo)", "Sede Plaza Mayor", "Unidad 2", [([0, 2, 4], time(8), time(13), 30)]),
        ("Dra. Valeria Sánchez (ficticia)", "RO-CS-003", "Ortodoncia (demo)", "Sede Plaza Mayor", "Unidad 3", [([1, 3], time(9), time(17), 30)]),
        ("Dr. Mauricio Gaviria (ficticio)", "RO-CS-004", "Endodoncia (demo)", "Sede Plaza Mayor", "Unidad 4", [([0, 1, 3], time(8), time(12), 30)]),
        ("Dra. Tatiana Ríos (ficticia)", "RO-CS-005", "Periodoncia (demo)", "Sede Plaza Mayor", "Unidad 2", [([1, 3], time(14), time(18), 40)]),
        ("Dra. Juliana Echeverri (ficticia)", "RO-CS-006", "Odontopediatría (demo)", "Sede Plaza Mayor", "Unidad 1", [([5], time(8), time(13), 30)]),
    ],
    "requisitos": {
        "Odontología General (demo)": [("Documento de identidad", "Cédula o tarjeta de identidad."), ("Radiografías previas", "Si las tiene, llévelas.")],
        "Odontopediatría (demo)": [("Adulto responsable", "El niño asiste con un adulto que presente su documento.")],
    },
    "politicas": [("CANCELACION", "Cancelación de citas odontológicas (demo)", {"horas_minimas_cancelar": 12}, "Las citas se cancelan con al menos 12 horas de anticipación."),
                  ("REPROGRAMACION", "Reprogramación de citas odontológicas (demo)", {"horas_minimas_reprogramar": 12, "maximo_reprogramaciones": 2}, "Una cita se puede reprogramar hasta 2 veces con 12 horas de anticipación."),
                  ("INASISTENCIA", "Inasistencias odontológicas (demo)", {"inasistencias_para_aviso": 3}, "Si no puede asistir, avise con tiempo para dar el turno a otra persona.")],
    "faqs": [
        ("Horarios", "¿Cuál es el horario del centro?", "De lunes a viernes de 8:00 a. m. a 6:00 p. m. Los sábados de 8:00 a. m. a 1:00 p. m. solo atiende odontopediatría."),
        ("Sede", "¿Dónde queda el centro?", "En Calle Imaginaria 7 número 20 15, local 204, Plaza Mayor, Ciudad Demo."),
        ("Servicios", "¿Qué especialidades odontológicas hay?", "Odontología general, ortodoncia, endodoncia, periodoncia y odontopediatría."),
        ("Niños", "¿Atienden niños?", "Sí, odontopediatría atiende los sábados en la mañana. El niño debe venir con un adulto responsable."),
        ("Cambios", "¿Puedo cambiar mi cita?", "Sí, hasta 2 veces y con al menos 12 horas de anticipación. Cancelar también requiere 12 horas."),
    ],
    "documentos": [
        ("Guía de la primera valoración odontológica (demo)", "REQUISITOS", "Dirección clínica (demo)", "Guía de la primera valoración\n\nLleve su documento de identidad y, si las tiene, radiografías o historial odontológico anterior. La valoración dura unos 30 minutos. Los menores asisten con un adulto responsable. El centro no da orientación médica por el asistente virtual."),
        ("Sede, horarios y especialidades del centro (demo)", "SERVICIOS", "Administración (demo)", "Sede y horarios\n\nCentro Odontológico Sonrisa Plena: Calle Imaginaria 7 número 20 15, local 204, Plaza Mayor. Atiende de lunes a viernes de 8:00 a. m. a 6:00 p. m. y los sábados de 8:00 a. m. a 1:00 p. m. (solo odontopediatría). Especialidades: odontología general, ortodoncia, endodoncia, periodoncia y odontopediatría."),
        ("Reglamento de citas del centro (demo)", "REGLAMENTO", "Gerencia (demo)", "Reglamento de citas\n\nLas citas se confirman con un código de reserva. Se cancelan con 12 horas de anticipación y se reprograman hasta 2 veces. Una inasistencia solo se registra tras la revisión de una persona del equipo; no hay sanciones automáticas."),
    ],
    "bloqueo": {"sede": "Sede Plaza Mayor", "profesional": "Dra. Valeria Sánchez (ficticia)"},
}

HOSPITAL_SAN_GABRIEL = {
    "nombre": "Hospital Regional San Gabriel (ficticio)", "tipo": "Hospital", "ciudad": "Ciudad Demo",
    "descripcion": "Hospital de mediana complejidad. Consulta externa de cardiología, neurología, endocrinología, urología, oftalmología y medicina interna.",
    "sedes": {"Consulta Externa San Gabriel": {"ciudad": "Ciudad Demo", "direccion": "Avenida Inventada 30 # 15-60, edificio B", "telefono": "601 000 3001"}},
    "consultorios": {"Consulta Externa San Gabriel": [("Consultorio 1", "1"), ("Consultorio 2", "1"), ("Consultorio 3", "1"), ("Consultorio 4", "2"), ("Consultorio 5", "2")]},
    "especialidades": {
        "Medicina Interna (demo)": ("Adultos con enfermedades complejas.", [("Consulta de medicina interna", 30)]),
        "Cardiología (demo)": ("Corazón y circulación.", [("Consulta de cardiología", 30)]),
        "Neurología (demo)": ("Sistema nervioso.", [("Consulta de neurología", 40)]),
        "Endocrinología (demo)": ("Hormonas y metabolismo.", [("Consulta de endocrinología", 30)]),
        "Urología (demo)": ("Vías urinarias.", [("Consulta de urología", 20)]),
        "Oftalmología (demo)": ("Salud visual.", [("Consulta de oftalmología", 20)]),
    },
    "profesionales": [
        ("Dr. Gustavo Arias (ficticio)", "RM-HSG-001", "Medicina Interna (demo)", "Consulta Externa San Gabriel", "Consultorio 1", [(L_V, time(7), time(12), 30)]),
        ("Dra. Patricia Londoño (ficticia)", "RM-HSG-002", "Cardiología (demo)", "Consulta Externa San Gabriel", "Consultorio 2", [([0, 1, 3], time(8), time(16), 30)]),
        ("Dr. Hernán Bedoya (ficticio)", "RM-HSG-003", "Neurología (demo)", "Consulta Externa San Gabriel", "Consultorio 3", [([2, 4], time(8), time(14), 40)]),
        ("Dra. Claudia Betancur (ficticia)", "RM-HSG-004", "Endocrinología (demo)", "Consulta Externa San Gabriel", "Consultorio 4", [([0, 2], time(13), time(18), 30)]),
        ("Dr. Alejandro Ruiz (ficticio)", "RM-HSG-005", "Urología (demo)", "Consulta Externa San Gabriel", "Consultorio 5", [([1, 3], time(8), time(13), 20)]),
        ("Dra. Marcela Ospina (ficticia)", "RM-HSG-006", "Oftalmología (demo)", "Consulta Externa San Gabriel", "Consultorio 1", [(L_V, time(14), time(18), 20)]),
    ],
    "requisitos": {
        "Cardiología (demo)": [("Documento de identidad", "Cédula del paciente."), ("Autorización de la aseguradora", "Si la aseguradora la exige, lleve la autorización y la remisión.")],
        "Neurología (demo)": [("Remisión", "Lleve la remisión del médico tratante y los estudios previos.")],
    },
    "politicas": [("CANCELACION", "Cancelación de consulta externa (demo)", {"horas_minimas_cancelar": 48}, "La consulta externa se cancela con al menos 48 horas de anticipación."),
                  ("REPROGRAMACION", "Reprogramación de consulta externa (demo)", {"horas_minimas_reprogramar": 48, "maximo_reprogramaciones": 1}, "Una cita se puede reprogramar una sola vez, con 48 horas de anticipación."),
                  ("INASISTENCIA", "Inasistencias en consulta externa (demo)", {"inasistencias_para_aviso": 2}, "Avise si no puede asistir: los cupos de especialistas son escasos.")],
    "faqs": [
        ("Urgencias", "¿Atienden urgencias?", "El servicio de urgencias funciona las 24 horas en la entrada principal. Este asistente no gestiona urgencias: ante una emergencia llame a la línea 123 o acuda a urgencias."),
        ("Horarios", "¿Cuál es el horario de consulta externa?", "De lunes a viernes de 7:00 a. m. a 6:00 p. m., según el especialista."),
        ("Sede", "¿Dónde queda la consulta externa?", "En Avenida Inventada 30 número 15 60, edificio B, Ciudad Demo."),
        ("Requisitos", "¿Qué debo llevar a la consulta con un especialista?", "Su documento, la remisión y la autorización de su aseguradora si la exige, y los estudios previos relacionados."),
        ("Cambios", "¿Cuánto tiempo antes puedo cancelar?", "Con al menos 48 horas de anticipación. La cita se puede reprogramar una sola vez."),
    ],
    "documentos": [
        ("Guía de consulta externa (demo)", "REQUISITOS", "Consulta externa (demo)", "Guía de consulta externa\n\nLlegue 20 minutos antes de la cita. Presente su documento de identidad, la remisión del médico tratante y la autorización de su aseguradora si la exige. Lleve los estudios y exámenes previos relacionados con la consulta. Las citas con especialistas se cancelan con 48 horas de anticipación."),
        ("Servicios y ubicación del hospital (demo)", "SERVICIOS", "Administración (demo)", "Servicios y ubicación\n\nHospital Regional San Gabriel, Avenida Inventada 30 número 15 60, Ciudad Demo. Consulta externa en el edificio B: medicina interna, cardiología, neurología, endocrinología, urología y oftalmología. El servicio de urgencias funciona las 24 horas en la entrada principal."),
        ("Protocolo ante una emergencia en el hospital (demo)", "PROTOCOLO", "Dirección médica (demo)", "Protocolo ante una emergencia\n\nEl asistente virtual no atiende urgencias ni da orientación médica. Si una persona describe una situación de riesgo, se le indica llamar al 123 o acudir de inmediato al servicio de urgencias, abierto las 24 horas."),
    ],
    "bloqueo": {"sede": "Consulta Externa San Gabriel", "profesional": "Dra. Patricia Londoño (ficticia)"},
}

CENTRO_PEQUENOS_PASOS = {
    "nombre": "Centro Pediátrico Pequeños Pasos (ficticio)", "tipo": "Centro pediátrico", "ciudad": "Ciudad Demo",
    "descripcion": "Atención de niños y adolescentes: pediatría, neuropediatría, nutrición, psicología infantil y vacunación.",
    "sedes": {"Sede Jardín": {"ciudad": "Ciudad Demo", "direccion": "Carrera Soñada 22 # 50-10", "telefono": "601 000 4001"}},
    "consultorios": {"Sede Jardín": [("Sala 1", "1"), ("Sala 2", "1"), ("Sala 3", "1"), ("Sala de vacunación", "1")]},
    "especialidades": {
        "Pediatría General (demo)": ("Control y atención de niños y adolescentes.", [("Consulta pediátrica", 30), ("Control de crecimiento", 20)]),
        "Neuropediatría (demo)": ("Desarrollo y sistema nervioso infantil.", [("Consulta de neuropediatría", 40)]),
        "Nutrición Pediátrica (demo)": ("Alimentación infantil.", [("Consulta de nutrición infantil", 30)]),
        "Psicología Infantil (demo)": ("Acompañamiento psicológico para niños y familias.", [("Sesión de psicología infantil", 45)]),
        "Vacunación (demo)": ("Aplicación del esquema de vacunación.", [("Aplicación de vacunas", 15)]),
    },
    "profesionales": [
        ("Dra. Sandra Patiño (ficticia)", "RM-PP-001", "Pediatría General (demo)", "Sede Jardín", "Sala 1", [(L_V, time(8), time(12), 30)]),
        ("Dr. Néstor Villegas (ficticio)", "RM-PP-002", "Pediatría General (demo)", "Sede Jardín", "Sala 2", [(L_V, time(14), time(18), 30)]),
        ("Dra. Gloria Montoya (ficticia)", "RM-PP-003", "Neuropediatría (demo)", "Sede Jardín", "Sala 3", [([1, 3], time(8), time(14), 40)]),
        ("Dra. Natalia Correa (ficticia)", "RN-PP-004", "Nutrición Pediátrica (demo)", "Sede Jardín", "Sala 3", [([0, 2, 4], time(14), time(18), 30)]),
        ("Dr. Daniel Osorio (ficticio)", "RP-PP-005", "Psicología Infantil (demo)", "Sede Jardín", "Sala 2", [([0, 2], time(8), time(12), 45), ([5], time(8), time(12), 45)]),
        ("Enfermería de vacunación (ficticia)", "RE-PP-006", "Vacunación (demo)", "Sede Jardín", "Sala de vacunación", [(L_V, time(8), time(16), 15)]),
    ],
    "requisitos": {
        "Pediatría General (demo)": [("Documento del niño y del acompañante", "Registro civil o tarjeta de identidad del menor y documento del adulto."), ("Carné de vacunación", "Llévelo siempre.")],
        "Vacunación (demo)": [("Carné de vacunación", "Es indispensable para registrar las dosis."), ("Documento del menor", "Registro civil o tarjeta de identidad.")],
    },
    "politicas": [("CANCELACION", "Cancelación de citas pediátricas (demo)", {"horas_minimas_cancelar": 6}, "Las citas se cancelan con al menos 6 horas de anticipación."),
                  ("REPROGRAMACION", "Reprogramación de citas pediátricas (demo)", {"horas_minimas_reprogramar": 6, "maximo_reprogramaciones": 3}, "Una cita se puede reprogramar hasta 3 veces con 6 horas de anticipación."),
                  ("INASISTENCIA", "Inasistencias pediátricas (demo)", {"inasistencias_para_aviso": 3}, "Si no pueden asistir, avisen para ofrecer el cupo a otra familia.")],
    "faqs": [
        ("Horarios", "¿Cuál es el horario del centro?", "De lunes a viernes de 8:00 a. m. a 6:00 p. m. Los sábados de 8:00 a. m. a 12:00 m. solo atiende psicología infantil."),
        ("Sede", "¿Dónde queda el centro pediátrico?", "En Carrera Soñada 22 número 50 10, Ciudad Demo."),
        ("Acompañante", "¿Quién puede acompañar al niño?", "Un adulto responsable con su documento de identidad. Los adolescentes pueden venir con un adulto de su familia."),
        ("Vacunación", "¿Qué debo llevar para vacunar?", "El carné de vacunación y el documento del niño. Pida la cita en vacunación con este asistente."),
        ("Cambios", "¿Puedo cancelar o cambiar la cita?", "Sí, con al menos 6 horas de anticipación; se puede reprogramar hasta 3 veces."),
    ],
    "documentos": [
        ("Guía de la primera cita pediátrica (demo)", "REQUISITOS", "Dirección médica (demo)", "Guía de la primera cita\n\nVenga con un adulto responsable que presente su documento y el del niño (registro civil o tarjeta de identidad). Lleve el carné de vacunación y los resultados de exámenes recientes. La consulta pediátrica dura unos 30 minutos. El asistente virtual no da orientación médica."),
        ("Sede, horarios y servicios del centro pediátrico (demo)", "SERVICIOS", "Administración (demo)", "Sede y servicios\n\nCentro Pediátrico Pequeños Pasos: Carrera Soñada 22 número 50 10, Ciudad Demo. Atiende de lunes a viernes de 8:00 a. m. a 6:00 p. m.; los sábados de 8:00 a. m. a 12:00 m. solo psicología infantil. Servicios: pediatría general, neuropediatría, nutrición pediátrica, psicología infantil y vacunación."),
        ("Reglamento de citas pediátricas (demo)", "REGLAMENTO", "Gerencia (demo)", "Reglamento de citas\n\nLas citas se confirman con un código de reserva. Se cancelan con 6 horas de anticipación y se reprograman hasta 3 veces. Una inasistencia se registra solo después de que una persona del equipo la revisa."),
    ],
    "bloqueo": {"sede": "Sede Jardín", "profesional": "Dra. Gloria Montoya (ficticia)"},
}

NUEVAS = [IPS_SALUD_VITAL, CENTRO_SONRISA, HOSPITAL_SAN_GABRIEL, CENTRO_PEQUENOS_PASOS]
