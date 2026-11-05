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
import os
import psycopg2

db_host = os.environ.get('ORANGE_DB_HOST', 'localhost')
db_name = os.environ.get('ORANGE_DB_NAME', 'orange_poliza')
db_user = os.environ.get('ORANGE_DB_USER', 'orange_user')
db_pass = os.environ.get('ORANGE_DB_PASSWORD', 'orange_pass')

print('Migrating for SSO...')
try:
    conn = psycopg2.connect(host=db_host, dbname=db_name, user=db_user, password=db_pass)
    conn.autocommit = True
    cur = conn.cursor()
    
    cur.execute('ALTER TABLE usuarios ALTER COLUMN password_hash DROP NOT NULL;')
    print('Dropped NOT NULL constraint on password_hash')
    
    try:
        cur.execute('ALTER TABLE usuarios ADD CONSTRAINT usuarios_correo_key UNIQUE (correo);')
        print('Added UNIQUE constraint on correo')
    except Exception as e:
        print('Constraint already exists or invalid:', e)
        
    cur.close()
    conn.close()
    print('Migration complete.')
except Exception as e:
    print(f'Database error: {e}')

