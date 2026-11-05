# Auditoría de Orange Poliza Engine (POLIZAGENERATOR)

Fecha: 2026-11-04. Alcance: webapp (Flask), backend (motor, importadores, exportadores), esquema SQL, Docker, pruebas.
Método: lectura de código, análisis estático (pyflakes, bandit) y ejecución real contra PostgreSQL 16 con la app levantada.

Leyenda: [E] = comprobado ejecutando el sistema. [L] = comprobado leyendo el código (no ejecutado).

## 1. Críticos (rompen el sistema, la contabilidad o la seguridad)

| # | Hallazgo | Evidencia | Arreglo |
|---|----------|-----------|---------|
| C1 | Importes guardados como `REAL` (float de 4 bytes) en PostgreSQL. | [E] 1,234,567.89 se guarda como 1,234,567.9 y 99,999,999.99 como 100,000,000.0. Aplica a `movimientos.total`, `poliza_lineas.importe`, `cfdis.total`, `cfdi_impuestos`, etc. | Cambiar a `NUMERIC(18,2)` (tasas a `NUMERIC(9,6)`) y usar `Decimal` en `formula_engine` y `policy_generator`. |
| C2 | Fuga de conexiones al pool. `clasificar_pendientes` y `generar_polizas_pendientes` abren una conexión al inicio y reasignan `con` sin cerrar la primera. | [E] Tras 17 llamadas: `PoolError: connection pool exhausted`; la app queda inservible hasta reiniciar. `movimientos_repo.py` líneas 106/122 y 229/249. También `registrar_memoria_ml_exitosa` y `log_audit` abren conexiones con otra abierta. | Una sola conexión por operación, `try/finally`, y un context manager en `db.py`. |
| C3 | El esquema no crea columnas que el código usa: `cfdis.nombre_emisor`, `cfdis.nombre_receptor`, `organizaciones.limite_usuarios`. | [E] En BD nueva, generar pólizas falla con `UndefinedColumn: c.nombre_emisor`. `cfdi_repo` también las inserta, así que importar CFDI falla igual. Solo funciona en bases donde se agregaron a mano. | Agregarlas a `schema.sql` y a `_aplicar_migraciones`. |
| C4 | Escalada de privilegios en `/mi-despacho`: el rol del nuevo usuario viene del formulario sin validar. | [E] Un admin del despacho B creó un usuario `superadmin` (ve todos los despachos). `empresas_routes.py` función `mi_despacho`. | Lista blanca de roles; un admin no puede asignar rol mayor al suyo. |
| C5 | IDOR entre despachos: se opera sobre ids de la URL sin filtrar por empresa. | [E] Usuario del despacho B borró una regla del despacho A (`regla_eliminar` -> `eliminar_regla(regla_id)`). [L] Mismo patrón: `eliminar_banco(banco_id)`, `pendiente_asignar_rapido` (lee el movimiento solo por id), `polizas_editar`, eliminar regla general, `quitar` usuario. | Todo `DELETE/UPDATE/SELECT` por id lleva `AND empresa_id = ?`. |
| C6 | Secretos dentro del zip. | [L] `.env.docker` trae `GOOGLE_CLIENT_SECRET` real; `licencia/clave.txt` trae una clave en texto; `docker-compose.yml` y `.env.docker` traen `orange_pass`; la llave HMAC de la licencia está fija en `licencia.py`; `_notas_*.md` documenta las llaves XOR de los `.dat`. | Rotar ya el secreto de Google y la clave de `clave.txt`. Sacar todo a variables de entorno. Si el zip salió de tu equipo, asume que ya está comprometido. |
| C7 | CSRF solo lee `request.form`. Todas las llamadas `fetch` con JSON mandan `X-CSRFToken` que se ignora. | [E] Editar póliza, enviar lote a CONTPAQi (`exportar_sdk_lote`) y `probar_sql` responden error. Además `@app.errorhandler(Exception)` captura los `HTTPException`: un 403 se muestra como 500. El webhook de Stripe y el `/api/v1/engine/classify` también quedan bloqueados. | Aceptar el header, exentar el webhook (validando firma) y dejar pasar `HTTPException` en el handler. |
| C8 | `movimientos_repo.actualizar_poliza` no existe. | [E] `hasattr(..., 'actualizar_poliza') == False`. La edición de pólizas en pantalla nunca funciona. | Implementarla (transacción, recalcular cuadre, validar empresa). |
| C9 | Descuadre por redondeo y IVA de CFDI ignorado. | [E] Con 500 movimientos aleatorios y una regla BASE + IVA = TOTAL, 73 (14.6 %) no se clasifican y se quedan "pendientes" sin avisar el motivo. [L] `construir_variables()` recibe `iva_exacto` y nunca lo usa, así que el IVA de la póliza sale de `TOTAL/1.16` aunque el CFDI diga otra cosa (tasas mixtas, exentos). | Calcular BASE e IVA con `Decimal` y regla de absorción de centavo; usar `iva_exacto`; mostrar al usuario el motivo del descuadre. |

