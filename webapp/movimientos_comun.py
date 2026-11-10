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

from datetime import datetime

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from formula_engine import MontoDecimal, a_decimal, redondear_moneda
from cfdi_asociados import aplicar_impuestos_cfdi
from policy_generator import generar_poliza, totales_por_naturaleza


TIPOS_POLIZA = ("ingreso", "egreso")


TAMANO_LOTE = 1000


_SQL_MOVIMIENTOS_CON_BANCO = """
    SELECT m.*, b.cuenta_contable AS cuenta_banco
    FROM movimientos m
    LEFT JOIN documentos_importados d ON d.id = m.documento_id
    LEFT JOIN bancos b ON b.id = d.banco_id
"""


class EmpresaNoEncontradaError(LookupError):
    pass


class TotalPolizasGeneradas(int):
    """Entero que conserva la lista de movimientos que no pudieron convertirse en póliza."""
    def __new__(cls, valor, descuadres=None):
        instancia = super().__new__(cls, int(valor))
        instancia.descuadres = list(descuadres or [])
        return instancia


def _lista_ids(ids) -> str:
    return ",".join(str(int(i)) for i in ids)


def _sin_guiones(cuenta) -> str:
    return str(cuenta or "").strip().replace("-", "")


def _fila_a_movimiento_dict(fila):
    afectable = fila.get("afectable_impuestos")
    return {
        "id": fila["id"],
        "empresa_id": fila["empresa_id"],
        "fecha": datetime.strptime(fila["fecha"], "%Y-%m-%d"),
        "descripcion": fila["descripcion"],
        "tipo": fila["tipo"],
        "total": redondear_moneda(fila["total"]),
        "rfc_contraparte": fila["rfc_contraparte"],
        "cuenta_bancaria_contraparte": fila["cuenta_bancaria_contraparte"],
        "numero_factura": fila["numero_factura"],
        "cuenta_banco": fila.get("cuenta_banco"),
        "tiene_iva": afectable is None or bool(afectable),
        "ret_iva": MontoDecimal("0.00"),
        "ret_isr": MontoDecimal("0.00"),
        "tipo_cambio": a_decimal(fila.get("tipo_cambio", "1.0"), "1.0"),
    }


def _cargar_config_fiscal(con, empresa_id) -> dict:
    empresa = con.execute(
        "SELECT nombre, tasa_iva, tasa_retencion_iva, tasa_retencion_isr FROM empresas WHERE id = ?",
        (empresa_id,),
    ).fetchone()
    if empresa is None:
        raise EmpresaNoEncontradaError(f"La empresa {empresa_id} no existe.")
    bancos = con.execute(
        "SELECT cuenta_contable FROM bancos WHERE empresa_id = ?", (empresa_id,)
    ).fetchall()
    return {
        "nombre": empresa["nombre"],
        "tasa_iva": a_decimal(empresa["tasa_iva"], "0.16"),
        "tasa_ret_iva": a_decimal(empresa["tasa_retencion_iva"], "0.0"),
        "tasa_ret_isr": a_decimal(empresa["tasa_retencion_isr"], "0.0"),
        "cuentas_banco": frozenset(b["cuenta_contable"] for b in bancos if b["cuenta_contable"]),
    }


def _generar_para_movimiento(mov, plantilla, nombre_regla, motivo, cfg):
    return generar_poliza(
        movimiento=mov,
        plantilla=plantilla,
        nombre_regla=nombre_regla,
        motivo_match=motivo,
        tasa_iva=cfg["tasa_iva"],
        tasa_ret_iva=cfg["tasa_ret_iva"],
        tasa_ret_isr=cfg["tasa_ret_isr"],
        cuentas_banco_empresa=cfg["cuentas_banco"],
    )


def _info_incidencia(mov, nombre_regla, motivo, tipo_incidencia, resultado=None):
    """Registro uniforme de un movimiento que no pudo clasificarse o convertirse en póliza."""
    cargos, abonos = totales_por_naturaleza(resultado.lineas) if resultado else (MontoDecimal("0.00"),) * 2
    diferencia = resultado.diferencia if resultado else MontoDecimal("0.00")
    total = redondear_moneda(mov["total"])
    parte_regla = f" con la regla '{nombre_regla}'" if nombre_regla else ""
    return {
        "tipo_incidencia": tipo_incidencia,
        "movimiento_id": mov["id"],
        "descripcion": mov.get("descripcion", ""),
        "total": total,
        "regla": nombre_regla,
        "cargos": cargos,
        "abonos": abonos,
        "diferencia": diferencia,
        "motivo": motivo,
        "mensaje": f"Movimiento #{mov['id']} ({str(mov.get('descripcion', ''))[:45]} - ${total:,.2f}){parte_regla}: {str(motivo).rstrip('.')}.",
    }


def _info_descuadre(mov, nombre_regla, resultado):
    if resultado.errores:
        motivo = "; ".join(resultado.errores)
    else:
        cargos, abonos = totales_por_naturaleza(resultado.lineas)
        motivo = (
            f"Cargos (${cargos:,.2f}) != Abonos (${abonos:,.2f}), "
            f"diferencia de ${abs(resultado.diferencia):,.2f}"
        )
    return _info_incidencia(mov, nombre_regla, motivo, "descuadre", resultado)


_construir_info_descuadre = _info_descuadre


def _aplicar_impuestos_cfdi_a_movimiento(mov, cfdis_asoc):
    aplicar_impuestos_cfdi(mov, cfdis_asoc)
