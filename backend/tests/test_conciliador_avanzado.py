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
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "cfdi"))

from cfdi_importer import CFDI, ImpuestoTraslado
from conciliador_avanzado import conciliar_avanzado

RFC_EMPRESA = "EDP930101AB1"

def cfdi(uuid, rfc_emisor, rfc_receptor, total, fecha, serie="A", folio="1"):
    base = round(total / 1.16, 2)
    iva = round(total - base, 2)
    return CFDI(
        uuid=uuid, rfc_emisor=rfc_emisor, nombre_emisor="X", rfc_receptor=rfc_receptor,
        nombre_receptor="Y", fecha=fecha, total=total, subtotal=base, serie=serie, folio=folio,
        tipo_comprobante="I", impuestos_trasladados=[ImpuestoTraslado(base, iva, 0.16)],
    )

def mov(tipo, total, fecha, rfc=None):
    return {"tipo": tipo, "total": total, "fecha": fecha, "rfc_contraparte": rfc, "descripcion": "mov"}

def main():
    print("=" * 70)
    print("CASO 1: Cliente paga PARCIAL una factura, después liquida el resto")
    print("=" * 70)
    cfdis = [cfdi("U1", RFC_EMPRESA, "CLI001XXX", 10000.00, datetime(2026, 8, 1), folio="100")]
    movs = [
        mov("ingreso", 6000.00, datetime(2026, 8, 3), rfc="CLI001XXX"),
        mov("ingreso", 4000.00, datetime(2026, 8, 10), rfc="CLI001XXX"),
    ]
    for p in conciliar_avanzado(cfdis, movs, RFC_EMPRESA):
        print(f"  [{p.tipo_match}] confianza={p.confianza}  {p.motivo}")

    print("\n" + "=" * 70)
    print("CASO 2: Proveedor cobra VARIAS facturas en un solo pago")
    print("=" * 70)
    cfdis = [
        cfdi("U2", "PROV002XXX", RFC_EMPRESA, 5000.00, datetime(2026, 8, 1), folio="200"),
        cfdi("U3", "PROV002XXX", RFC_EMPRESA, 3200.00, datetime(2026, 8, 2), folio="201"),
        cfdi("U4", "PROV002XXX", RFC_EMPRESA, 1800.00, datetime(2026, 8, 2), folio="202"),
    ]
    movs = [mov("egreso", 10000.00, datetime(2026, 8, 5), rfc="PROV002XXX")]
    for p in conciliar_avanzado(cfdis, movs, RFC_EMPRESA):
        print(f"  [{p.tipo_match}] confianza={p.confianza}  {p.motivo}")

    print("\n" + "=" * 70)
    print("CASO 3: Comisiones bancarias del mes, facturadas juntas al final")
    print("=" * 70)
    cfdis = [cfdi("U5", "BANCO003XXX", RFC_EMPRESA, 580.00, datetime(2026, 8, 31), folio="900")]
    movs = [
        mov("egreso", 145.00, datetime(2026, 8, 5), rfc="BANCO003XXX"),
        mov("egreso", 145.00, datetime(2026, 8, 12), rfc="BANCO003XXX"),
        mov("egreso", 145.00, datetime(2026, 8, 19), rfc="BANCO003XXX"),
        mov("egreso", 145.00, datetime(2026, 8, 26), rfc="BANCO003XXX"),
    ]
    for p in conciliar_avanzado(cfdis, movs, RFC_EMPRESA):
        print(f"  [{p.tipo_match}] confianza={p.confianza}  {p.motivo}")

    print("\n" + "=" * 70)
    print("CASO 4: Movimiento sin RFC pero con importe idéntico a una factura")
    print("=" * 70)
    cfdis = [cfdi("U6", "PROV004XXX", RFC_EMPRESA, 999.99, datetime(2026, 8, 1), folio="777")]
    movs = [mov("egreso", 999.99, datetime(2026, 8, 3), rfc=None)]
    for p in conciliar_avanzado(cfdis, movs, RFC_EMPRESA):
        print(f"  [{p.tipo_match}] confianza={p.confianza}  {p.motivo}")

if __name__ == "__main__":
    main()

