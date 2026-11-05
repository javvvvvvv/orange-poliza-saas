-- =====================================================================
-- Orange Poliza Engine - Esquema de base de datos (PostgreSQL)
-- Todas las tablas usan CREATE TABLE IF NOT EXISTS: este archivo se
-- ejecuta en cada arranque del servidor (inicializar_db()) para poder
-- crear tablas nuevas sin romper una base ya existente. El orden de
-- las tablas importa: a diferencia de SQLite, PostgreSQL exige que la
-- tabla referenciada por una FOREIGN KEY ya exista al momento del
-- CREATE TABLE.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Organizaciones, usuarios y permisos (multiusuario real)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS configuracion (
    clave           TEXT PRIMARY KEY,
    valor           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS organizaciones (
    id              SERIAL PRIMARY KEY,
    nombre          TEXT NOT NULL,
    limite_usuarios INTEGER NOT NULL DEFAULT 5,
    estado_suscripcion TEXT NOT NULL DEFAULT 'activa', -- 'activa' | 'suspendida'
    fecha_vencimiento TEXT, -- ISO Date o NULL para lifetime
    creada_en       TEXT NOT NULL DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS usuarios (
    id              SERIAL PRIMARY KEY,
    organizacion_id INTEGER NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
    nombre          TEXT NOT NULL,
    usuario         TEXT NOT NULL UNIQUE,   -- nombre de usuario para iniciar sesión (no correo)
    correo          TEXT UNIQUE,            -- requerido para sso, unique
    password_hash   TEXT,
    rol_global      TEXT NOT NULL DEFAULT 'usuario', -- 'admin' | 'usuario'
    activo          INTEGER NOT NULL DEFAULT 1,
    creado_en       TEXT NOT NULL DEFAULT (NOW()::text)
);

CREATE TABLE IF NOT EXISTS empresas (
    id              SERIAL PRIMARY KEY,
    organizacion_id INTEGER NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
    nombre          TEXT NOT NULL,
    rfc             TEXT,
    tasa_iva        NUMERIC(9,6) NOT NULL DEFAULT 0.16,
    tasa_retencion_iva NUMERIC(9,6) NOT NULL DEFAULT 0.0,
    tasa_retencion_isr NUMERIC(9,6) NOT NULL DEFAULT 0.0,
    activa          INTEGER NOT NULL DEFAULT 1,
    creada_en       TEXT NOT NULL DEFAULT (NOW()::text),

    -- Configuración de IVA: a qué cuenta del catálogo va cada movimiento de IVA.
    cuenta_iva_acreditable          TEXT,
    cuenta_iva_por_acreditar        TEXT,
    cuenta_iva_trasladado           TEXT,
    cuenta_iva_por_trasladar        TEXT,
    cuenta_complementaria_ingresos  TEXT,   -- para movimientos en dólares
    cuenta_complementaria_egresos   TEXT,
    cuenta_dif_cambiaria            TEXT,
    retenciones_activas             INTEGER NOT NULL DEFAULT 0,

    -- Nombre interno de base de datos en CONTPAQi (ej. "ctNOMBRE_EMPRESA"),
    -- NO el nombre bonito que ve el usuario. Lo usan el bridge y la
    -- sincronizacion de catalogo para abrir la empresa correcta.
    base_datos_contpaqi             TEXT,
    sql_servidor                    TEXT,
    sql_usuario                     TEXT,
    sql_password                    TEXT,
    sql_base_datos                  TEXT
);

-- Qué usuario puede acceder a qué empresa y con qué rol
CREATE TABLE IF NOT EXISTS usuario_empresa (
    usuario_id      INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
    empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    rol             TEXT NOT NULL DEFAULT 'editor', -- 'admin' | 'editor' | 'capturista' | 'lector'
    PRIMARY KEY (usuario_id, empresa_id)
);

-- ---------------------------------------------------------------------
-- Catálogo de cuentas (por empresa)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cuentas_catalogo (
    id              SERIAL PRIMARY KEY,
    empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    cuenta          TEXT NOT NULL,
    descripcion     TEXT NOT NULL,
    cuenta_padre    TEXT,
    nivel           INTEGER,
    naturaleza      TEXT,          -- 'deudora' | 'acreedora'
    afectable       INTEGER NOT NULL DEFAULT 1,
    codigo_agrupador TEXT,
    UNIQUE (empresa_id, cuenta)
);

-- ---------------------------------------------------------------------
-- Bancos y estados de cuenta importados
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS bancos (
    id              SERIAL PRIMARY KEY,
    empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nombre          TEXT NOT NULL,          -- 'BBVA', 'Santander', etc.
    cuenta_contable TEXT NOT NULL,          -- cuenta del catálogo que representa este banco
    moneda          TEXT NOT NULL DEFAULT 'MXN'
);

CREATE TABLE IF NOT EXISTS documentos_importados (
    id              SERIAL PRIMARY KEY,
    empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    banco_id        INTEGER REFERENCES bancos(id) ON DELETE SET NULL,
    nombre_archivo  TEXT NOT NULL,
    hash_sha256     TEXT,              -- Hash SHA-256 para prevenir reimportaciones duplicadas
    ruta_archivo    TEXT,              -- copia persistida, para regenerar el marcado
    nombre_hoja     TEXT,
    tipo_archivo    TEXT NOT NULL,     -- 'excel' | 'pdf_digital' | 'pdf_escaneado' | 'csv' | 'xml'
    importado_por   INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    importado_en    TEXT NOT NULL DEFAULT (NOW()::text),
    estado          TEXT NOT NULL DEFAULT 'pendiente' -- 'pendiente'|'procesado'|'error'
);

-- ---------------------------------------------------------------------
-- Motor de reglas (antes de "movimientos" y "polizas": ambas las referencian)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS reglas (
    id                          SERIAL PRIMARY KEY,
    empresa_id                  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    nombre                      TEXT NOT NULL,
    prioridad                   INTEGER NOT NULL DEFAULT 100,
    activa                      INTEGER NOT NULL DEFAULT 1,

    -- condiciones (cualquiera puede ser NULL = no se usa esa condición)
    rfc_contraparte             TEXT,
    cuenta_bancaria_contraparte TEXT,
    descripcion_exacta          TEXT,
    tipo_movimiento             TEXT,     -- 'ingreso' | 'egreso' | NULL (cualquiera)

    creada_por                  INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    creada_en                   TEXT NOT NULL DEFAULT (NOW()::text),
    veces_aplicada               INTEGER NOT NULL DEFAULT 0
);

-- Palabras clave de "descripción contiene" (una regla puede tener varias)
CREATE TABLE IF NOT EXISTS regla_palabras_clave (
    id          SERIAL PRIMARY KEY,
    regla_id    INTEGER NOT NULL REFERENCES reglas(id) ON DELETE CASCADE,
    palabra     TEXT NOT NULL
);

-- Las líneas que la regla genera en la póliza (la "plantilla")
CREATE TABLE IF NOT EXISTS plantilla_movimientos (
    id              SERIAL PRIMARY KEY,
    regla_id        INTEGER NOT NULL REFERENCES reglas(id) ON DELETE CASCADE,
    orden           INTEGER NOT NULL DEFAULT 0,
    cuenta          TEXT NOT NULL,
    naturaleza      TEXT NOT NULL,     -- 'cargo' | 'abono'
    formula         TEXT NOT NULL,     -- ej. "TOTAL / 1.16"
    descripcion_linea TEXT             -- opcional, si no se usa la del movimiento
);

-- Líneas adicionales que se agregan automáticamente a TODOS los
-- movimientos de ingreso o egreso al usar el asistente rápido, más
-- allá del básico banco+contraparte+IVA (ej. "Ingresos por aplicar").
CREATE TABLE IF NOT EXISTS reglas_generales (
    id                  SERIAL PRIMARY KEY,
    empresa_id          INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    tipo_movimiento     TEXT NOT NULL,  -- 'ingreso' | 'egreso'
    orden               INTEGER NOT NULL DEFAULT 0,
    cuenta              TEXT NOT NULL,
    naturaleza          TEXT NOT NULL,  -- 'cargo' | 'abono'
    formula             TEXT NOT NULL,
    descripcion_linea   TEXT
);

-- ---------------------------------------------------------------------
-- Pólizas generadas
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS polizas (
    id              SERIAL PRIMARY KEY,
    empresa_id      INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    tipo            TEXT NOT NULL,        -- 'ingreso' | 'egreso' | 'diario'
    numero          INTEGER NOT NULL,
    fecha           TEXT NOT NULL,
    referencia      TEXT,
    concepto        TEXT,
    cuadrada        INTEGER NOT NULL DEFAULT 0,
    enviada_contpaqi INTEGER NOT NULL DEFAULT 0, -- 1 = enviada al SDK o exportada
    fecha_envio     TEXT,
    aprobada_por    INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    generada_en     TEXT NOT NULL DEFAULT (NOW()::text),
    UNIQUE (empresa_id, tipo, numero)
);

CREATE TABLE IF NOT EXISTS poliza_lineas (
    id              SERIAL PRIMARY KEY,
    poliza_id       INTEGER NOT NULL REFERENCES polizas(id) ON DELETE CASCADE,
    orden           INTEGER NOT NULL,
    cuenta          TEXT NOT NULL,
    naturaleza      TEXT NOT NULL,     -- 'cargo' | 'abono'
    importe         NUMERIC(18,2) NOT NULL,
    descripcion     TEXT,
    formula_usada   TEXT
);

-- Auditoría: guarda por qué se generó cada póliza, para el "visor de explicación"
CREATE TABLE IF NOT EXISTS poliza_auditoria (
    id              SERIAL PRIMARY KEY,
    poliza_id       INTEGER NOT NULL REFERENCES polizas(id) ON DELETE CASCADE,
    paso            INTEGER NOT NULL,
    detalle         TEXT NOT NULL
);

-- ---------------------------------------------------------------------
-- Movimientos normalizados (sin importar si vinieron de PDF/Excel/XML)
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS movimientos (
    id                          SERIAL PRIMARY KEY,
    empresa_id                  INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    documento_id                INTEGER REFERENCES documentos_importados(id) ON DELETE SET NULL,
    fecha                       TEXT NOT NULL,
    descripcion                 TEXT NOT NULL,
    descripcion_normalizada     TEXT NOT NULL,
    tipo                        TEXT NOT NULL,   -- 'ingreso' | 'egreso'
    total                       NUMERIC(18,2) NOT NULL,
    moneda                      TEXT NOT NULL DEFAULT 'MXN',
    tipo_cambio                 NUMERIC(18,6) NOT NULL DEFAULT 1.0,
    rfc_contraparte             TEXT,
    cuenta_bancaria_contraparte TEXT,
    referencia_bancaria         TEXT,   -- folio/referencia que trae el banco (informativo)
    numero_factura              TEXT,   -- CFDI/factura real; usado como Referencia al exportar.
                                          -- NULL o '' si el movimiento no tiene factura (ej. OXXO, comisiones).
    fila_original               INTEGER, -- índice de fila en el Excel original (para marcarlo después)
    afectable_impuestos         INTEGER, -- 1 = sí (normal), 0 = no (ej. nómina: solo banco + cuenta, sin IVA)

    -- resultado de aplicar el motor de reglas
    regla_id                    INTEGER REFERENCES reglas(id) ON DELETE SET NULL,
    confianza_clasificacion     INTEGER,     -- 0-100
    estado_clasificacion        TEXT NOT NULL DEFAULT 'pendiente',
        -- 'pendiente' | 'automatico' | 'sugerido_ia' | 'manual' | 'revisado'

    poliza_id                   INTEGER REFERENCES polizas(id) ON DELETE SET NULL
);

-- ---------------------------------------------------------------------
-- CFDI (facturas electrónicas emitidas y recibidas) y su conciliación
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS cfdis (
    id                  SERIAL PRIMARY KEY,
    empresa_id          INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    uuid                TEXT NOT NULL,
    tipo                TEXT NOT NULL,       -- 'emitido' | 'recibido'
    rfc_emisor          TEXT NOT NULL,
    nombre_emisor       TEXT,
    rfc_receptor        TEXT NOT NULL,
    nombre_receptor     TEXT,
    fecha               TEXT NOT NULL,
    total               NUMERIC(18,2) NOT NULL,
    subtotal            NUMERIC(18,2) NOT NULL,
    serie               TEXT,
    folio               TEXT,
    uuids_relacionados  TEXT,
    archivo_origen      TEXT,
    importado_en        TEXT NOT NULL DEFAULT (NOW()::text),
    UNIQUE (empresa_id, uuid)
);

CREATE TABLE IF NOT EXISTS cfdi_impuestos (
    id              SERIAL PRIMARY KEY,
    cfdi_id         INTEGER NOT NULL REFERENCES cfdis(id) ON DELETE CASCADE,
    base            NUMERIC(18,2) NOT NULL,
    importe         NUMERIC(18,2) NOT NULL,
    tasa            NUMERIC(9,6) NOT NULL,
    es_retencion    INTEGER NOT NULL DEFAULT 0,  -- 0 = traslado normal, 1 = retención
    tipo_impuesto   TEXT NOT NULL DEFAULT 'IVA'  -- 'IVA' | 'ISR'; solo relevante si es_retencion=1
);

-- La conciliación en sí: qué CFDI quedó asociado a qué movimiento, con
-- qué confianza y por qué (para el visor de auditoría). Es muchos-a-
-- muchos porque una factura puede cubrir varios movimientos (pagos
-- parciales) y un movimiento puede cubrir varias facturas (pago
-- combinado). `grupo_id` agrupa las filas que pertenecen a la misma
-- propuesta de conciliación, para confirmarlas/rechazarlas juntas.
CREATE TABLE IF NOT EXISTS cfdi_movimiento (
    id              SERIAL PRIMARY KEY,
    grupo_id        TEXT NOT NULL,
    cfdi_id         INTEGER NOT NULL REFERENCES cfdis(id) ON DELETE CASCADE,
    movimiento_id   INTEGER NOT NULL REFERENCES movimientos(id) ON DELETE CASCADE,
    importe_aplicado NUMERIC(18,2) NOT NULL,
    tipo_match      TEXT NOT NULL,   -- 'exacto'|'combinado_facturas'|'combinado_movimientos'|'pago_parcial'|'monto_sin_rfc'
    confianza       INTEGER NOT NULL,
    motivo          TEXT NOT NULL,
    confirmado      INTEGER NOT NULL DEFAULT 0,
    conciliado_en   TEXT NOT NULL DEFAULT (NOW()::text),
    UNIQUE (cfdi_id, movimiento_id)
);

-- Índices de Rendimiento
CREATE INDEX IF NOT EXISTS idx_movimientos_empresa ON movimientos(empresa_id);
CREATE INDEX IF NOT EXISTS idx_movimientos_estado ON movimientos(estado_clasificacion);
CREATE INDEX IF NOT EXISTS idx_movimientos_empresa_estado ON movimientos(empresa_id, estado_clasificacion);
CREATE INDEX IF NOT EXISTS idx_movimientos_poliza_id ON movimientos(poliza_id);
CREATE INDEX IF NOT EXISTS idx_polizas_empresa ON polizas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_polizas_fecha ON polizas(fecha);
CREATE INDEX IF NOT EXISTS idx_reglas_empresa ON reglas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_cfdis_rfc_emisor ON cfdis(rfc_emisor);
CREATE INDEX IF NOT EXISTS idx_cfdis_rfc_receptor ON cfdis(rfc_receptor);

CREATE TABLE IF NOT EXISTS memoria_ml (
    id SERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    descripcion_limpia TEXT NOT NULL,
    tipo TEXT NOT NULL,
    regla_asignada_id INTEGER NOT NULL REFERENCES reglas(id) ON DELETE CASCADE,
    frecuencia INTEGER NOT NULL DEFAULT 1,
    ultima_vez TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (empresa_id, descripcion_limpia, tipo, regla_asignada_id)
);

CREATE INDEX IF NOT EXISTS idx_poliza_lineas_poliza_id ON poliza_lineas(poliza_id);
CREATE INDEX IF NOT EXISTS idx_cfdi_movimiento_cfdi ON cfdi_movimiento(cfdi_id);
CREATE INDEX IF NOT EXISTS idx_cfdi_movimiento_movimiento ON cfdi_movimiento(movimiento_id);
CREATE INDEX IF NOT EXISTS idx_memoria_ml_empresa ON memoria_ml(empresa_id);

CREATE TABLE IF NOT EXISTS auditoria_despacho (
    id SERIAL PRIMARY KEY,
    organizacion_id INTEGER NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
    usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
    accion TEXT NOT NULL,
    detalle TEXT,
    fecha TEXT NOT NULL DEFAULT (NOW()::text)
);