## 2. Altos

- **Suscripción nunca se aplica** [L]: `estado_suscripcion` no se valida en ninguna ruta. El webhook de Stripe actualiza `fecha_vencimiento_suscripcion` (columna inexistente, el SQL falla), `STRIPE_WEBHOOK_SECRET` cae a `whsec_mock`, `billing_service` no integra Stripe, y `/billing/mock-checkout` no pide login: si se arregla el CSRF, cualquiera reactiva cualquier organización gratis.
- **Subidas sin `secure_filename`** [L]: `importacion_routes` (vista previa y CFDI) y `configuracion_routes` (catálogo) usan `archivo.filename` directo en `os.path.join`. Permite escribir fuera de la carpeta. Encadenado con `joblib.load` de `ml_models/` sería ejecución remota de código. Tampoco hay `MAX_CONTENT_LENGTH`, límite de ZIP ni limpieza de temporales.
- **Login y sesión** [L]: sin límite de intentos, sin regenerar sesión al entrar, sin flags de cookie (`SameSite`, `Secure`, `HttpOnly`). Google OAuth no verifica `email_verified` y cae a `mock_client_id` si faltan variables. `/setup` queda abierto hasta crear el primer usuario.
- **`app.run(debug=True, host='0.0.0.0')`** en `app.py` [L]: consola Werkzeug accesible por red (ejecución de código) si se arranca con `python app.py`.
- **`secret.key` con 4 workers de gunicorn** [L]: en el primer arranque cada worker puede generar una llave distinta (sesiones y CSRF inconsistentes). `SECRET_KEY` del entorno se ignora.
- **Docker** [L]: `volumes: ... external: true` con nombre fijo falla en una máquina nueva; PostgreSQL expuesto en 5432 con contraseña conocida; imagen corre como root; los volúmenes montan el código encima de la imagen.
- **`/api/v1/engine/classify`** [E]: sin login y con `NameError` (`empresa` no existe, línea 120): responde 500 siempre que una regla haga match.
- **Puente CONTPAQi (C#)** [L, parcial]: escucha en `0.0.0.0` y no vi autenticación; cualquiera en la red podría crear pólizas o cuentas. Confirmar.
- **Eliminar empresa con pólizas falla** [E]: `poliza_lineas.poliza_id` es `NOT NULL` pero con `ON DELETE SET NULL`; responde 500.
- **Duplicados**: reimportar el mismo estado de cuenta duplica movimientos (sin hash de archivo ni llave única) [L]. Enviar a CONTPAQi exporta todas las pólizas cada vez, sin marcar "enviada" [L]. Numeración con `MAX(numero)+1` sin bloqueo: dos usuarios a la vez chocan con el `UNIQUE` y uno recibe 500 [L].
- **`sql_password` de SQL Server en texto plano** en `empresas` [L], y guardar ajustes con el campo vacío lo sobrescribe.
- **`probar_sql`** [L]: cualquier usuario con acceso a la empresa hace que el servidor se conecte a un host arbitrario (escaneo interno).
- **Imports frágiles** [E]: `pdf_importer` importa `backend.app...`; sin `PYTHONPATH` a la raíz la app ni arranca. Hay `sys.path.insert` en muchos archivos y `excel_importer` se carga con dos nombres.

## 3. Medios

- N+1 y conexiones por iteración: `reglas_lista`, `pendientes` avanzado (3 conexiones por movimiento), `limpiar_polizas`, `registrar_uso` en bucle, memoria ML por movimiento.
- Modelo de datos: fechas como `TEXT`, `TIMESTAMP` sin zona, faltan índices (`movimientos.poliza_id`, `(empresa_id, estado_clasificacion)`), `usuario_empresa` solo se consulta para empresas de otro despacho (no restringe dentro del mismo).
- `float()`/`int()` sin validar (`tasa_iva`, `prioridad`) dan 500. `validar_formula` existe pero no se usa al guardar reglas, así que se guardan fórmulas inválidas.
- Mojibake: unos 30 archivos con doble codificación (`Póliza`); se guarda en `poliza_auditoria` y se muestra al usuario. Hay BOM en varios `.py` y líneas en blanco intercaladas en `app.py`/`auth.py`.
- Código muerto: más de 60 imports sin uso, `_mover_archivo_con_reintentos` copiada 5 veces, `sugerencia_para_movimiento` definida dos veces, `despacho_routes` importado pero no registrado (duplica `mi_despacho`), `check_len.py`, `GEMINI.md`.
- Archivos `.dat` ofuscados con XOR y una nota con la llave en el mismo repo: ofuscación sin valor que estorba el mantenimiento.
- Archivos largos: `movimientos_repo.py` 585 líneas, `contpaqi_txt_exporter.py` 516, `polizas_routes.py` 442.
- Estándar Orange Crew: organización por capas técnicas en vez de feature-first, `except: pass` (conciliar CFDI, borrado de temporales), `str(e)` mostrado al usuario, tres `.env.example` distintos, licencia incompleta en archivos de rutas.
- `docs/MEMORIA_TECNICA.md` tiene 16 líneas: insuficiente para un registro INDAUTOR.

## 4. Pruebas

- `backend/tests/test_formula_engine.py`: 25/25 pasan. El motor de fórmulas es seguro (usa `ast`, sin `eval`).
- `tests/test_api.py` no se puede ejecutar: importa `PlantillaPoliza` que no existe, y el caso con match fallaría por el `NameError` de C-API.
- `pytest` solo colecta 3 pruebas; el resto son scripts de demostración sin asserts.
- Cobertura nula en: multiusuario, permisos, importación, exportación TXT, pool de conexiones, esquema.

## 5. Lo que sí está bien

- Motor de fórmulas con `ast` (sin `eval`), con pruebas de inyección.
- Consultas con parámetros (los `f-string` con `IN (...)` solo usan ids enteros convertidos con `int()`).
- Aislamiento por GET entre despachos: acceder a una empresa ajena devuelve 403 [E].
- Con el esquema completo, generar pólizas y exportar el TXT de CONTPAQi funciona de punta a punta [E].
- Pool de conexiones centralizado en `db.py` y claves foráneas físicas en el DDL.

## 6. No revisado a fondo

- Que el layout del TXT coincida con lo que CONTPAQi acepta (necesita prueba contra CONTPAQi real).
- Lógica de `excel_importer`, `pdf_importer`, namespaces CFDI 4.0 y complemento de pagos 2.0 en `cfdi_importer`.
- `learning_engine`, `fuzzy_matcher`, `orange_intelligence`.
- Plantillas HTML (accesibilidad WCAG 2.2) más allá de buscar el token CSRF.
- Código C# del puente más allá de `Program.cs`.

## 7. Plan de mejoras sugerido

**Fase 1 (antes de usarlo con datos reales)**
1. Rotar secretos expuestos (Google, `clave.txt`) y sacar todo a variables de entorno.
2. Cerrar la fuga de conexiones (C2) con un context manager.
3. Migrar importes a `NUMERIC` y a `Decimal` (C1, C9).
4. Completar `schema.sql` y migraciones (C3).
5. CSRF por header, handler de `HTTPException`, `actualizar_poliza` (C7, C8).
6. Lista blanca de roles y filtro por `empresa_id` en cada operación por id (C4, C5).
7. `secure_filename`, límites de tamaño y limpieza de temporales.

**Fase 2**
8. Suscripción real: validar estado en `before_request`, webhook Stripe corregido, quitar mock en producción.
9. Límite de intentos de login, regenerar sesión, flags de cookie, `email_verified`, `/setup` protegido.
10. Docker: volumen no externo, usuario no root, BD sin puerto público, `SECRET_KEY` por entorno.
11. Marcar pólizas como enviadas, hash de archivo importado, numeración con bloqueo.
12. Suite de pruebas real (pytest + BD de prueba) para lo anterior.

**Fase 3**
13. Reorganizar feature-first, partir archivos largos, limpiar mojibake, imports y duplicados, quitar `.dat` ofuscados.
14. Auditoría de accesibilidad de plantillas y rediseño según el estándar.
15. Ampliar `MEMORIA_TECNICA.md`.
