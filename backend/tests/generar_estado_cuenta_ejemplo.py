# -*- coding: utf-8 -*-
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
"""Genera un estado de cuenta de ejemplo (formato columnas separadas) para
probar el pipeline completo de punta a punta."""

from datetime import datetime, timedelta
from openpyxl import Workbook

def generar_estado_cuenta_ejemplo(ruta_salida: str):
    wb = Workbook()
    ws = wb.active
    ws.title = "Movimientos"

    ws.append(["Fecha", "Concepto", "Referencia", "RFC", "Factura", "Ingresos", "Egresos"])

    base = datetime(2026, 8, 1)
    # (dias, concepto, referencia_banco, rfc, numero_factura, ingreso, egreso)
    # numero_factura = "" cuando el movimiento no trae factura (OXXO, comisiones,
    # Netflix, Uber): así se ve reflejado también en la póliza exportada.
    filas = [
        (0, "COMPRA OXXO 4821 SUC 332", "REF001", None, "", None, 348.00),
        (0, "COMPRA OXXO 7733 SUC 118", "REF002", None, "", None, 162.00),
        (1, "TRANSFERENCIA SPEI PROVEEDOR ABC HONORARIOS AGOSTO", "REF003", "ABC010101XXX", "A-1042", None, 20000.00),
        (1, "PAGO NETFLIX.COM MEXICO", "REF004", None, "", None, 249.00),
        (2, "DEPOSITO CLIENTE COMERCIALIZADORA DEL NORTE", "REF005", None, "F-8831", 58000.00, None),
        (2, "COMISION MANEJO DE CUENTA", "REF006", None, "", None, 145.00),
        (3, "COMPRA OXXO 9012 SUC 501", "REF007", None, "", None, 89.50),
        (3, "PAGO SERVICIO DESCONOCIDO XYZ 991", "REF008", None, "", None, 1500.00),
        (4, "TRANSFERENCIA SPEI PROVEEDOR ABC HONORARIOS SEPTIEMBRE", "REF009", "ABC010101XXX", "A-1078", None, 20000.00),
        (4, "DEPOSITO CLIENTE COMERCIALIZADORA DEL NORTE", "REF010", None, "F-8902", 32000.00, None),
        (5, "PAGO UBER TRIP 88213", "REF011", None, "", None, 187.00),
        (5, "PAGO UBER TRIP 44092", "REF012", None, "", None, 213.50),
    ]

    for dias, concepto, referencia, rfc, factura, ingreso, egreso in filas:
        fecha = base + timedelta(days=dias)
        ws.append([fecha.strftime("%d/%m/%Y"), concepto, referencia, rfc, factura, ingreso, egreso])

    wb.save(ruta_salida)
    return ruta_salida

if __name__ == "__main__":
    generar_estado_cuenta_ejemplo("/home/claude/orange-poliza-engine/backend/tests/estado_cuenta_ejemplo.xlsx")
    print("Generado.")

