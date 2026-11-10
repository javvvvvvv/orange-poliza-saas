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

from decimal import Decimal
from datetime import datetime

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from formula_engine import MontoDecimal, a_decimal, redondear_moneda
from db import get_connection


class PolizasYaEnviadasError(Exception):
    pass


def _normalizar_linea_manual(linea):
    """Regresa (cuenta, naturaleza, importe, descripcion), o None si es una fila vacía."""
    if linea.get("naturaleza"):
        naturaleza = "cargo" if str(linea["naturaleza"]).lower() == "cargo" else "abono"
        importe = redondear_moneda(linea.get("importe", 0))
    elif a_decimal(linea.get("cargo", 0)) > Decimal("0.00"):
        naturaleza = "cargo"
        importe = redondear_moneda(linea["cargo"])
    else:
        naturaleza = "abono"
        importe = redondear_moneda(linea.get("abono", linea.get("importe", 0)))

    cuenta = str(linea.get("cuenta", "")).strip()
    if not cuenta and importe == Decimal("0.00"):
        return None
    if not cuenta:
        raise ValueError("Hay una línea con importe pero sin cuenta.")
    if importe <= Decimal("0.00"):
        raise ValueError(f"La cuenta {cuenta} tiene un importe en cero o negativo.")
    return cuenta, naturaleza, importe, str(linea.get("descripcion", "")).strip()


def actualizar_poliza(poliza_id, lineas, empresa_id):
    """Reemplaza las líneas de una póliza que aún no se ha enviado y recalcula su cuadre."""
    if not lineas or not isinstance(lineas, list):
        raise ValueError("La póliza debe tener al menos una línea.")

    normalizadas = [n for n in (_normalizar_linea_manual(l) for l in lineas) if n]
    if not normalizadas:
        raise ValueError("La póliza debe tener al menos una línea con cuenta e importe.")

    with get_connection() as con:
        poliza = con.execute(
            "SELECT id, enviada_contpaqi FROM polizas WHERE id = ? AND empresa_id = ?",
            (poliza_id, empresa_id),
        ).fetchone()
        if not poliza:
            raise ValueError("Póliza no encontrada.")
        if poliza["enviada_contpaqi"]:
            raise ValueError("La póliza ya fue exportada a CONTPAQi y no se puede editar.")

        con.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (poliza_id,))
        cargos = MontoDecimal("0.00")
        abonos = MontoDecimal("0.00")
        for orden, (cuenta, naturaleza, importe, descripcion) in enumerate(normalizadas):
            if naturaleza == "cargo":
                cargos = redondear_moneda(cargos + importe)
            else:
                abonos = redondear_moneda(abonos + importe)
            con.execute(
                """INSERT INTO poliza_lineas
                   (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, 'MANUAL')""",
                (poliza_id, orden, cuenta, naturaleza, importe, descripcion),
            )

        diferencia = redondear_moneda(cargos - abonos)
        cuadrada = 1 if diferencia == Decimal("0.00") else 0
        con.execute(
            "UPDATE polizas SET cuadrada = ? WHERE id = ? AND empresa_id = ?",
            (cuadrada, poliza_id, empresa_id),
        )

        ultimo_paso = con.execute(
            "SELECT COALESCE(MAX(paso), -1) FROM poliza_auditoria WHERE poliza_id = ?",
            (poliza_id,),
        ).fetchone()[0]
        estado_txt = "Cuadrada" if cuadrada else f"Descuadrada (Diferencia: ${abs(diferencia):,.2f})"
        con.execute(
            "INSERT INTO poliza_auditoria (poliza_id, paso, detalle) VALUES (?, ?, ?)",
            (
                poliza_id,
                ultimo_paso + 1,
                f"Edición manual ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}): "
                f"Cargos ${cargos:,.2f}, Abonos ${abonos:,.2f} -> {estado_txt}",
            ),
        )

    return {
        "cuadrada": bool(cuadrada),
        "cargos": cargos,
        "abonos": abonos,
        "diferencia": diferencia,
    }


def _limpiar_polizas_en_conexion(con, empresa_id, incluir_enviadas=False):
    """Borra pólizas y libera sus movimientos. Las ya enviadas a CONTPAQi se conservan."""
    filtro = "" if incluir_enviadas else " AND enviada_contpaqi = 0"
    subconsulta = f"SELECT id FROM polizas WHERE empresa_id = ?{filtro}"
    con.execute(f"UPDATE movimientos SET poliza_id = NULL WHERE poliza_id IN ({subconsulta})", (empresa_id,))
    con.execute(f"DELETE FROM poliza_auditoria WHERE poliza_id IN ({subconsulta})", (empresa_id,))
    con.execute(f"DELETE FROM poliza_lineas WHERE poliza_id IN ({subconsulta})", (empresa_id,))
    con.execute(f"DELETE FROM polizas WHERE empresa_id = ?{filtro}", (empresa_id,))


def limpiar_polizas(empresa_id, incluir_enviadas=False):
    with get_connection() as con:
        _limpiar_polizas_en_conexion(con, empresa_id, incluir_enviadas)


def limpiar_movimientos(empresa_id, forzar=False):
    """Borra todos los movimientos de la empresa.

    Se niega si hay pólizas ya enviadas a CONTPAQi: sin los movimientos se
    pierde la trazabilidad y se podrían volver a importar y enviar.
    """
    with get_connection() as con:
        enviadas = con.execute(
            "SELECT COUNT(*) AS n FROM polizas WHERE empresa_id = ? AND enviada_contpaqi = 1",
            (empresa_id,),
        ).fetchone()["n"]
        if enviadas and not forzar:
            raise PolizasYaEnviadasError(
                f"Hay {enviadas} póliza(s) ya enviadas a CONTPAQi. Borrar los movimientos "
                f"rompe la trazabilidad; usa forzar=True solo si es intencional."
            )
        _limpiar_polizas_en_conexion(con, empresa_id, incluir_enviadas=True)
        con.execute(
            "DELETE FROM cfdi_movimiento WHERE movimiento_id IN (SELECT id FROM movimientos WHERE empresa_id = ?)",
            (empresa_id,),
        )
        con.execute("DELETE FROM movimientos WHERE empresa_id = ?", (empresa_id,))
        con.execute("DELETE FROM documentos_importados WHERE empresa_id = ?", (empresa_id,))
