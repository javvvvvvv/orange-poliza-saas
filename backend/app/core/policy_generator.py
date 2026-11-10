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
Generador de pólizas para Orange Poliza Engine (aritmética exacta Decimal).

Recibe un movimiento normalizado y la plantilla contable de la regla que
hizo match, y devuelve las líneas calculadas, si la póliza cuadra y una
explicación paso a paso para el visor de auditoría.

Una póliza solo se considera cuadrada si, además de Debe = Haber, tiene
líneas, tiene cargos y abonos, la cuenta de banco está resuelta y el lado del
banco corresponde al tipo de movimiento (ingreso = cargo, egreso = abono).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

try:
    from .formula_engine import (
        evaluar_formula,
        construir_variables,
        FormulaError,
        MontoDecimal,
        redondear_moneda,
    )
except ImportError:  # se ejecuta como script suelto (ej. demo_end_to_end.py)
    from formula_engine import (
        evaluar_formula,
        construir_variables,
        FormulaError,
        MontoDecimal,
        redondear_moneda,
    )

TOKEN_BANCO = "BANCO"
TIPOS_MOVIMIENTO = ("ingreso", "egreso")
CERO = Decimal("0.00")
# Con n fórmulas redondeadas por separado el error máximo es de n/2 centavos.
TOLERANCIA_REDONDEO = Decimal("0.02")


@dataclass
class LineaPlantilla:
    """Una línea de la plantilla contable de una regla (`plantilla_movimientos`)."""
    cuenta: str
    naturaleza: str  # 'cargo' | 'abono'
    formula: str      # ej. "BASE", "IVA", "TOTAL"
    descripcion_linea: Optional[str] = None  # si es None, se usa la descripción del movimiento


@dataclass
class LineaPoliza:
    """Una línea ya calculada, lista para exportar."""
    cuenta: str
    naturaleza: str
    importe: Decimal
    descripcion: str
    formula_usada: str
    es_banco: bool = False


@dataclass
class ResultadoPoliza:
    lineas: list = field(default_factory=list)
    cuadrada: bool = False
    diferencia: Decimal = field(default_factory=lambda: MontoDecimal("0.00"))
    explicacion: list = field(default_factory=list)
    errores: list = field(default_factory=list)


def _normalizar_cuenta(cuenta) -> str:
    return str(cuenta or "").strip().replace("-", "").upper()


def totales_por_naturaleza(lineas) -> tuple[MontoDecimal, MontoDecimal]:
    cargos = sum((l.importe for l in lineas if l.naturaleza == "cargo"), MontoDecimal("0.00"))
    abonos = sum((l.importe for l in lineas if l.naturaleza == "abono"), MontoDecimal("0.00"))
    return redondear_moneda(cargos), redondear_moneda(abonos)


def _absorber_diferencia(lineas, diferencia) -> Optional[LineaPoliza]:
    """Mueve el centavo de redondeo a una línea que no sea el banco ni TOTAL.

    El importe del banco debe coincidir siempre con el movimiento real, por eso
    nunca se toca. Si ninguna línea puede absorberlo, la póliza queda descuadrada
    y se reporta.
    """
    ajustables = []
    for linea in lineas:
        if linea.es_banco or linea.formula_usada.strip().upper() == "TOTAL":
            continue
        # diferencia = cargos - abonos: un cargo baja, un abono sube
        delta = -diferencia if linea.naturaleza == "cargo" else diferencia
        nuevo_importe = redondear_moneda(linea.importe + delta)
        if nuevo_importe > CERO:
            ajustables.append((linea, nuevo_importe))
    if not ajustables:
        return None
    linea, nuevo_importe = max(ajustables, key=lambda par: par[0].importe)
    linea.importe = nuevo_importe
    return linea


def _origen_del_iva(movimiento: dict, tasa_iva) -> str:
    if not movimiento.get("tiene_iva", False):
        return "Sin IVA (movimiento no afectable a impuestos)."
    if movimiento.get("iva_exacto") is not None:
        return "IVA y retenciones exactos tomados de los CFDI confirmados."
    return f"IVA ESTIMADO con la tasa de la empresa ({tasa_iva}); no hay CFDI confirmado."


