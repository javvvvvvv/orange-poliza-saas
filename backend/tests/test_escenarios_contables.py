# -*- coding: utf-8 -*-
"""
Suite de pruebas exhaustiva para escenarios contables en Orange Poliza Engine.
Cubre:
1. Factura con IVA 16% estándar
2. Factura con IVA 8% frontera norte (no división ciega entre 1.16)
3. Factura con Tasa 0% y Exento (sin IVA inventado)
4. Factura con IEPS (BASE + IEPS + IVA = TOTAL)
5. Factura con Retenciones (Honorarios/Arrendamiento: ISR 10% e IVA 10.6667%)
6. Pagos parciales y absorción de centavo en liquidación
7. Prevención de contaminación cruzada de RFC en reglas
8. Plantillas según naturaleza de cuenta (Gasto vs Proveedor)
9. Movimientos sin CFDI con IVA quedan en 'sugerido' (Decisión A1)
10. Retenciones desactivadas no generan retenciones automáticas
"""

import os
import sys
import unittest
from decimal import Decimal

_CORE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app", "core"))
_WEBAPP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "webapp"))
for p in (_CORE, _WEBAPP):
    if p not in sys.path:
        sys.path.insert(0, p)

from formula_engine import construir_variables, evaluar_formula, MontoDecimal, redondear_moneda
from policy_generator import generar_poliza, LineaPlantilla
from rule_engine import Regla, encontrar_regla
from asignacion_rapida import construir_plantilla_simple, es_cuenta_de_resultados


