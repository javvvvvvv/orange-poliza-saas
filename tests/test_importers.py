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
import unittest
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "importers")))

from excel_importer import procesar_dataframe, ConfiguracionImportacion, FORMATO_COLUMNAS_SEPARADAS

class TestImporters(unittest.TestCase):
    def test_procesar_dataframe(self):
        # Simular lo que extrae pdfplumber o read_excel
        data = {
            "Fecha": ["2026-08-20", "2026-08-21"],
            "Concepto": ["SPEI RECIBIDO", "PAGO SERVICIO LUZ"],
            "Cargos": [0, 1160],
            "Abonos": [5000, 0]
        }
        df = pd.DataFrame(data)
        
        config = ConfiguracionImportacion(
            formato=FORMATO_COLUMNAS_SEPARADAS,
            fila_encabezado=0,
            columna_fecha="Fecha",
            columnas_descripcion=["Concepto"],
            columna_ingresos="Abonos",
            columna_egresos="Cargos"
        )
        
        resultado = procesar_dataframe(df, config)
        
        self.assertEqual(resultado["filas_importadas"], 2)
        movs = resultado["movimientos"]
        self.assertEqual(movs[0]["tipo"], "ingreso")
        self.assertEqual(movs[0]["total"], 5000)
        self.assertEqual(movs[1]["tipo"], "egreso")
        self.assertEqual(movs[1]["total"], 1160)

if __name__ == "__main__":
    unittest.main()

