# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
#
# ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
# Este código fuente y su arquitectura son propiedad intelectual exclusiva de
# JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
# distribución, modificación, ingeniería inversa, copia o uso comercial sin la
# autorización expresa y por escrito del autor. Obra protegida conforme a la
# Ley Federal del Derecho de Autor y tratados internacionales aplicables.
# ============================================================================
"""
configuracion_routes.py ya leía `base_datos_contpaqi` de la tabla `empresas`,
pero la columna nunca existió en schema.sql ni en ninguna migración — el
modo "contpaqi" de sincronización de catálogo tronaba con error de columna
inexistente en cuanto alguien lo usaba. Esta migración la agrega.

Aquí se guarda el NOMBRE INTERNO de base de datos de CONTPAQi
(ej. "ctADRIANA_MARCELA_PACHECO_MONARREZ"), no el nombre bonito de la
empresa — ver contpaqi-bridge/README.md para el porqué.
"""
from db import get_connection

print('Adding base_datos_contpaqi...')
try:
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()

    try:
        cur.execute('ALTER TABLE empresas ADD COLUMN base_datos_contpaqi TEXT;')
        print('Added base_datos_contpaqi column')
    except Exception as e:
        print('Column might already exist:', e)

    cur.close()
    conn.close()
except Exception as e:
    print(f'Database error: {e}')
