# Memoria Técnica - Orange Poliza Engine

## Descripción Funcional
Motor de conciliación automática y generación de pólizas contables para CONTPAQi.
El sistema lee estados de cuenta bancarios (PDF/Excel) y comprobantes fiscales (CFDI XML), y utiliza algoritmos de coincidencia lógica, matemática y basada en reglas para asociar movimientos con facturas. Posteriormente, genera pólizas contables estructuradas e importa la información a CONTPAQi, resolviendo automáticamente UUIDs, provisiones, pagos parciales y retenciones.

## Arquitectura Lógica
El proyecto se organiza en capas separadas para garantizar mantenibilidad (KISS, DRY, SOLID):

- /webapp/routes: Capa de API y enrutamiento HTTP puro (Flask). Valida la entrada antes de tocar el negocio.
- /backend/app/core y /backend/app/cfdi: Lógica de negocio (Core). Contiene el motor de reglas (ule_engine.py), generador de pólizas (policy_generator.py) y el conciliador avanzado.
- *_repo.py / database: Capa de acceso a datos (Models/DB). Centraliza las consultas a la base de datos (PostgreSQL/SQLite) para no mezclar persistencia con vistas.
- /webapp/templates y /webapp/static: Interfaz de usuario (UI).

## Trazabilidad y Autores
Este software es propiedad exclusiva de Javier Illan Gonzalez (Orange Crew). Todo uso, modificación o distribución sin autorización está penado por la Ley Federal del Derecho de Autor (INDAUTOR).
