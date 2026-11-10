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
"""
Datos fiscales de los CFDI confirmados que ya están ligados a movimientos
bancarios: carga en lote, IVA y retenciones proporcionales al importe aplicado
y verificación de que el RFC del movimiento sea el mismo que el del CFDI.

Requiere que backend/app/core esté en sys.path (lo hace movimientos_repo).
"""

from decimal import Decimal
from typing import Optional

from formula_engine import a_decimal, redondear_moneda, MontoDecimal

TAMANO_LOTE = 1000
TOLERANCIA_COBERTURA = Decimal("0.01")

_SQL_CFDIS_POR_MOVIMIENTO = """
    SELECT cm.movimiento_id, cm.importe_aplicado,
           c.id AS cfdi_id, c.uuid, c.serie, c.folio, c.tipo AS cfdi_tipo,
           c.rfc_emisor, c.rfc_receptor, c.nombre_emisor, c.nombre_receptor,
           c.total AS cfdi_total, c.uuids_relacionados,
           COALESCE(imp.iva, 0) AS iva_cfdi,
           COALESCE(imp.ret_iva, 0) AS ret_iva_cfdi,
           COALESCE(imp.ret_isr, 0) AS ret_isr_cfdi
    FROM cfdi_movimiento cm
    JOIN cfdis c ON c.id = cm.cfdi_id
    LEFT JOIN (
        SELECT cfdi_id,
               SUM(CASE WHEN es_retencion = 0 AND tipo_impuesto = 'IVA' THEN importe ELSE 0 END) AS iva,
               SUM(CASE WHEN es_retencion = 1 AND tipo_impuesto = 'IVA' THEN importe ELSE 0 END) AS ret_iva,
               SUM(CASE WHEN es_retencion = 1 AND tipo_impuesto = 'ISR' THEN importe ELSE 0 END) AS ret_isr
        FROM cfdi_impuestos
        GROUP BY cfdi_id
    ) imp ON imp.cfdi_id = c.id
    WHERE cm.confirmado = 1 AND cm.movimiento_id IN ({ids})
    ORDER BY cm.movimiento_id, cm.importe_aplicado DESC
"""


def cargar_cfdis_por_movimiento(con, movimiento_ids) -> dict:
    """Un solo viaje a la base por lote de movimientos, sin consultas por fila."""
    ids = [int(i) for i in movimiento_ids]
    por_movimiento: dict = {}
    for inicio in range(0, len(ids), TAMANO_LOTE):
        lote = ",".join(str(i) for i in ids[inicio:inicio + TAMANO_LOTE])
        for fila in con.execute(_SQL_CFDIS_POR_MOVIMIENTO.format(ids=lote)).fetchall():
            por_movimiento.setdefault(fila["movimiento_id"], []).append(fila)
    return por_movimiento


def aplicar_impuestos_cfdi(mov: dict, cfdis: list) -> None:
    """Calcula IVA y retenciones exactos del movimiento a partir de sus CFDI.

    Cada CFDI aporta en proporción a lo que se aplicó de él (pagos parciales).
    Los CFDI con total en cero (complementos de pago) no aportan impuestos: si
    solo hay de esos, el movimiento queda con IVA estimado y así se indica.
    """
    iva = MontoDecimal("0.00")
    ret_iva = MontoDecimal("0.00")
    ret_isr = MontoDecimal("0.00")
    cubierto = MontoDecimal("0.00")
    aportaron = 0

    for cfdi in cfdis:
        total_cfdi = a_decimal(cfdi["cfdi_total"])
        if total_cfdi <= 0:
            continue
        aplicado = a_decimal(cfdi["importe_aplicado"])
        proporcion = min(Decimal("1"), aplicado / total_cfdi)
        iva += a_decimal(cfdi["iva_cfdi"]) * proporcion
        ret_iva += a_decimal(cfdi["ret_iva_cfdi"]) * proporcion
        ret_isr += a_decimal(cfdi["ret_isr_cfdi"]) * proporcion
        cubierto += aplicado
        aportaron += 1

    if not aportaron:
        return

    mov["iva_exacto"] = redondear_moneda(iva)
    if ret_iva > 0:
        mov["ret_iva"] = redondear_moneda(ret_iva)
    if ret_isr > 0:
        mov["ret_isr"] = redondear_moneda(ret_isr)
    mov["tiene_iva"] = True

    total_mov = redondear_moneda(mov["total"])
    sin_cubrir = redondear_moneda(total_mov - cubierto)
    if abs(sin_cubrir) > TOLERANCIA_COBERTURA:
        mov.setdefault("notas", []).append(
            f"Los CFDI confirmados cubren ${cubierto:,.2f} de ${total_mov:,.2f}; "
            f"los ${abs(sin_cubrir):,.2f} restantes quedan como base sin IVA. Revisar."
        )


def rfc_contraparte_cfdi(cfdi) -> str:
    """RFC del proveedor (CFDI recibido) o del cliente (CFDI emitido)."""
    rfc = cfdi["rfc_emisor"] if cfdi["cfdi_tipo"] == "recibido" else cfdi["rfc_receptor"]
    return (rfc or "").strip().upper()


def reconciliar_rfc(mov: dict, cfdis: list) -> Optional[str]:
    """Verifica que el movimiento y sus CFDI hablen de la misma contraparte.

    Si coinciden, o el movimiento no traía RFC, deja el RFC del CFDI en el
    movimiento (así las reglas por RFC pueden hacer match). Regresa el motivo
    del conflicto, o None si todo está bien. Un conflicto impide clasificar
    en automático para no mezclar clientes o proveedores.
    """
    rfcs = {rfc_contraparte_cfdi(c) for c in cfdis} - {""}
    if not rfcs:
        return None
    if len(rfcs) > 1:
        return "El movimiento tiene CFDI confirmados de varias contrapartes: " + ", ".join(sorted(rfcs)) + "."
    (rfc_cfdi,) = rfcs
    rfc_movimiento = (mov.get("rfc_contraparte") or "").strip().upper()
    if rfc_movimiento and rfc_movimiento != rfc_cfdi:
        return (
            f"El RFC del movimiento ({rfc_movimiento}) no coincide con el del "
            f"CFDI confirmado ({rfc_cfdi})."
        )
    mov["rfc_contraparte"] = rfc_cfdi
    return None