def generar_poliza(
    movimiento: dict,
    plantilla: list[LineaPlantilla],
    nombre_regla: str,
    motivo_match: str,
    tasa_iva=Decimal("0.16"),
    tasa_ret_iva=Decimal("0.0"),
    tasa_ret_isr=Decimal("0.0"),
    cuentas_banco_empresa=frozenset(),
) -> ResultadoPoliza:
    """
    Genera las líneas de póliza para un movimiento aplicando la plantilla.

    :param movimiento: dict con 'total', 'descripcion', 'tipo', 'tiene_iva',
        'cuenta_banco' y, si hay CFDI confirmados, 'iva_exacto', 'ret_iva', 'ret_isr'
    :param cuentas_banco_empresa: cuentas contables de todos los bancos de la
        empresa; sirve para detectar reglas ligadas a un banco distinto al del movimiento
    """
    resultado = ResultadoPoliza()
    resultado.explicacion.append(f"Regla aplicada: '{nombre_regla}'. {motivo_match}")

    try:
        total = redondear_moneda(movimiento["total"])
        if total <= CERO:
            raise FormulaError("El total del movimiento debe ser mayor a cero.")
        variables = construir_variables(
            total=total,
            tasa_iva=tasa_iva,
            tiene_iva=movimiento.get("tiene_iva", False),
            ret_iva=movimiento.get("ret_iva", Decimal("0.00")),
            ret_isr=movimiento.get("ret_isr", Decimal("0.00")),
            tipo_cambio=movimiento.get("tipo_cambio", Decimal("1.0")),
            tasa_ret_iva=tasa_ret_iva,
            tasa_ret_isr=tasa_ret_isr,
            iva_exacto=movimiento.get("iva_exacto"),
            ieps=movimiento.get("ieps", Decimal("0.00")),
        )
    except FormulaError as e:
        resultado.errores.append(str(e))
        return resultado

    if not plantilla:
        resultado.errores.append("La regla no tiene líneas en su plantilla contable.")
        return resultado

    resultado.explicacion.append(_origen_del_iva(movimiento, tasa_iva))
    resultado.explicacion.extend(movimiento.get("notas", []))
    resultado.explicacion.append(
        "Variables calculadas del movimiento: " + ", ".join(f"{k}={v}" for k, v in variables.items())
    )

    tipo = movimiento.get("tipo")
    cuenta_banco_movimiento = _normalizar_cuenta(movimiento.get("cuenta_banco"))
    cuentas_otros_bancos = {_normalizar_cuenta(c) for c in cuentas_banco_empresa} - {cuenta_banco_movimiento, ""}

    for linea_plantilla in plantilla:
        naturaleza = str(linea_plantilla.naturaleza).strip().lower()
        cuenta_plantilla = _normalizar_cuenta(linea_plantilla.cuenta)

        if naturaleza not in ("cargo", "abono"):
            resultado.errores.append(
                f"La línea de la cuenta {linea_plantilla.cuenta} tiene una naturaleza inválida: '{linea_plantilla.naturaleza}'."
            )
            continue

        try:
            importe = evaluar_formula(linea_plantilla.formula, variables)
        except FormulaError as e:
            resultado.errores.append(f"Error en la línea de cuenta {linea_plantilla.cuenta}: {e}")
            continue

        if importe < CERO:
            resultado.errores.append(
                f"La fórmula '{linea_plantilla.formula}' para la cuenta {linea_plantilla.cuenta} "
                f"dio un importe negativo ({importe}). Revisa la fórmula."
            )
            continue

        if importe == CERO:
            resultado.explicacion.append(
                f"  Línea omitida: cuenta {linea_plantilla.cuenta} = {linea_plantilla.formula} -> $0.00"
            )
            continue

        if cuenta_plantilla == TOKEN_BANCO:
            if not cuenta_banco_movimiento:
                resultado.errores.append(
                    "El movimiento no tiene cuenta contable de banco: el documento importado no "
                    "tiene banco asignado o el banco no tiene cuenta contable."
                )
                continue
            cuenta = movimiento["cuenta_banco"]
            es_banco = True
        else:
            cuenta = linea_plantilla.cuenta
            es_banco = cuenta_plantilla == cuenta_banco_movimiento

        if cuenta_plantilla in cuentas_otros_bancos:
            resultado.errores.append(
                f"La regla usa la cuenta {linea_plantilla.cuenta}, que es de otro banco distinto al del movimiento."
            )
            continue

        if es_banco and tipo in TIPOS_MOVIMIENTO:
            lado_esperado = "cargo" if tipo == "ingreso" else "abono"
            if naturaleza != lado_esperado:
                resultado.errores.append(
                    f"La regla pone el banco como {naturaleza}, pero un {tipo} debe ir como {lado_esperado}. "
                    f"La regla no corresponde a este movimiento."
                )
                continue

        resultado.lineas.append(LineaPoliza(
            cuenta=cuenta,
            naturaleza=naturaleza,
            importe=importe,
            descripcion=linea_plantilla.descripcion_linea or movimiento.get("descripcion", ""),
            formula_usada=linea_plantilla.formula,
            es_banco=es_banco,
        ))
        resultado.explicacion.append(
            f"  {naturaleza.upper():6s}  cuenta {str(cuenta):12s} = {linea_plantilla.formula}  ->  ${importe:,.2f}"
        )

    if not resultado.errores:
        naturalezas = {l.naturaleza for l in resultado.lineas}
        if not resultado.lineas:
            resultado.errores.append("Ninguna línea de la plantilla produjo un importe mayor a cero.")
        elif naturalezas != {"cargo", "abono"}:
            resultado.errores.append("La póliza necesita al menos un cargo y un abono.")

    cargos, abonos = totales_por_naturaleza(resultado.lineas)
    diferencia = redondear_moneda(cargos - abonos)

    if not resultado.errores and CERO < abs(diferencia) <= TOLERANCIA_REDONDEO:
        linea_ajustada = _absorber_diferencia(resultado.lineas, diferencia)
        if linea_ajustada is not None:
            resultado.explicacion.append(
                f"  Ajuste de redondeo de ${abs(diferencia):,.2f} aplicado en la cuenta "
                f"{linea_ajustada.cuenta} -> ${linea_ajustada.importe:,.2f}"
            )
            cargos, abonos = totales_por_naturaleza(resultado.lineas)
            diferencia = redondear_moneda(cargos - abonos)

    resultado.diferencia = diferencia
    resultado.cuadrada = not resultado.errores and bool(resultado.lineas) and diferencia == CERO

    if resultado.cuadrada:
        resultado.explicacion.append(f"Póliza cuadrada. Cargos: ${cargos:,.2f}  Abonos: ${abonos:,.2f}")
    elif not resultado.errores:
        resultado.explicacion.append(
            f"Póliza NO cuadrada. Cargos: ${cargos:,.2f}  Abonos: ${abonos:,.2f}  Diferencia: ${diferencia:,.2f}"
        )

    return resultado
