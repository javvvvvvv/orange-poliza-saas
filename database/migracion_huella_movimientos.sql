-- ============================================================================
-- PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
-- Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
-- Organización: ORANGE CREW  |  Contacto: ILLANJAVIER9@GMAIL.COM
-- Queda estrictamente prohibida su reproducción, distribución, modificación,
-- ingeniería inversa, copia o uso comercial sin autorización expresa y por
-- escrito del autor. Obra protegida conforme a la Ley Federal del Derecho de Autor.
-- ============================================================================
-- Ejecutar ANTES de desplegar el nuevo movimientos_repo.py.
-- Los movimientos ya existentes quedan con huella NULL (no se consideran duplicados
-- entre sí); solo los importados a partir de ahora se deduplican.

ALTER TABLE movimientos ADD COLUMN IF NOT EXISTS huella TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_movimientos_empresa_huella
    ON movimientos (empresa_id, huella);

CREATE INDEX IF NOT EXISTS ix_movimientos_empresa_estado
    ON movimientos (empresa_id, estado_clasificacion);

CREATE INDEX IF NOT EXISTS ix_movimientos_poliza
    ON movimientos (poliza_id);

CREATE INDEX IF NOT EXISTS ix_polizas_empresa_envio
    ON polizas (empresa_id, enviada_contpaqi, cuadrada);

CREATE INDEX IF NOT EXISTS ix_cfdi_movimiento_movimiento
    ON cfdi_movimiento (movimiento_id, confirmado);

-- Compatibilidad de inserción con valores predeterminados para cfdi_movimiento
ALTER TABLE cfdi_movimiento ALTER COLUMN grupo_id SET DEFAULT '';
ALTER TABLE cfdi_movimiento ALTER COLUMN tipo_match SET DEFAULT 'exacto';
ALTER TABLE cfdi_movimiento ALTER COLUMN confianza SET DEFAULT 100;
ALTER TABLE cfdi_movimiento ALTER COLUMN motivo SET DEFAULT 'Asociación automática';
