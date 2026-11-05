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
Generador de pólizas para Orange Poliza Engine (con aritmética exacta Decimal).

Toma:
  - un movimiento normalizado (fecha, descripción, total, tipo, etc.)
  - una PLANTILLA de movimientos contables (lista de líneas: cuenta,
    cargo/abono, fórmula) que viene de la regla que hizo match
      (ver rule_engine.encontrar_regla)

y genera:
  - las líneas de la póliza (cargos y abonos ya calculados con Decimal)
  - una validación de que cuadra (suma cargos == suma abonos)
  - una explicación paso a paso para el visor de auditoría
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

@dataclass
class LineaPlantilla:
    """Una línea de la plantilla contable de una regla, tal como se
    guarda en la tabla `plantilla_movimientos`."""
    cuenta: str
    naturaleza: str  # 'cargo' | 'abono'
    formula: str      # ej. "TOTAL / 1.16", "IVA", "TOTAL"
    descripcion_linea: Optional[str] = None  # si es None, se usa la descripción del movimiento

@dataclass
class LineaPoliza:
    """Una línea ya calculada, lista para exportar."""
    cuenta: str
    naturaleza: str
    importe: Decimal
    descripcion: str
    formula_usada: str

@dataclass
class ResultadoPoliza:
    lineas: list = field(default_factory=list)
    cuadrada: bool = False
    diferencia: Decimal = field(default_factory=lambda: MontoDecimal("0.00"))
    explicacion: list = field(default_factory=list)  # lista de strings, paso a paso
    errores: list = field(default_factory=list)

