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
Conecta el motor de asociación (asociacion_cfdi) con la base de datos.

Solo confirma las asociaciones de nivel automático. Las sugerencias se devuelven
para que la interfaz las muestre y una persona las confirme. Antes de escribir se
revalida el saldo del CFDI y que el movimiento siga libre, con un candado por
empresa, porque entre calcular y escribir otro proceso pudo asociar lo mismo.
"""

import os
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core"))

from asociacion_cfdi import MotorAsociacion, NIVEL_AUTOMATICO, NIVEL_SUGERENCIA, TOLERANCIA_IMPORTE
from formula_engine import redondear_moneda
from db import get_connection

_SQL_CFDIS_CON_SALDO = """
    SELECT c.*, c.total - COALESCE(ap.aplicado, 0) AS saldo_pendiente
    FROM cfdis c
    LEFT JOIN (
        SELECT cfdi_id, SUM(importe_aplicado) AS aplicado
        FROM cfdi_movimiento
        WHERE confirmado = 1
        GROUP BY cfdi_id
    ) ap ON ap.cfdi_id = c.id
    WHERE c.empresa_id = ?
"""

_SQL_MOVIMIENTOS_SIN_CFDI = """
    SELECT m.*
    FROM movimientos m
    WHERE m.empresa_id = ? AND m.poliza_id IS NULL AND m.tipo IN ('ingreso', 'egreso')
      AND NOT EXISTS (
          SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1
      )
    ORDER BY m.fecha, m.id
"""

_SQL_CUENTAS_CONOCIDAS = """
    SELECT DISTINCT m.cuenta_bancaria_contraparte AS cuenta,
           CASE WHEN c.tipo = 'recibido' THEN c.rfc_emisor ELSE c.rfc_receptor END AS rfc
    FROM cfdi_movimiento cm
    JOIN movimientos m ON m.id = cm.movimiento_id
    JOIN cfdis c ON c.id = cm.cfdi_id
    WHERE cm.confirmado = 1 AND m.empresa_id = ?
      AND m.cuenta_bancaria_contraparte IS NOT NULL AND m.cuenta_bancaria_contraparte <> ''
"""


def cargar_cuentas_conocidas(con, empresa_id) -> dict:
    """RFC -> cuentas bancarias que ya se confirmaron antes para esa contraparte."""
    conocidas = defaultdict(set)
    for fila in con.execute(_SQL_CUENTAS_CONOCIDAS, (empresa_id,)).fetchall():
        if fila["rfc"]:
            conocidas[fila["rfc"]].add(fila["cuenta"])
    return dict(conocidas)


def proponer_asociaciones_empresa(con, empresa_id) -> dict:
    """Calcula propuestas para todos los movimientos sin CFDI. No escribe nada."""
    cfdis = con.execute(_SQL_CFDIS_CON_SALDO, (empresa_id,)).fetchall()
    movimientos = con.execute(_SQL_MOVIMIENTOS_SIN_CFDI, (empresa_id,)).fetchall()
    motor = MotorAsociacion(cfdis, cargar_cuentas_conocidas(con, empresa_id))
    return motor.proponer_lote(movimientos)


def _saldo_actual(con, empresa_id, cfdi_id) -> Decimal:
    fila = con.execute(
        """SELECT c.total - COALESCE((
                  SELECT SUM(importe_aplicado) FROM cfdi_movimiento WHERE cfdi_id = c.id AND confirmado = 1
              ), 0) AS saldo
           FROM cfdis c WHERE c.id = ? AND c.empresa_id = ?""",
        (cfdi_id, empresa_id),
    ).fetchone()
    return redondear_moneda(fila["saldo"]) if fila else Decimal("0.00")


def _movimiento_libre(con, empresa_id, movimiento_id) -> bool:
    movimiento = con.execute(
        "SELECT poliza_id FROM movimientos WHERE id = ? AND empresa_id = ?", (movimiento_id, empresa_id)
    ).fetchone()
    if movimiento is None or movimiento["poliza_id"] is not None:
        return False
    ya_asociado = con.execute(
        "SELECT 1 FROM cfdi_movimiento WHERE movimiento_id = ? AND confirmado = 1", (movimiento_id,)
    ).fetchone()
    return ya_asociado is None


def _aplicable(con, empresa_id, propuesta) -> bool:
    if not _movimiento_libre(con, empresa_id, propuesta.movimiento_id):
        return False
    return all(
        _saldo_actual(con, empresa_id, cfdi_id) + TOLERANCIA_IMPORTE >= importe
        for cfdi_id, importe in propuesta.aplicaciones
    )


def _guardar_asociacion(con, movimiento_id, cfdi_id, importe):
    existente = con.execute(
        "SELECT id FROM cfdi_movimiento WHERE movimiento_id = ? AND cfdi_id = ?", (movimiento_id, cfdi_id)
    ).fetchone()
    if existente:
        con.execute(
            "UPDATE cfdi_movimiento SET confirmado = 1, importe_aplicado = ? WHERE id = ?",
            (redondear_moneda(importe), existente["id"]),
        )
        return
    con.execute(
        """INSERT INTO cfdi_movimiento 
           (grupo_id, cfdi_id, movimiento_id, importe_aplicado, tipo_match, confianza, motivo, confirmado) 
           VALUES (?, ?, ?, ?, 'exacto', 100, 'Asociación automática por RFC e importe', 1)""",
        (f"auto_{movimiento_id}_{cfdi_id}", cfdi_id, movimiento_id, redondear_moneda(importe)),
    )


def confirmar_automaticas(con, empresa_id, resultados) -> list:
    """Confirma las propuestas automáticas que siguen siendo válidas. Regresa los ids de movimiento."""
    con.cursor().execute("SELECT pg_advisory_xact_lock(hashtext('asoc_cfdi_' || ?::text))", (empresa_id,))
    confirmados = []
    for resultado in resultados.values():
        automatica = next((p for p in resultado.propuestas if p.nivel == NIVEL_AUTOMATICO), None)
        if automatica is None or not _aplicable(con, empresa_id, automatica):
            continue
        for cfdi_id, importe in automatica.aplicaciones:
            _guardar_asociacion(con, automatica.movimiento_id, cfdi_id, importe)
        confirmados.append(automatica.movimiento_id)
    return confirmados


def asociar_automaticamente(empresa_id) -> dict:
    """Asocia en una sola transacción lo que es seguro y devuelve lo que requiere revisión."""
    with get_connection() as con:
        resultados = proponer_asociaciones_empresa(con, empresa_id)
        confirmados = confirmar_automaticas(con, empresa_id, resultados)

    automaticas = {
        mov_id for mov_id, r in resultados.items() if any(p.nivel == NIVEL_AUTOMATICO for p in r.propuestas)
    }
    sugerencias = [
        r for mov_id, r in resultados.items()
        if mov_id not in confirmados and any(p.nivel == NIVEL_SUGERENCIA for p in r.propuestas)
    ]
    degradadas = [
        r for r in sugerencias
        if any("también con los movimientos" in a for p in r.propuestas for a in p.advertencias)
    ]
    vetados = [r for r in resultados.values() if r.descartados]
    return {
        "asociados": len(confirmados),
        "con_sugerencias": sugerencias,
        "en_disputa": degradadas,
        "omitidas_por_cambio": sorted(automaticas - set(confirmados)),
        "vetados_por_contraparte": vetados,
    }
