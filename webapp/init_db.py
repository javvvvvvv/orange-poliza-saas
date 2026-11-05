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
import sys
from db import inicializar_db, ErrorConexionBD
import time

def init():
    max_retries = 5
    for i in range(max_retries):
        try:
            print("Intentando inicializar la base de datos...")
            inicializar_db()
            print("Base de datos inicializada correctamente.")
            return
        except ErrorConexionBD as e:
            print(f"Error conectando a BD: {e}")
            if i < max_retries - 1:
                print(f"Reintentando en 3 segundos... ({i+1}/{max_retries})")
                time.sleep(3)
            else:
                print("No se pudo conectar a la BD despues de varios intentos.")
                sys.exit(1)
        except Exception as e:
            print(f"Error inesperado inicializando BD: {e}")
            sys.exit(1)

if __name__ == '__main__':
    init()