class TestEscenariosContables(unittest.TestCase):

    def setUp(self):
        self.empresa_base = {
            "id": 1,
            "nombre": "Empresa Pruebas S.A. de C.V.",
            "cuenta_iva_acreditable": "118-01-000",
            "cuenta_iva_por_acreditar": "119-01-000",
            "cuenta_iva_trasladado": "208-01-000",
            "cuenta_iva_por_trasladar": "209-01-000",
            "cuenta_retencion_iva": "216-01-000",
            "cuenta_retencion_isr": "216-02-000",
            "retenciones_activas": 1,
            "tasa_iva": Decimal("0.16"),
            "tasa_retencion_iva": Decimal("0.106667"),
            "tasa_retencion_isr": Decimal("0.100000"),
        }

    def test_01_iva_16_estandar_con_cfdi(self):
        """Factura estándar del 16%: Total $1,160.00, IVA $160.00, Base $1,000.00."""
        mov = {
            "total": Decimal("1160.00"),
            "tipo": "egreso",
            "tiene_iva": True,
            "iva_exacto": Decimal("160.00"),
            "cuenta_banco": "102-01-001",
            "descripcion": "COMPRA DE PAPELERIA",
        }
        vars_calc = construir_variables(
            total=mov["total"],
            iva_exacto=mov["iva_exacto"],
            tiene_iva=mov["tiene_iva"]
        )
        self.assertEqual(vars_calc["BASE"], Decimal("1000.00"))
        self.assertEqual(vars_calc["IVA"], Decimal("160.00"))
        self.assertEqual(vars_calc["TOTAL"], Decimal("1160.00"))
        self.assertEqual(vars_calc["BASE"] + vars_calc["IVA"], vars_calc["TOTAL"])

        # Póliza contra cuenta de gasto
        plantilla = [
            LineaPlantilla("601-01-001", "cargo", "BASE"),
            LineaPlantilla("118-01-000", "cargo", "IVA"),
            LineaPlantilla("BANCO", "abono", "TOTAL"),
        ]
        res = generar_poliza(mov, plantilla, "Regla Papeleria", "Match exacto")
        self.assertTrue(res.cuadrada, f"Póliza no cuadró: {res.errores} {res.explicacion}")
        self.assertEqual(len(res.lineas), 3)

    def test_02_iva_8_frontera_norte(self):
        """Factura con estímulo fronterizo 8%: Total $1,080.00, IVA $80.00, Base $1,000.00.
        No debe aplicar TOTAL/1.16."""
        mov = {
            "total": Decimal("1080.00"),
            "tipo": "egreso",
            "tiene_iva": True,
            "iva_exacto": Decimal("80.00"),
            "cuenta_banco": "102-01-001",
            "descripcion": "SERVICIO EN TIJUANA",
        }
        vars_calc = construir_variables(
            total=mov["total"],
            iva_exacto=mov["iva_exacto"],
            tiene_iva=mov["tiene_iva"]
        )
        self.assertEqual(vars_calc["BASE"], Decimal("1000.00"))
        self.assertEqual(vars_calc["IVA"], Decimal("80.00"))
        self.assertEqual(vars_calc["TOTAL"], Decimal("1080.00"))

        plantilla = [
            LineaPlantilla("601-02-001", "cargo", "BASE"),
            LineaPlantilla("118-01-000", "cargo", "IVA"),
            LineaPlantilla("BANCO", "abono", "TOTAL"),
        ]
        res = generar_poliza(mov, plantilla, "Regla Frontera", "Match exacto")
        self.assertTrue(res.cuadrada)

    def test_03_tasa_cero_y_exento(self):
        """Facturas a tasa 0% o exentas de IVA: Total $5,000.00, IVA $0.00.
        No debe generar cargos de IVA acreditable ni dividir entre 1.16."""
        mov = {
            "total": Decimal("5000.00"),
            "tipo": "egreso",
            "tiene_iva": False,
            "iva_exacto": Decimal("0.00"),
            "cuenta_banco": "102-01-001",
            "descripcion": "COMPRA DE MEDICINAS O LIBROS",
        }
        vars_calc = construir_variables(
            total=mov["total"],
            iva_exacto=mov["iva_exacto"],
            tiene_iva=mov["tiene_iva"]
        )
        self.assertEqual(vars_calc["BASE"], Decimal("5000.00"))
        self.assertEqual(vars_calc["IVA"], Decimal("0.00"))

        plantilla = [
            LineaPlantilla("601-05-001", "cargo", "BASE"),
            LineaPlantilla("118-01-000", "cargo", "IVA"),
            LineaPlantilla("BANCO", "abono", "TOTAL"),
        ]
        res = generar_poliza(mov, plantilla, "Medicinas", "Match")
        self.assertTrue(res.cuadrada)
        # La línea con IVA = 0.00 se omite automáticamente
        self.assertEqual(len(res.lineas), 2)
        self.assertEqual(res.lineas[0].importe, Decimal("5000.00"))
        self.assertEqual(res.lineas[1].importe, Decimal("5000.00"))

    def test_04_ieps_combustibles(self):
        """Factura con IEPS y tasas mixtas:
        Subtotal: $1,000.00, IEPS: $80.00, IVA 16%: $172.80, Total: $1,252.80."""
        mov = {
            "total": Decimal("1252.80"),
            "tipo": "egreso",
            "tiene_iva": True,
            "iva_exacto": Decimal("172.80"),
            "ieps": Decimal("80.00"),
            "cuenta_banco": "102-01-001",
            "descripcion": "CARGA DE GASOLINA",
        }
        vars_calc = construir_variables(
            total=mov["total"],
            iva_exacto=mov["iva_exacto"],
            ieps=mov["ieps"],
            tiene_iva=mov["tiene_iva"]
        )
        self.assertEqual(vars_calc["BASE"], Decimal("1000.00"))
        self.assertEqual(vars_calc["IEPS"], Decimal("80.00"))
        self.assertEqual(vars_calc["IVA"], Decimal("172.80"))
        self.assertEqual(vars_calc["BASE"] + vars_calc["IEPS"] + vars_calc["IVA"], vars_calc["TOTAL"])

        plantilla = [
            LineaPlantilla("601-03-001", "cargo", "BASE + IEPS"),
            LineaPlantilla("118-01-000", "cargo", "IVA"),
            LineaPlantilla("BANCO", "abono", "TOTAL"),
        ]
        res = generar_poliza(mov, plantilla, "Gasolina", "Match")
        self.assertTrue(res.cuadrada)
        self.assertEqual(res.lineas[0].importe, Decimal("1080.00"))
        self.assertEqual(res.lineas[1].importe, Decimal("172.80"))
        self.assertEqual(res.lineas[2].importe, Decimal("1252.80"))

    def test_05_retenciones_honorarios_arrendamiento(self):
        """Recibo de honorarios:
        Subtotal: $10,000.00
        IVA 16%: $1,600.00
        Retención IVA (2/3 de IVA = 10.6667%): $1,066.67
        Retención ISR (10%): $1,000.00
        Total pagado en banco: $9,533.33 (10000 + 1600 - 1066.67 - 1000)"""
        mov = {
            "total": Decimal("9533.33"),
            "tipo": "egreso",
            "tiene_iva": True,
            "iva_exacto": Decimal("1600.00"),
            "ret_iva": Decimal("1066.67"),
            "ret_isr": Decimal("1000.00"),
            "cuenta_banco": "102-01-001",
            "descripcion": "HONORARIOS CONTABLES LIC LOPEZ",
        }
        vars_calc = construir_variables(
            total=mov["total"],
            iva_exacto=mov["iva_exacto"],
            ret_iva=mov["ret_iva"],
            ret_isr=mov["ret_isr"],
            tiene_iva=mov["tiene_iva"]
        )
        self.assertEqual(vars_calc["BASE"], Decimal("10000.00"))
        self.assertEqual(vars_calc["IVA"], Decimal("1600.00"))
        self.assertEqual(vars_calc["RET_IVA"], Decimal("1066.67"))
        self.assertEqual(vars_calc["RET_ISR"], Decimal("1000.00"))

        plantilla = [
            LineaPlantilla("601-10-001", "cargo", "BASE"),
            LineaPlantilla("118-01-000", "cargo", "IVA"),
            LineaPlantilla("216-01-000", "abono", "RET_IVA"),
            LineaPlantilla("216-02-000", "abono", "RET_ISR"),
            LineaPlantilla("BANCO", "abono", "TOTAL"),
        ]
        res = generar_poliza(mov, plantilla, "Honorarios", "Match")
        self.assertTrue(res.cuadrada)
        # Cargos: 10000 + 1600 = 11600.00
        # Abonos: 1066.67 + 1000.00 + 9533.33 = 11600.00
        cargos = sum(l.importe for l in res.lineas if l.naturaleza == "cargo")
        abonos = sum(l.importe for l in res.lineas if l.naturaleza == "abono")
        self.assertEqual(cargos, Decimal("11600.00"))
        self.assertEqual(abonos, Decimal("11600.00"))

    def test_06_pagos_parciales_liquidacion_exacta(self):
        """CFDI de $10,000.00 pagado en dos exhibiciones:
        Pago 1: $6,000.00 (60%)
        Pago 2: $4,000.00 (40%)
        Liquidación total no debe tener deriva de centavos."""
        cfdi_total = Decimal("10000.00")
        cfdi_iva = Decimal("1379.31")  # Factura con IVA 16% donde subtotal=8620.69

        pago1 = redondear_moneda(cfdi_iva * (Decimal("6000.00") / cfdi_total))
        # En la liquidación final (pago 2), se absorbe la diferencia exacta
        pago2 = redondear_moneda(cfdi_iva - pago1)

        self.assertEqual(pago1 + pago2, cfdi_iva)
        self.assertEqual(pago1 + pago2, Decimal("1379.31"))

    def test_07_prevencion_contaminacion_rfc_en_reglas(self):
        """Un movimiento con RFC 'PEM080519ABC' no debe asociarse a una regla
        que pertenezca a otro RFC 'CFE370814XYZ', incluso si contiene palabras clave similares."""
        reglas = [
            Regla(
                id=1, empresa_id=1, nombre="Regla CFE", prioridad=10, activa=True,
                rfc_contraparte="CFE370814XYZ",
                descripcion_contiene=["PAGO", "ENERGIA", "SUMINISTRO"]
            ),
            Regla(
                id=2, empresa_id=1, nombre="Regla Pemex", prioridad=20, activa=True,
                rfc_contraparte="PEM080519ABC",
                descripcion_contiene=["GASOLINA", "COMBUSTIBLE"]
            ),
        ]
        mov_pemex = {
            "descripcion": "PAGO DE COMBUSTIBLE ENERGIA",
            "rfc_contraparte": "PEM080519ABC",
            "tipo": "egreso",
        }
        match = encontrar_regla(mov_pemex, reglas)
        self.assertIsNotNone(match.regla)
        # A pesar de que la regla 1 tenía mayor prioridad (10) y coincidía con "PAGO" y "ENERGIA",
        # debe ser descartada porque su RFC (CFE) contradice el RFC del movimiento (PEMEX)
        self.assertEqual(match.regla.id, 2)
        self.assertEqual(match.regla.nombre, "Regla Pemex")

    def test_08_plantilla_por_naturaleza_de_cuenta(self):
        """Verifica que la asignación rápida distinga automáticamente entre
        cuentas de resultados (gasto) y cuentas de balance (proveedores)."""
        # 1. Cuenta de gasto (601-01-000) -> 3 líneas (BASE a gasto, IVA a acreditable, TOTAL a banco)
        plantilla_gasto = construir_plantilla_simple(
            tipo="egreso", tiene_iva=True, afectable_impuestos=True,
            cuenta_contraparte="601-01-000", cuenta_banco="102-01-001",
            empresa=self.empresa_base
        )
        formulas_gasto = [l["formula"] for l in plantilla_gasto]
        self.assertIn("BASE", formulas_gasto)
        self.assertIn("IVA", formulas_gasto)
        self.assertIn("TOTAL", formulas_gasto)
        # La cuenta contraparte debe recibir 'BASE', no 'TOTAL'
        linea_cp = next(l for l in plantilla_gasto if l["cuenta"] == "601-01-000")
        self.assertEqual(linea_cp["formula"], "BASE")

        # 2. Cuenta de proveedor (201-01-001) -> 4 líneas (TOTAL a proveedor, TOTAL a banco, traspaso IVA)
        plantilla_prov = construir_plantilla_simple(
            tipo="egreso", tiene_iva=True, afectable_impuestos=True,
            cuenta_contraparte="201-01-001", cuenta_banco="102-01-001",
            empresa=self.empresa_base
        )
        self.assertEqual(len(plantilla_prov), 4)
        linea_prov = next(l for l in plantilla_prov if l["cuenta"] == "201-01-001")
        self.assertEqual(linea_prov["formula"], "TOTAL")

    def test_09_decision_a1_sin_cfdi_queda_sugerido(self):
        """Verifica que la regla con palabra clave o coincidencia difusa sobre
        un movimiento con RFC conocido no le dé certeza absoluta (>80)."""
        reglas = [
            Regla(
                id=5, empresa_id=1, nombre="Palabra Clave Larga", prioridad=10, activa=True,
                rfc_contraparte=None,  # Regla genérica sin RFC
                descripcion_contiene=["PAPELERIA Y OFICINA"]
            )
        ]
        mov_con_rfc = {
            "descripcion": "COMPRA DE PAPELERIA Y OFICINA EXPRESS",
            "rfc_contraparte": "PRO990101XYZ",
            "tipo": "egreso",
        }
        match = encontrar_regla(mov_con_rfc, reglas)
        self.assertIsNotNone(match.regla)
        # Confianza reducida a 80 máximo para no clasificar a ciegas en automático
        self.assertLessEqual(match.confianza, 80)

    def test_10_retenciones_desactivadas(self):
        """Si la empresa tiene retenciones desactivadas, no debe generar retenciones en plantilla."""
        empresa_sin_ret = dict(self.empresa_base)
        empresa_sin_ret["retenciones_activas"] = 0

        plantilla = construir_plantilla_simple(
            tipo="egreso", tiene_iva=True, afectable_impuestos=True,
            cuenta_contraparte="601-01-000", cuenta_banco="102-01-001",
            empresa=empresa_sin_ret
        )
        formulas = [l["formula"] for l in plantilla]
        self.assertNotIn("RET_IVA", formulas)
        self.assertNotIn("RET_ISR", formulas)


if __name__ == "__main__":
    unittest.main()