def generar_poliza(
    movimiento: dict,
    plantilla: list[LineaPlantilla],
    nombre_regla: str,
    motivo_match: str,
    tasa_iva=Decimal("0.16"),
    tasa_ret_iva=Decimal("0.0"),
    tasa_ret_isr=Decimal("0.0"),
) -> ResultadoPoliza:
    """
    Genera las líneas de póliza para un movimiento, aplicando la
    plantilla de la regla que hizo match.

    :param movimiento: dict con al menos 'total', 'descripcion', 'tipo',
        'tiene_iva', 'ret_iva', 'ret_isr', 'tipo_cambio'
    :param plantilla: lista de LineaPlantilla en el orden en que deben
        aparecer en la póliza
    :param nombre_regla: nombre de la regla que se está aplicando (para
        la explicación)
    :param motivo_match: el `motivo` que regresó rule_engine.encontrar_regla
    """
    resultado = ResultadoPoliza()

    variables = construir_variables(
        total=movimiento["total"],
        tasa_iva=tasa_iva,
        tiene_iva=movimiento.get("tiene_iva", False),
        ret_iva=movimiento.get("ret_iva", Decimal("0.00")),
        ret_isr=movimiento.get("ret_isr", Decimal("0.00")),
        tipo_cambio=movimiento.get("tipo_cambio", Decimal("1.0")),
        tasa_ret_iva=tasa_ret_iva,
        tasa_ret_isr=tasa_ret_isr,
        iva_exacto=movimiento.get("iva_exacto"),
        ieps=movimiento.get("ieps", Decimal("0.00")),
        base_gravada=movimiento.get("base_gravada"),
        base_exenta=movimiento.get("base_exenta", Decimal("0.00")),
        base_0=movimiento.get("base_0", Decimal("0.00")),
        descuento=movimiento.get("descuento", Decimal("0.00")),
    )

    resultado.explicacion.append(
        f"Regla aplicada: '{nombre_regla}'. {motivo_match}"
    )
    resultado.explicacion.append(
        "Variables calculadas del movimiento: "
        + ", ".join(f"{k}={v}" for k, v in variables.items())
    )

    suma_cargos = MontoDecimal("0.00")
    suma_abonos = MontoDecimal("0.00")

    for linea_plantilla in plantilla:
        try:
            importe = evaluar_formula(linea_plantilla.formula, variables)
        except FormulaError as e:
            resultado.errores.append(
                f"Error en la línea de cuenta {linea_plantilla.cuenta}: {e}"
            )
            continue

        if importe < Decimal("0.00"):
            resultado.errores.append(
                f"La fórmula '{linea_plantilla.formula}' para la cuenta "
                f"{linea_plantilla.cuenta} dio un importe negativo ({importe}). "
                f"Revisa la fórmula."
            )
            continue

        if importe == Decimal("0.00"):
            # Omitir líneas con importe cero (ej. retenciones opcionales que no aplican a esta factura)
            continue

        descripcion_linea = (
            linea_plantilla.descripcion_linea or movimiento.get("descripcion", "")
        )

        cuenta_eval = linea_plantilla.cuenta
        if cuenta_eval == "BANCO" and movimiento.get("cuenta_banco"):
            cuenta_eval = movimiento["cuenta_banco"]

        linea = LineaPoliza(
            cuenta=cuenta_eval,
            naturaleza=linea_plantilla.naturaleza,
            importe=importe,
            descripcion=descripcion_linea,
            formula_usada=linea_plantilla.formula,
        )
        resultado.lineas.append(linea)

        if linea_plantilla.naturaleza == "cargo":
            suma_cargos = redondear_moneda(suma_cargos + importe)
        else:
            suma_abonos = redondear_moneda(suma_abonos + importe)

        resultado.explicacion.append(
            f"  {linea_plantilla.naturaleza.upper():6s}  cuenta {linea_plantilla.cuenta:12s} "
            f"= {linea_plantilla.formula}  ->  ${importe:,.2f}"
        )

    diferencia = redondear_moneda(suma_cargos - suma_abonos)

    # Absorción automática de 1 centavo cuando dos fórmulas con división
    # (ej. "TOTAL / 1.16" y "(TOTAL / 1.16) * 0.16") producen $0.01 de diferencia por redondeo
    if not resultado.errores and resultado.lineas and Decimal("0.00") < abs(diferencia) <= Decimal("0.01"):
        naturaleza_ajustar = "cargo" if diferencia < Decimal("0.00") else "abono"
        ajuste = abs(diferencia) if diferencia < Decimal("0.00") else -abs(diferencia)
        candidatas = [
            l for l in resultado.lineas
            if l.naturaleza == naturaleza_ajustar and l.formula_usada.strip().upper() != "TOTAL"
        ]
        if not candidatas:
            candidatas = [l for l in resultado.lineas if l.naturaleza == naturaleza_ajustar]
        if candidatas:
            linea_aj = candidatas[-1]
            nuevo_imp = redondear_moneda(linea_aj.importe + ajuste)
            if nuevo_imp >= Decimal("0.00"):
                linea_aj.importe = nuevo_imp
                suma_cargos = redondear_moneda(sum((l.importe for l in resultado.lineas if l.naturaleza == "cargo"), MontoDecimal("0.00")))
                suma_abonos = redondear_moneda(sum((l.importe for l in resultado.lineas if l.naturaleza == "abono"), MontoDecimal("0.00")))
                diferencia = redondear_moneda(suma_cargos - suma_abonos)
                resultado.explicacion.append(
                    f"  Ajuste de redondeo ($0.01) aplicado en cuenta {linea_aj.cuenta} -> ${linea_aj.importe:,.2f}"
                )

    resultado.diferencia = diferencia
    resultado.cuadrada = (abs(diferencia) < Decimal("0.01")) and not resultado.errores

    if resultado.cuadrada:
        resultado.explicacion.append(
            f"✓ Póliza cuadrada. Cargos: ${suma_cargos:,.2f}  "
            f"Abonos: ${suma_abonos:,.2f}"
        )
    else:
        if not resultado.errores:
            resultado.explicacion.append(
                f"✗ Póliza NO cuadrada. Cargos: ${suma_cargos:,.2f}  "
                f"Abonos: ${suma_abonos:,.2f}  Diferencia: ${diferencia:,.2f}"
            )

    return resultado
