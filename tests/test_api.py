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
import json
import sys
import os

# Asegurar imports
base_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if base_root not in sys.path:
    sys.path.insert(0, base_root)
webapp_dir = os.path.join(base_root, "webapp")
if webapp_dir not in sys.path:
    sys.path.insert(0, webapp_dir)
core_dir = os.path.join(base_root, "backend", "app", "core")
if core_dir not in sys.path:
    sys.path.insert(0, core_dir)

from app import app
from rule_engine import Regla

class TestMotorContableAPI(unittest.TestCase):
    def setUp(self):
        self.app = app.test_client()
        self.app.testing = True
        self.headers = {"X-API-Key": "orange_api_key_2026"}

    def test_api_classify_sin_auth_falla(self):
        payload = {
            "movimientos": [{"fecha": "2026-08-20T10:00:00", "descripcion": "TEST", "total": 100, "tipo": "egreso"}]
        }
        res = self.app.post("/api/v1/engine/classify", json=payload)
        self.assertEqual(res.status_code, 401)

    def test_api_classify_sin_match(self):
        payload = {
            "movimientos": [
                {"fecha": "2026-08-20T10:00:00", "descripcion": "COMPRA DESCONOCIDA", "total": 500, "tipo": "egreso"}
            ],
            "reglas": [],
            "plantillas": []
        }
        res = self.app.post("/api/v1/engine/classify", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["procesados"], 1)
        self.assertFalse(data["resultados"][0]["match"]["encontrado"])
        self.assertIsNone(data["resultados"][0]["poliza"])

    def test_api_classify_con_match(self):
        payload = {
            "movimientos": [
                {"fecha": "2026-08-20T10:00:00", "descripcion": "PAGO CFE", "total": 1160, "tipo": "egreso"}
            ],
            "reglas": [
                {
                    "id": 1,
                    "nombre": "Regla CFE",
                    "condicion_tipo": "egreso",
                    "condicion_palabras_clave": ["CFE", "LUZ"]
                }
            ],
            "plantillas": [
                {
                    "id": 1,
                    "regla_id": 1,
                    "tipo_poliza": "egreso",
                    "concepto_poliza": "Pago de luz",
                    "asientos": [
                        {"cuenta": "600-01-000", "tipo_movimiento": "cargo", "concepto": "Luz", "importe": "{subtotal}"},
                        {"cuenta": "118-01-000", "tipo_movimiento": "cargo", "concepto": "IVA Luz", "importe": "{iva}"},
                        {"cuenta": "100-01-000", "tipo_movimiento": "abono", "concepto": "Pago banco", "importe": "{total}"}
                    ]
                }
            ]
        }
        res = self.app.post("/api/v1/engine/classify", json=payload, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["procesados"], 1)
        
        match = data["resultados"][0]["match"]
        self.assertTrue(match["encontrado"])
        self.assertEqual(match["regla_id"], 1)
        
        poliza = data["resultados"][0]["poliza"]
        self.assertIsNotNone(poliza)
        self.assertTrue(poliza["cuadrada"])
        
        # Validar IVA (1160 / 1.16 = 1000 subtotal, 160 iva)
        asientos = poliza["asientos"]
        self.assertEqual(len(asientos), 3)
        self.assertEqual(asientos[0]["importe"], 1000.0)
        self.assertEqual(asientos[1]["importe"], 160.0)
        self.assertEqual(asientos[2]["importe"], 1160.0)

if __name__ == "__main__":
    unittest.main()
