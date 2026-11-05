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
from db import get_connection

print('Adding limite_usuarios...')
try:
    conn = get_connection()
    conn.autocommit = True
    cur = conn.cursor()
    
    try:
        cur.execute('ALTER TABLE organizaciones ADD COLUMN limite_usuarios INTEGER NOT NULL DEFAULT 3;')
        print('Added limite_usuarios column')
    except Exception as e:
        print('Column might already exist:', e)
        
    cur.close()
    conn.close()
except Exception as e:
    print(f'Database error: {e}')

