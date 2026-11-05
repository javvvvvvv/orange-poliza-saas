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
"""Conciliador determinístico de CFDI contra movimientos bancarios
(1 CFDI : 1 movimiento). Ver `conciliador_avanzado.py` para los casos
N:1, 1:N y pagos parciales."""

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from cfdi_importer import CFDI

_DIR = os.path.dirname(__file__)
_LLAVE = b"OC-4c-cfdi-2026"

def _cargar_mapa() -> dict:
    with open(os.path.join(_DIR, "_c4b1.dat")) as f:
        blob = f.read().strip()
    xored = base64.b85decode(blob)
    raw = bytes(b ^ _LLAVE[i % len(_LLAVE)] for i, b in enumerate(xored))
    return json.loads(raw)["cm"]

_CFG = _cargar_mapa()

@dataclass
class ConciliacionCFDI:
    cfdi: CFDI
    movimiento: dict
    confianza: int
    motivo: str

def clasificar_cfdi(cfdi: CFDI, rfc_empresa: str) -> str:
    rfc_empresa = (rfc_empresa or "").strip().upper()
    if cfdi.rfc_emisor == rfc_empresa:
        return "emitido"
    if cfdi.rfc_receptor == rfc_empresa:
        return "recibido"
    return "desconocido"

def _dentro_de_ventana(fecha_cfdi: datetime, fecha_mov, dias: int) -> bool:
    if not hasattr(fecha_mov, "date"):
        return False
    delta = abs((fecha_cfdi.date() - fecha_mov.date()).days)
    return delta <= dias

def conciliar(
    cfdis: list[CFDI],
    movimientos: list[dict],
    rfc_empresa: str,
    ventana_dias: int = None,
    tolerancia_monto: float = None,
) -> dict:
    """Cruza CFDIs contra movimientos normalizados 1 a 1. Regresa
    conciliaciones, cfdis_sin_movimiento e indices_movimientos_usados."""
    ventana_dias = _CFG["ventana"] if ventana_dias is None else ventana_dias
    tolerancia_monto = _CFG["tol"] if tolerancia_monto is None else tolerancia_monto
    conciliaciones = []
    cfdis_sin_movimiento = []
    indices_usados = set()

    for cfdi in cfdis:
        tipo = clasificar_cfdi(cfdi, rfc_empresa)
        if tipo == "desconocido":
            cfdis_sin_movimiento.append({
                "cfdi": cfdi,
                "motivo": (
                    f"Ni el RFC emisor ({cfdi.rfc_emisor}) ni el receptor "
                    f"({cfdi.rfc_receptor}) coinciden con el RFC de la "
                    f"empresa ({rfc_empresa}). ¿Es un CFDI de otra empresa?"
                ),
            })
            continue

        tipo_mov_esperado = "ingreso" if tipo == "emitido" else "egreso"
        rfc_contraparte_cfdi = cfdi.rfc_receptor if tipo == "emitido" else cfdi.rfc_emisor

        mejor_indice = None
        mejor_confianza = -1
        mejor_motivo = ""

        for i, mov in enumerate(movimientos):
            if i in indices_usados:
                continue
            if mov.get("tipo") != tipo_mov_esperado:
                continue
            if abs(mov.get("total", 0) - cfdi.total) > tolerancia_monto:
                continue
            if not _dentro_de_ventana(cfdi.fecha, mov.get("fecha"), ventana_dias):
                continue

            rfc_mov = (mov.get("rfc_contraparte") or "").strip().upper()
            if rfc_mov and rfc_mov == rfc_contraparte_cfdi:
                confianza = _CFG["cf"][0]
                motivo = (
                    f"El RFC del movimiento ({rfc_mov}) coincide con el "
                    f"{'receptor' if tipo == 'emitido' else 'emisor'} del CFDI, "
                    f"el importe coincide exactamente (${cfdi.total:,.2f}) y la "
                    f"fecha está dentro de {ventana_dias} días."
                )
            else:
                confianza = _CFG["cf"][1]
                motivo = (
                    f"El importe coincide exactamente (${cfdi.total:,.2f}) y la "
                    f"fecha está dentro de {ventana_dias} días, pero el "
                    f"movimiento no trae RFC para confirmarlo del todo."
                )

            if confianza > mejor_confianza:
                mejor_confianza = confianza
                mejor_indice = i
                mejor_motivo = motivo

        if mejor_indice is not None:
            conciliaciones.append(ConciliacionCFDI(
                cfdi=cfdi,
                movimiento=movimientos[mejor_indice],
                confianza=mejor_confianza,
                motivo=mejor_motivo,
            ))
            indices_usados.add(mejor_indice)
        else:
            cfdis_sin_movimiento.append({
                "cfdi": cfdi,
                "motivo": (
                    f"No se encontró ningún movimiento de tipo '{tipo_mov_esperado}' "
                    f"por ${cfdi.total:,.2f} dentro de {ventana_dias} días de la "
                    f"fecha del CFDI ({cfdi.fecha.strftime('%d/%m/%Y')})."
                ),
            })

    return {
        "conciliaciones": conciliaciones,
        "cfdis_sin_movimiento": cfdis_sin_movimiento,
        "indices_movimientos_usados": indices_usados,
    }

def aplicar_conciliaciones_a_movimientos(conciliaciones: list[ConciliacionCFDI]):
    for c in conciliaciones:
        cfdi = c.cfdi
        folio_visible = f"{cfdi.serie or ''}{cfdi.folio or ''}".strip() or cfdi.uuid[:8]
        c.movimiento["numero_factura"] = folio_visible
        c.movimiento["cfdi_uuid"] = cfdi.uuid
        c.movimiento["cfdi_impuestos"] = [
            {"base": t.base, "importe": t.importe, "tasa": t.tasa}
            for t in cfdi.impuestos_trasladados
        ]

